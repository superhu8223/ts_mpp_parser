#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Telnet通信模块
负责telnet连接、命令执行、日志监控
"""

import socket
import select
try:
    import telnetlib  # Python <=3.12
except ImportError:  # Python 3.13+ 移除了telnetlib
    telnetlib = None
import threading
import queue
import time
from datetime import datetime
from typing import Optional, Callable, List
from .models import RuntimeEvent, EventLevel, LogMapConfig


class _SimpleTelnet:
    """轻量级Telnet兼容实现，用于Python 3.13+无telnetlib时。

    只实现TelnetHandler所需的最小接口：write、read_very_eager、close。
    """

    def __init__(self, host: str, port: int, timeout: float = 10.0):
        self.sock = socket.create_connection((host, port), timeout)
        self.sock.settimeout(0.1)
        self._closed = False

    def write(self, data: bytes):
        if self._closed:
            return
        self.sock.sendall(data)
    
    def flush(self):
        """刷新缓冲区（Socket 默认立即发送，此方法为兼容性保留）"""
        pass

    def read_very_eager(self) -> bytes:
        if self._closed:
            return b""

        chunks = []
        try:
            while True:
                ready, _, _ = select.select([self.sock], [], [], 0)
                if not ready:
                    break
                chunk = self.sock.recv(4096)
                if not chunk:
                    break
                chunks.append(chunk)
        except Exception:
            # 读失败视为无数据
            pass
        return b"".join(chunks)

    def close(self):
        if self._closed:
            return
        self._closed = True
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except Exception:
            pass
        try:
            self.sock.close()
        except Exception:
            pass


class TelnetHandler:
    """Telnet处理器"""
    
    def __init__(self, host: str, port: int, logmap_config: LogMapConfig, 
                 log_type: str = "launch"):
        """
        初始化Telnet处理器
        log_type: "launch" 或 "serial"，用于匹配logmap配置
        """
        self.host = host
        self.port = port
        self.logmap_config = logmap_config
        self.log_type = log_type
        self.telnet: Optional[telnetlib.Telnet] = None
        self.is_running = False
        self.read_thread: Optional[threading.Thread] = None
        self.log_queue = queue.Queue()
        self.event_callbacks: List[Callable[[RuntimeEvent], None]] = []
        self.log_callback: Optional[Callable[[str], None]] = None  # GUI日志回调
        self.log_file: Optional[str] = None
        self.log_file_handle = None
        
    def connect(self, timeout: float = 10.0, retry_times: int = 5) -> bool:
        """连接telnet"""
        for i in range(retry_times):
            try:
                if telnetlib:
                    self.telnet = telnetlib.Telnet(self.host, self.port, timeout=timeout)
                else:
                    self.telnet = _SimpleTelnet(self.host, self.port, timeout=timeout)
                print(f"Telnet已连接: {self.host}:{self.port}")
                return True
            except Exception as e:
                print(f"Telnet连接失败 (尝试 {i+1}/{retry_times}): {e}")
                if i < retry_times - 1:
                    time.sleep(3)
        
        # 连接失败，触发事件
        event = RuntimeEvent(
            line_log=f"telnet连接不上：{self.host}",
            event_level=EventLevel.ERROR,
            event_name="设备变砖",
            timestamp=datetime.now()
        )
        self._trigger_event(event)
        return False
    
    def disconnect(self):
        """断开telnet连接"""
        self.is_running = False
        if self.read_thread and self.read_thread.is_alive():
            self.read_thread.join(timeout=2)
        
        if self.telnet:
            try:
                self.telnet.close()
                print(f"Telnet已断开: {self.host}:{self.port}")
            except:
                pass
        
        if self.log_file_handle:
            self.log_file_handle.close()
            self.log_file_handle = None
    
    def start_monitoring(self, log_file: Optional[str] = None):
        """开始监控telnet日志"""
        self.log_file = log_file
        if log_file:
            self.log_file_handle = open(log_file, 'a', encoding='utf-8')
        
        self.is_running = True
        self.read_thread = threading.Thread(target=self._read_loop, daemon=True)
        self.read_thread.start()

    @staticmethod
    def _strip_device_timestamp(line: str) -> str:
        """移除设备端时间戳/前缀，保留核心日志。"""
        if not line:
            return ""
        import re
        patterns = [
            r'^\s*\[\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}\.\d+\]\s*',  # [1970-01-01 00:00:36.282486]
            r'^\s*\d{4}_\d{2}_\d{2}\s+\d{2}:\d{2}:\d{2}\.\d+\s*',          # 1970_01_01 00:00:36.282486
            r'^\s*\[\s*\d+\.\d+\]\s*'                                        # [    0.000000]
        ]
        cleaned = line
        for pat in patterns:
            cleaned = re.sub(pat, '', cleaned)
        return cleaned.strip()    
    def _strip_ansi_codes(self, text: str) -> str:
        """移除ANSI颜色码和其他控制序列"""
        import re
        # 匹配ANSI转义序列: \x1b[...m 或 ESC[...m
        ansi_escape = re.compile(r'\x1b\[[0-9;]*m|\033\[[0-9;]*m|\[[0-9;]*m')
        return ansi_escape.sub('', text)    
    def _read_loop(self):
        """读取循环"""
        while self.is_running and self.telnet:
            try:
                data = self.telnet.read_very_eager()
                if data:
                    text = data.decode('utf-8', errors='ignore')
                    lines = text.split('\n')
                    for line in lines:
                        line = line.strip()
                        if line:
                            self._process_line(line)
                else:
                    time.sleep(0.01)
            except Exception as e:
                if self.is_running:
                    print(f"Telnet读取错误: {e}")
                time.sleep(0.1)
    
    def _process_line(self, line: str):
        """处理一行日志"""
        timestamp = datetime.now()
        cleaned_line = self._strip_device_timestamp(line)
        if not cleaned_line:
            return
        
        # 移除ANSI颜色码
        cleaned_line = self._strip_ansi_codes(cleaned_line)
        
        # 根据log_type选择对应的logmap配置
        if self.log_type == "launch":
            ignore_patterns = self.logmap_config.launch_ignore
            emerge_patterns = self.logmap_config.launch_emerge
            error_patterns = self.logmap_config.launch_error
            warning_patterns = self.logmap_config.launch_warning
            info_patterns = self.logmap_config.launch_info
        else:
            ignore_patterns = self.logmap_config.serial_ignore
            emerge_patterns = self.logmap_config.serial_emerge
            error_patterns = self.logmap_config.serial_error
            warning_patterns = self.logmap_config.serial_warning
            info_patterns = self.logmap_config.serial_info

        # 先做忽略过滤，再做日志写入/事件处理（符合4.1.3.2要求）
        for pattern in ignore_patterns:
            if pattern.pattern.lower() in cleaned_line.lower():
                return

        timestamped_line = f"[{timestamp.strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]}] {cleaned_line}"
        
        # 写入日志文件
        if self.log_file_handle:
            self.log_file_handle.write(timestamped_line + '\n')
            self.log_file_handle.flush()
        
        # 调用GUI日志回调
        if self.log_callback:
            try:
                self.log_callback(cleaned_line)
            except Exception as e:
                print(f"GUI日志回调错误: {e}")
        
        # 检查各个级别
        for pattern in emerge_patterns:
            if pattern.pattern.lower() in cleaned_line.lower():
                event = RuntimeEvent(
                    line_log=cleaned_line,
                    event_level=EventLevel.FATAL,  # emerge映射为FATAL级别
                    event_name=pattern.event_name or pattern.pattern,
                    timestamp=timestamp
                )
                self._trigger_event(event)
                return
        
        for pattern in error_patterns:
            if pattern.pattern.lower() in cleaned_line.lower():
                event = RuntimeEvent(
                    line_log=cleaned_line,
                    event_level=EventLevel.ERROR,
                    event_name=pattern.event_name or pattern.pattern,
                    timestamp=timestamp
                )
                self._trigger_event(event)
                return
        
        for pattern in warning_patterns:
            if pattern.pattern.lower() in cleaned_line.lower():
                event = RuntimeEvent(
                    line_log=cleaned_line,
                    event_level=EventLevel.WARNING,
                    event_name=pattern.event_name or pattern.pattern,
                    timestamp=timestamp
                )
                self._trigger_event(event)
                return
        
        for pattern in info_patterns:
            if pattern.pattern.lower() in cleaned_line.lower():
                event = RuntimeEvent(
                    line_log=cleaned_line,
                    event_level=EventLevel.INFO,
                    event_name=pattern.event_name or pattern.pattern,
                    timestamp=timestamp
                )
                self._trigger_event(event)
                return
        
        # 将日志放入队列
        self.log_queue.put((timestamp, cleaned_line))
    
    def _trigger_event(self, event: RuntimeEvent):
        """触发事件回调"""
        for callback in self.event_callbacks:
            try:
                callback(event)
            except Exception as e:
                print(f"事件回调错误: {e}")
    
    def add_event_callback(self, callback: Callable[[RuntimeEvent], None]):
        """添加事件回调"""
        self.event_callbacks.append(callback)
    
    def set_log_callback(self, callback: Callable[[str], None]):
        """设置日志回调（用于GUI显示）"""
        self.log_callback = callback
    
    def send_command(self, command, wait_time: float = 0.5) -> bool:
        """发送命令（支持单条字符串或多条命令列表）"""
        if not self.telnet:
            print("Telnet未连接")
            return False
        
        try:
            # 支持列表或单条命令
            commands = command if isinstance(command, list) else [command]
            
            for cmd in commands:
                # 只清理首尾空格，保留引号（shell需要引号来保护特殊字符）
                cmd = cmd.strip()
                
                # 跳过空命令
                if not cmd:
                    continue
                
                if not cmd.endswith('\n'):
                    cmd += '\n'
                
                # 编码并发送
                cmd_bytes = cmd.encode('utf-8')
                self.telnet.write(cmd_bytes)
                
                # 刷新缓冲区，确保命令完整发送
                # 对于 _SimpleTelnet，直接调用 flush
                if hasattr(self.telnet, 'flush'):
                    try:
                        self.telnet.flush()
                    except:
                        pass
                
                time.sleep(wait_time)
            
            return True
        except Exception as e:
            print(f"发送命令失败: {e}")
            return False
    
    def execute_command(self, command: str, wait_time: float = 1.0) -> str:
        """执行命令并返回输出
        
        注意：如果监控线程正在运行，从日志队列中读取输出而不是直接读取telnet连接
        """
        if not self.telnet:
            print("[Telnet] 连接未建立，无法执行命令")
            return ""
        
        try:
            if not command.endswith('\n'):
                command += '\n'
            
            # 如果监控线程正在运行，从日志队列读取
            if self.is_running and self.read_thread and self.read_thread.is_alive():
                # 清空队列中的旧数据
                while not self.log_queue.empty():
                    try:
                        self.log_queue.get_nowait()
                    except queue.Empty:
                        break
                
                # 发送命令
                self.telnet.write(command.encode('utf-8'))
                
                # 从队列中收集输出
                output_lines = []
                start_time = time.time()
                command_echo_seen = False
                
                while time.time() - start_time < wait_time + 1.0:  # 额外1秒超时
                    try:
                        timestamp, line = self.log_queue.get(timeout=0.1)
                        
                        # 检测命令回显
                        if not command_echo_seen and command.strip() in line:
                            command_echo_seen = True
                            continue
                        
                        # 收集输出直到看到提示符
                        output_lines.append(line)
                        
                        # 如果看到shell提示符，停止收集
                        if line.strip().endswith('#') or line.strip().endswith('$'):
                            # 等待一小段时间确保没有更多输出
                            time.sleep(0.1)
                            break
                    except queue.Empty:
                        if command_echo_seen and len(output_lines) > 0:
                            # 已经有一些输出了，可以停止
                            break
                        continue
                
                result = '\n'.join(output_lines)
                
                # 调试信息
                if len(result) == 0:
                    print(f"[Telnet] 警告: 命令 '{command.strip()}' 从队列返回空输出")
                else:
                    print(f"[Telnet] 命令 '{command.strip()}' 返回 {len(result)} 字节，{len(output_lines)} 行")
                
                return result
            else:
                # 监控线程未运行，直接读取
                # 清空之前的缓冲区
                try:
                    self.telnet.read_very_eager()
                except:
                    pass
                
                self.telnet.write(command.encode('utf-8'))
                time.sleep(wait_time)
                
                # 尝试多次读取
                output = b""
                max_attempts = 3
                for attempt in range(max_attempts):
                    try:
                        chunk = self.telnet.read_very_eager()
                        if chunk:
                            output += chunk
                        if len(chunk) == 0 and len(output) > 0:
                            break
                        if attempt < max_attempts - 1:
                            time.sleep(0.1)
                    except EOFError:
                        print("[Telnet] 连接已关闭")
                        break
                    except Exception as read_err:
                        print(f"[Telnet] 读取数据失败: {read_err}")
                        break
                
                result = output.decode('utf-8', errors='ignore')
                
                if len(result) == 0:
                    print(f"[Telnet] 警告: 命令 '{command.strip()}' 返回空输出")
                
                return result
        except Exception as e:
            print(f"[Telnet] 执行命令失败: {e}")
            import traceback
            traceback.print_exc()
            return ""
    
    def wait_for_pattern(self, pattern: str, timeout: float = 10.0) -> bool:
        """等待特定模式出现"""
        start_time = time.time()
        
        while time.time() - start_time < timeout:
            try:
                timestamp, line = self.log_queue.get(timeout=0.1)
                if pattern.lower() in line.lower():
                    return True
            except queue.Empty:
                continue
        
        return False
    
    def send_ctrl_c(self):
        """发送Ctrl+C"""
        if self.telnet:
            try:
                self.telnet.write(b'\x03')  # Ctrl+C
                time.sleep(0.5)
            except Exception as e:
                print(f"发送Ctrl+C失败: {e}")
