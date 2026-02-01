#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
串口通信模块
负责串口连接、命令发送、日志接收和解析
"""

import os
import serial
import threading
import queue
import time
from datetime import datetime
from typing import Optional, Callable, List
from .models import SerialConfig, RuntimeEvent, EventLevel, LogMapConfig


class SerialHandler:
    """串口处理器"""
    
    def __init__(self, config: SerialConfig, logmap_config: LogMapConfig):
        self.config = config
        self.logmap_config = logmap_config
        self.serial_port: Optional[serial.Serial] = None
        self.is_running = False
        self.read_thread: Optional[threading.Thread] = None
        self.log_queue = queue.Queue()
        self.event_callbacks: List[Callable[[RuntimeEvent], None]] = []
        self.log_callback: Optional[Callable[[str], None]] = None  # GUI日志回调
        self.log_file: Optional[str] = None
        self.log_file_handle = None
        
    def connect(self) -> bool:
        """连接串口"""
        try:
            # 设置校验位
            parity_map = {
                'none': serial.PARITY_NONE,
                'even': serial.PARITY_EVEN,
                'odd': serial.PARITY_ODD,
                'mark': serial.PARITY_MARK,
                'space': serial.PARITY_SPACE
            }
            parity = parity_map.get(self.config.parity.lower(), serial.PARITY_NONE)
            
            # 设置停止位
            stopbits_map = {
                1: serial.STOPBITS_ONE,
                1.5: serial.STOPBITS_ONE_POINT_FIVE,
                2: serial.STOPBITS_TWO
            }
            stopbits = stopbits_map.get(self.config.stopbits, serial.STOPBITS_ONE)
            
            self.serial_port = serial.Serial(
                port=self.config.port,
                baudrate=self.config.baudrate,
                bytesize=self.config.databits,
                parity=parity,
                stopbits=stopbits,
                timeout=0.1
            )
            
            print(f"串口已连接: {self.config.port}")
            return True
        except Exception as e:
            print(f"串口连接失败: {e}")
            return False

    def start_monitoring(self, log_file: Optional[str] = None):
        """开始监控串口日志"""
        if self.is_running:
            print("串口监控已经运行中")
            return

        self.set_log_file(log_file)

        self.is_running = True
        self.read_thread = threading.Thread(target=self._read_loop, daemon=True)
        self.read_thread.start()

    def set_log_file(self, log_file: Optional[str], clear_existing: bool = False):
        """切换串口日志文件
        
        Args:
            log_file: 日志文件路径
            clear_existing: 是否清空已有的日志文件内容（新case时设置为True）
        """
        # 先关闭当前文件句柄
        if self.log_file_handle:
            try:
                self.log_file_handle.flush()
                self.log_file_handle.close()
            except Exception as e:
                print(f"关闭旧串口日志文件失败: {e}")
            finally:
                self.log_file_handle = None

        self.log_file = log_file

        if log_file:
            try:
                os.makedirs(os.path.dirname(log_file), exist_ok=True)
                # 根据clear_existing参数选择打开模式
                mode = 'w' if clear_existing else 'a'
                self.log_file_handle = open(log_file, mode, encoding='utf-8')
                print(f"[串口日志] 日志文件已设置: {log_file} (mode={mode})")
            except Exception as e:
                print(f"打开串口日志文件失败: {e}")
    
    def _strip_ansi_codes(self, text: str) -> str:
        """移除ANSI颜色码和其他控制序列"""
        import re
        # 匹配ANSI转义序列: \x1b[...m 或 ESC[...m
        ansi_escape = re.compile(r'\x1b\[[0-9;]*m|\033\[[0-9;]*m|\[[0-9;]*m')
        return ansi_escape.sub('', text)

    def _read_loop(self):
        """读取循环"""
        buffer = ""

        while self.is_running:
            try:
                if self.serial_port and self.serial_port.in_waiting:
                    data = self.serial_port.read(self.serial_port.in_waiting)
                    text = data.decode('utf-8', errors='ignore')
                    buffer += text

                    # 处理完整的行
                    while '\n' in buffer:
                        line, buffer = buffer.split('\n', 1)
                        line = line.strip()
                        if line:
                            self._process_line(line)
                else:
                    time.sleep(0.01)
            except Exception as e:
                print(f"串口读取错误: {e}")
                time.sleep(0.1)
    
    def get_current_mode(self) -> str:
        """获取当前模式（uboot或linux，如果在linux则检查是否需要登录）"""
        # 清空队列
        while not self.log_queue.empty():
            try:
                self.log_queue.get_nowait()
            except queue.Empty:
                break

        # 发送回车获取提示符，等待响应
        self.send_command("\r\n", wait_time=0.2)

        def collect_lines(duration: float) -> str:
            collected_local = ""
            start = time.time()
            while time.time() - start < duration:
                try:
                    timestamp, line = self.log_queue.get(timeout=0.1)
                    collected_local += line + "\n"
                except queue.Empty:
                    continue
            return collected_local

        # 收集更多响应确保获得完整提示符
        collected = collect_lines(2)

        def has_prompt(text: str) -> str:
            if "=>" in text:
                return "uboot"
            if "buildroot login:" in text:
                return "linux_login"
            # 检查Linux shell提示符（root用户为 # ）
            lines = text.strip().split('\n')
            if lines:
                last_line = lines[-1].strip()
                if last_line.endswith('#') or last_line == '#':
                    return "linux"
            return ""

        # 优先检查常规提示符
        mode = has_prompt(collected)
        if mode:
            return mode

        # 检查是否在uboot特殊模式（只有 ">" 提示符）
        lines = collected.strip().split('\n')
        last_line = lines[-1].strip() if lines else ""
        if last_line == ">":
            print("检测到uboot特殊模式（>），发送Ctrl+C退出...")
            if self.serial_port and self.serial_port.is_open:
                try:
                    self.serial_port.write(b'\x03')  # Ctrl+C
                except Exception as e:
                    print(f"发送Ctrl+C失败: {e}")
            time.sleep(0.5)

            # 清空队列并重新获取提示符
            while not self.log_queue.empty():
                try:
                    self.log_queue.get_nowait()
                except queue.Empty:
                    break

            self.send_command("\r\n", wait_time=0.2)
            collected = collect_lines(1.5)
            mode = has_prompt(collected)
            if mode:
                return mode

        # 仍未检测到明确提示符，返回unknown（可能系统正在启动中）
        return "unknown"
    
    def _process_line(self, line: str):
        """处理一行日志"""
        timestamp = datetime.now()
        
        # 移除ANSI颜色码
        cleaned_line = self._strip_ansi_codes(line)
        
        timestamped_line = f"[{timestamp.strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]}] {cleaned_line}"
        
        # 写入日志文件（优先级最高，确保每一行都被保存）
        if self.log_file_handle:
            try:
                self.log_file_handle.write(timestamped_line + '\n')
                self.log_file_handle.flush()
            except Exception as e:
                print(f"写入串口日志文件失败: {e}")
        else:
            print(f"[WARNING] 日志文件未打开，无法保存日志行: {timestamped_line}")
        
        # 调用GUI日志回调（所有日志都显示）
        if self.log_callback:
            try:
                self.log_callback(cleaned_line)
            except Exception as e:
                print(f"GUI日志回调错误: {e}")
        
        # 检查是否需要忽略事件处理（但仍然显示在GUI）
        for pattern in self.logmap_config.serial_ignore:
            if pattern.pattern.lower() in cleaned_line.lower():
                return  # 忽略事件处理，但已经显示在GUI了
        
        # 检查是否匹配emerge级别（映射为FATAL事件）
        for pattern in self.logmap_config.serial_emerge:
            if pattern.pattern.lower() in cleaned_line.lower():
                print(f"[串口事件] 匹配到emerge模式: '{pattern.pattern}' -> FATAL级别")
                event = RuntimeEvent(
                    line_log=cleaned_line,
                    event_level=EventLevel.FATAL,  # emerge映射为FATAL级别
                    event_name=pattern.event_name or pattern.pattern,
                    timestamp=timestamp
                )
                self._trigger_event(event)
                return
        
        # 检查是否匹配error级别
        for pattern in self.logmap_config.serial_error:
            if pattern.pattern.lower() in cleaned_line.lower():
                event = RuntimeEvent(
                    line_log=cleaned_line,
                    event_level=EventLevel.ERROR,
                    event_name=pattern.event_name or pattern.pattern,
                    timestamp=timestamp
                )
                self._trigger_event(event)
                return
        
        # 检查是否匹配warning级别
        for pattern in self.logmap_config.serial_warning:
            if pattern.pattern.lower() in cleaned_line.lower():
                event = RuntimeEvent(
                    line_log=cleaned_line,
                    event_level=EventLevel.WARNING,
                    event_name=pattern.event_name or pattern.pattern,
                    timestamp=timestamp
                )
                self._trigger_event(event)
                return
        
        # 检查是否匹配info级别
        for pattern in self.logmap_config.serial_info:
            if pattern.pattern.lower() in cleaned_line.lower():
                event = RuntimeEvent(
                    line_log=cleaned_line,
                    event_level=EventLevel.INFO,
                    event_name=pattern.event_name or pattern.pattern,
                    timestamp=timestamp
                )
                self._trigger_event(event)
                return
        
        # 将日志放入队列供其他组件使用
        self.log_queue.put((timestamp, line))
    
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

    def disconnect(self):
        """停止监控并关闭串口"""
        self.is_running = False

        if self.read_thread and self.read_thread.is_alive():
            self.read_thread.join(timeout=1.0)

        # 关闭日志文件
        if self.log_file_handle:
            try:
                self.log_file_handle.flush()
                self.log_file_handle.close()
            except Exception as e:
                print(f"关闭串口日志文件失败: {e}")
            finally:
                self.log_file_handle = None

        # 关闭串口
        if self.serial_port:
            try:
                self.serial_port.close()
            except Exception as e:
                print(f"关闭串口失败: {e}")
            finally:
                self.serial_port = None
    
    def send_command(self, command, wait_time: float = 0.2):
        """发送命令（支持单条字符串或多条命令列表）"""
        if not self.serial_port or not self.serial_port.is_open:
            print("串口未连接")
            return False
        
        try:
            # 支持列表或单条命令
            commands = command if isinstance(command, list) else [command]
            
            for cmd in commands:
                # 清理首尾引号和空格
                cmd = cmd.strip()
                if cmd.startswith('"') and cmd.endswith('"'):
                    cmd = cmd[1:-1]
                elif cmd.startswith("'") and cmd.endswith("'"):
                    cmd = cmd[1:-1]
                
                # 添加回车换行
                if not cmd.endswith('\n') and not cmd.endswith('\r\n'):
                    cmd += '\r\n'
                
                self.serial_port.write(cmd.encode('utf-8'))
                self.serial_port.flush()
                time.sleep(wait_time)
            
            return True
        except Exception as e:
            print(f"发送命令失败: {e}")
            return False
    
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
    
    def enter_uboot_mode(self) -> bool:
        """进入uboot命令行环境"""
        print("尝试进入uboot模式...")
        
        # 检查当前模式
        current_mode = self.get_current_mode()
        
        if current_mode == "uboot":
            print("已经在uboot模式，可以直接烧录")
            return True
        
        if current_mode == "linux_login":
            print("检测到Linux登录提示符，需要先登录再重启...")
            self.send_command("root\r\n")
            time.sleep(2)
        
        print("当前在Linux模式，尝试重启进入uboot...")
        
        # 从Linux重启
        self.send_command("reboot\r\n")
        
        # 等待"Autoboot"出现
        if not self.wait_for_pattern("Autoboot", timeout=60):
            print("未检测到Autoboot提示")
            return False

        # 如果此时已经出现"=>"提示符，直接返回
        if self.wait_for_pattern("=>", timeout=0.5):
            print("已在uboot提示符下，无需发送'b'")
            return True

        print("检测到Autoboot提示，立即发送'b'进入uboot（最多8次）...")
        for i in range(8):
            self.send_command("b", wait_time=0.05)
            time.sleep(0.05)
            if self.wait_for_pattern("=>", timeout=0.3):
                print("成功进入uboot模式")
                return True

        # 最后再检查一次提示符
        if self.wait_for_pattern("=>", timeout=2):
            print("成功进入uboot模式")
            return True

        print("未检测到uboot提示符 '=>'")
        return False
    
    def enter_linux_mode(self) -> bool:
        """进入Linux环境"""
        print("尝试进入Linux环境...")
        
        # 检查当前模式
        current_mode = self.get_current_mode()
        
        # 已在Linux或登录提示符，立即处理
        if current_mode == "linux":
            print("已经在Linux模式（已登录）")
            return True
        if current_mode == "linux_login":
            print("检测到buildroot login提示符，立即登录root...")
            self.send_command("root\r\n")
            time.sleep(2)  # 等待登录完成
            # 确认登录成功
            final_mode = self.get_current_mode()
            if final_mode == "linux":
                print("成功登录Linux")
                return True
            else:
                print(f"登录后仍处于 {final_mode} 模式，继续处理...")
                # 如果还没进入linux，继续走下面的轮询逻辑
        
        print("当前在uboot模式，执行boot进入Linux...")
        self.send_command("boot\r\n")
        
        # 快速轮询等待启动并立即登录
        start_wait = time.time()
        timeout = 60  # 最长等待60秒，但会尽快登录
        while time.time() - start_wait < timeout:
            mode = self.get_current_mode()
            if mode == "linux":
                print("已进入Linux模式（已登录）")
                return True
            elif mode == "linux_login":
                print("检测到buildroot login提示符，发送root登录...")
                self.send_command("root\r\n")
                time.sleep(2)  # 等待登录完成
                # 再次确认是否登录成功
                final_mode = self.get_current_mode()
                if final_mode == "linux":
                    print("成功登录Linux")
                    return True
                else:
                    print(f"登录后检测到模式: {final_mode}，继续等待...")
            elif mode == "unknown":
                print("系统可能正在启动中，继续等待...")
            time.sleep(1.5)  # 增加等待间隔，减少轮询频率
        
        print("等待Linux启动超时")
        return False
    
    def configure_network(self, ip_address: str) -> bool:
        """配置网络"""
        print(f"配置网络IP: {ip_address}")
        
        self.send_command(f"ifconfig eth0 {ip_address}\r\n")
        time.sleep(1)
        
        # 启动telnetd
        self.send_command("telnetd\r\n")
        time.sleep(1)
        
        return True
