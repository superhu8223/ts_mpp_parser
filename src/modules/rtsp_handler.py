#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
RTSP拉流模块
负责RTSP收流、帧率码率统计、关键帧截图
"""

import threading
import time
import cv2
import numpy as np
from datetime import datetime
from typing import Optional, Callable, List
from .models import RuntimeEvent, EventLevel, StreamStats, StatData

try:
    import av
    PYAV_AVAILABLE = True
except ImportError:
    PYAV_AVAILABLE = False
    print("警告: 未安装PyAV库，无法通过NAL type判断关键帧。请运行: pip install av")


class RTSPHandler:
    """RTSP处理器"""
    
    def __init__(self, stream_id: int, url: str, simplified_mode: bool = False, use_udp: bool = False):
        self.stream_id = stream_id
        self.url = url
        self.stats = StreamStats(stream_id=stream_id, url=url)
        self.use_udp = use_udp  # 是否使用UDP协议
        self.is_running = False
        self.stop_requested = False  # 标记是否由stop()触发停止
        self.thread: Optional[threading.Thread] = None
        self.capture: Optional[cv2.VideoCapture] = None
        self.av_container = None  # PyAV容器
        self.event_callbacks: List[Callable[[RuntimeEvent], None]] = []
        self.frame_count = 0
        self.keyframe_count = 0  # 关键帧计数
        self.total_bytes = 0
        self.last_stats_time = None
        self.keyframe_images_saved = 0
        self.save_dir: Optional[str] = None
        self.frame_callback: Optional[Callable[[int, any, float, float], None]] = None  # GUI视频帧回调(stream_id, frame, fps, bitrate)
        self.video_window_callback: Optional[Callable[[int], None]] = None  # 视频窗口回调，在首帧时创建独立窗口
        self.gop_size = 30  # 假设GOP间隔为30帧（约15fps下约2秒一个I帧）
        self.backend = None  # 用于存储VideoCapture的backend信息
        self.simplified_mode = simplified_mode  # 简化模式（bNeedCtrlC模式），只检测是否收到数据，不解码显示
        self.stream_disconnected_reported = False  # 断流是否已报告过
        self.last_callback_time = None  # 上次调用frame_callback的时间，用于限制更新频率
    
    def set_transport(self, use_udp: bool):
        """动态设置传输协议（UDP/TCP）"""
        self.use_udp = use_udp
        transport = 'udp' if use_udp else 'tcp'
        print(f"[RTSP{self.stream_id}] 传输协议已设置为: {transport.upper()}")
    
    def is_h264_keyframe(self, frame_data: bytes) -> bool:
        """
        通过解析H.264 NAL单元判断是否为关键帧
        NAL type = 5 表示IDR帧（关键帧）
        """
        try:
            # 查找NAL起始码 0x00 0x00 0x00 0x01 或 0x00 0x00 0x01
            i = 0
            while i < len(frame_data) - 4:
                if frame_data[i:i+4] == b'\x00\x00\x00\x01':
                    nal_unit_type = frame_data[i+4] & 0x1F
                    # NAL type 5 = IDR (关键帧)
                    # NAL type 7 = SPS, 8 = PPS (通常伴随IDR)
                    if nal_unit_type == 5:
                        return True
                    i += 4
                elif frame_data[i:i+3] == b'\x00\x00\x01':
                    nal_unit_type = frame_data[i+3] & 0x1F
                    if nal_unit_type == 5:
                        return True
                    i += 3
                else:
                    i += 1
            return False
        except Exception as e:
            print(f"[RTSP{self.stream_id}] H.264 NAL解析失败: {e}")
            return False
    
    def is_keyframe(self, frame_number: int, pict_type: str = None) -> bool:
        """
        检测是否为关键帧（I帧）
        方法：优先使用PyAV的pict_type判断，fallback到GOP间隔估算
        pict_type: 'I' = 关键帧, 'P' = 预测帧, 'B' = 双向预测帧
        """
        # 第一帧总是I帧
        if frame_number == 1:
            return True
        
        # 如果有pict_type信息（来自PyAV），直接使用
        if pict_type:
            return pict_type == 'I'
        
        # Fallback: 每GOP周期的第一帧是I帧
        return (frame_number - 1) % self.gop_size == 0
        
    def start(self, save_dir: Optional[str] = None):
        """开始拉流"""
        print(f"[RTSP{self.stream_id}] 启动拉流线程，URL: {self.url}")
        self.save_dir = save_dir
        self.is_running = True
        self.thread = threading.Thread(target=self._stream_loop, daemon=True)
        self.thread.start()
        print(f"[RTSP{self.stream_id}] 拉流线程已启动")
    
    def stop(self):
        """停止拉流"""
        print(f"[RTSP{self.stream_id}] 停止信号已发送，is_running=False")
        self.stop_requested = True
        self.is_running = False
        
        # 第一步：立即释放资源，强制中断可能的阻塞IO
        print(f"[RTSP{self.stream_id}] 开始释放资源...")
        
        # 禁用FFmpeg的线程检查，避免断言失败
        import os
        os.environ['FFREPORT'] = 'level=quiet'
        
        # 不在stop中直接release，避免与拉流线程并发导致FFmpeg断言
        # 交由拉流线程退出时统一释放
        
        try:
            if self.av_container:
                print(f"[RTSP{self.stream_id}] 关闭AV容器...")
                self.av_container.close()
                print(f"[RTSP{self.stream_id}] AV容器已关闭")
                self.av_container = None
        except Exception as e:
            print(f"[RTSP{self.stream_id}] 关闭AV容器失败（忽略）: {type(e).__name__}")
        
        # 第二步：等待读取线程退出
        thread = self.thread
        if thread and thread.is_alive():
            print(f"[RTSP{self.stream_id}] 等待拉流线程退出（最多3秒）...")
            start_wait_time = time.time()
            # 等待采集线程完全退出，最多等3秒避免卡死
            thread.join(timeout=3.0)
            wait_time = time.time() - start_wait_time
            if thread.is_alive():
                # 线程仍未退出，但我们已经释放了资源，继续进行
                print(f"[RTSP{self.stream_id}] 警告：拉流线程{wait_time:.1f}秒后仍未退出，强制停止")
            else:
                print(f"[RTSP{self.stream_id}] 拉流线程已正常退出（耗时{wait_time:.1f}秒）")
        
        self.thread = None
        print(f"[RTSP{self.stream_id}] 停止处理完成")
    
    def _stream_loop(self):
        """拉流循环"""
        # 在启动时就设置环境变量（对所有模式生效）
        transport = 'udp' if self.use_udp else 'tcp'
        import os
        os.environ['OPENCV_FFMPEG_CAPTURE_OPTIONS'] = f'rtsp_transport;{transport}'
        # 禁用FFmpeg的线程检查，避免Assertion错误（这是已知的FFmpeg多线程问题）
        os.environ['FFREPORT'] = 'level=quiet'
        os.environ['FFMPEG_THREAD_TYPE'] = 'slice'  # 使用更稳定的线程模式
        print(f"[RTSP{self.stream_id}] 设置全局环境变量: OPENCV_FFMPEG_CAPTURE_OPTIONS=rtsp_transport;{transport}")
        print(f"[RTSP{self.stream_id}] 禁用FFmpeg线程检查")
        
        # 如果是简化模式，使用简化的收流逻辑
        if self.simplified_mode:
            print(f"[RTSP{self.stream_id}] 使用简化模式（OpenCV）")
            self._stream_loop_simplified()
        # 优先使用PyAV获取帧类型，fallback到OpenCV
        elif PYAV_AVAILABLE:
            print(f"[RTSP{self.stream_id}] 使用PyAV模式（优先）")
            self._stream_loop_pyav()
        else:
            print(f"[RTSP{self.stream_id}] 使用OpenCV模式（fallback）")
            self._stream_loop_opencv()
    
    def _stream_loop_simplified(self):
        """简化模式的拉流（bNeedCtrlC模式）：只检测是否收到数据，不解码显示，不统计帧率码率"""
        transport = 'udp' if self.use_udp else 'tcp'
        print(f"[RTSP{self.stream_id}] 启动简化模式收流，URL: {self.url}, 传输协议: {transport.upper()}")
        
        # 设置环境变量控制FFmpeg的RTSP传输协议
        import os
        os.environ['OPENCV_FFMPEG_CAPTURE_OPTIONS'] = f'rtsp_transport;{transport}'
        print(f"[RTSP{self.stream_id}] 设置环境变量: OPENCV_FFMPEG_CAPTURE_OPTIONS=rtsp_transport;{transport}")
        
        while self.is_running:
            try:
                print(f"[RTSP{self.stream_id}] 正在连接到: {self.url} (简化模式, 协议: {transport.upper()})")
                
                # 尝试使用params参数（OpenCV 4.5+）
                try:
                    params = [
                        cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 5000,
                        cv2.CAP_PROP_READ_TIMEOUT_MSEC, 5000
                    ]
                    self.capture = cv2.VideoCapture(self.url, cv2.CAP_FFMPEG, params)
                    print(f"[RTSP{self.stream_id}] 使用params参数创建 VideoCapture")
                except Exception as e:
                    print(f"[RTSP{self.stream_id}] params参数方式失败: {e}，使用默认方式")
                    self.capture = cv2.VideoCapture(self.url, cv2.CAP_FFMPEG)
                    if hasattr(cv2, 'CAP_PROP_OPEN_TIMEOUT_MSEC'):
                        self.capture.set(cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 5000)
                
                if not self.capture.isOpened():
                    print(f"[RTSP{self.stream_id}] 无法打开RTSP流: {self.url}，2秒后重试...")
                    
                    # 只报一次断流错误
                    if not self.stream_disconnected_reported:
                        event = RuntimeEvent(
                            line_log=f"rtsp断流：{self.url}",
                            event_level=EventLevel.ERROR,
                            event_name="rtsp断流",
                            timestamp=datetime.now()
                        )
                        self._trigger_event(event)
                        self.stream_disconnected_reported = True
                    
                    time.sleep(2)
                    continue
                
                print(f"[RTSP{self.stream_id}] RTSP流已连接: {self.url}")
                
                # 收到第一帧即认为成功
                first_frame = True
                
                while self.is_running:
                    if not self.capture:
                        print(f"[RTSP{self.stream_id}] 检测到capture已被释放，退出读取循环")
                        break
                    
                    ret, _ = self.capture.read()
                    
                    if not ret:
                        print(f"[RTSP{self.stream_id}] 流断流: {self.url}，重新尝试连接...")
                        
                        # 只报一次断流错误
                        if not self.stream_disconnected_reported:
                            event = RuntimeEvent(
                                line_log=f"rtsp断流：{self.url}",
                                event_level=EventLevel.ERROR,
                                event_name="rtsp断流",
                                timestamp=datetime.now()
                            )
                            self._trigger_event(event)
                            self.stream_disconnected_reported = True
                        
                        break  # 退出内层循环，进行重连
                    
                    # 收到第一帧
                    if first_frame:
                        first_frame = False
                        self.stream_disconnected_reported = False  # 重置断流标志
                        self.stats.first_frame_time = datetime.now()
                        print(f"[Stream{self.stream_id}] (简化模式) 收到第一帧，URL: {self.url}")
                        
                        event = RuntimeEvent(
                            line_log=f"收到第一帧：{self.url}",
                            event_level=EventLevel.INFO,
                            event_name="收到第一帧",
                            timestamp=datetime.now()
                        )
                        self._trigger_event(event)
                    
                    # 简化模式：收到数据就认为成功，不做解码、显示、统计
                    # 适当休眠降低CPU占用
                    time.sleep(0.1)
                
            except Exception as e:
                print(f"[RTSP{self.stream_id}] 简化模式拉流错误: {e}")
                
                # 只报一次断流错误
                if not self.stream_disconnected_reported:
                    event = RuntimeEvent(
                        line_log=f"rtsp断流：{self.url}",
                        event_level=EventLevel.ERROR,
                        event_name="rtsp断流",
                        timestamp=datetime.now()
                    )
                    self._trigger_event(event)
                    self.stream_disconnected_reported = True
                
                time.sleep(2)  # 等待后重试
        
        print(f"[RTSP{self.stream_id}] 简化模式收流线程退出")
    
    def _stream_loop_pyav(self):
        """使用PyAV拉流（可获取帧类型）"""
        retry_count = 0
        max_retries = 5
        
        while self.is_running and retry_count < max_retries:
            try:
                # 根据use_udp标志选择传输协议
                transport = 'udp' if self.use_udp else 'tcp'
                print(f"[RTSP{self.stream_id}] 正在连接到: {self.url} (使用PyAV, 传输协议: {transport.upper()})")
                
                # 打开RTSP流 - PyAV需要使用format_options传递给底层FFmpeg
                # 参考: https://github.com/PyAV-Org/PyAV/issues/conversions
                format_options = {
                    'rtsp_transport': transport,
                    'rtsp_flags': 'prefer_tcp' if transport == 'tcp' else '',
                }
                # 移除空值
                format_options = {k: v for k, v in format_options.items() if v}
                
                print(f"[RTSP{self.stream_id}] PyAV打开参数: format_options={format_options}")
                self.av_container = av.open(self.url, options=format_options, format='rtsp')
                video_stream = self.av_container.streams.video[0]
                try:
                    # 跳过 B 帧，只解码 I/P，降低解码开销
                    video_stream.codec_context.skip_frame = "BIDIR"
                    print(f"[RTSP{self.stream_id}] 已设置 skip_frame=BIDIR，解码只保留 I/P 帧")
                except Exception as skip_err:
                    print(f"[RTSP{self.stream_id}] 设置 skip_frame 失败: {skip_err}")
                
                print(f"[RTSP{self.stream_id}] RTSP流已连接: {self.url}")
                
                first_frame = True
                self.last_stats_time = datetime.now()
                frame_count_in_period = 0
                bytes_in_period = 0
                decode_time_sum = 0.0
                decode_time_max = 0.0
                decode_time_count = 0
                
                for packet in self.av_container.demux(video_stream):
                    if not self.is_running:
                        break
                    
                    for frame_av in packet.decode():
                        if not self.is_running:
                            break
                        decode_start = time.time()
                        self.frame_count += 1
                        frame_count_in_period += 1
                        
                        # 优化: 转换为numpy数组（直接输出RGB格式，避免后续多次BGR→RGB转换）
                        frame = frame_av.to_ndarray(format='rgb24')
                        decode_cost = (time.time() - decode_start) * 1000.0
                        decode_time_sum += decode_cost
                        decode_time_max = max(decode_time_max, decode_cost)
                        decode_time_count += 1
                        
                        # 获取帧类型
                        pict_type = None
                        if hasattr(frame_av, 'pict_type'):
                            try:
                                # pict_type可能是枚举（有.name属性）或者是整数
                                pt = frame_av.pict_type
                                if hasattr(pt, 'name'):
                                    pict_type = pt.name
                                elif isinstance(pt, int):
                                    # 如果是整数，尝试映射到枚举值
                                    # PyAV枚举: I=1, P=2, B=3
                                    pict_type_map = {1: 'I', 2: 'P', 3: 'B', 0: 'UNKNOWN'}
                                    pict_type = pict_type_map.get(pt, 'UNKNOWN')
                                else:
                                    pict_type = str(pt)
                            except Exception as pt_err:
                                print(f"[Stream{self.stream_id}] 获取pict_type失败: {pt_err}")
                                pict_type = None
                        
                        # 估算帧大小
                        frame_size = frame.nbytes
                        self.total_bytes += frame_size
                        bytes_in_period += frame_size
                        
                        # 实时统计
                        now = datetime.now()
                        time_diff_live = (now - self.last_stats_time).total_seconds()
                        if time_diff_live > 0:
                            current_fps_live = frame_count_in_period / time_diff_live
                            current_bitrate_live = (bytes_in_period * 8) / (time_diff_live * 1000)
                        else:
                            current_fps_live = 0.0
                            current_bitrate_live = 0.0
                        
                        # 使用pict_type判断关键帧
                        is_keyframe = self.is_keyframe(self.frame_count, pict_type)
                        if is_keyframe:
                            self.keyframe_count += 1
                        
                        # 第一帧事件
                        if first_frame:
                            first_frame = False
                            self.stats.first_frame_time = datetime.now()
                            print(f"[Stream{self.stream_id}] 收到第一帧，URL: {self.url}, pict_type: {pict_type}")
                            
                            event = RuntimeEvent(
                                line_log=f"收到第一帧：{self.url}",
                                event_level=EventLevel.INFO,
                                event_name="收到第一帧",
                                timestamp=datetime.now()
                            )
                            self._trigger_event(event)
                            
                            if self.video_window_callback:
                                print(f"[Stream{self.stream_id}] 触发视频窗口创建回调...")
                                try:
                                    self.video_window_callback(self.stream_id)
                                except Exception as e:
                                    print(f"[Stream{self.stream_id}] 视频窗口创建回调失败: {e}")
                        
                        # 保存关键帧
                        if is_keyframe and self.keyframe_images_saved < 3 and self.save_dir:
                            self._save_keyframe(frame)
                        
                        # 每帧都调用回调更新GUI显示，但限制频率最高1秒1次
                        if self.frame_callback:
                            if self.last_callback_time is None or (now - self.last_callback_time).total_seconds() >= 1.0:
                                try:
                                    self.frame_callback(self.stream_id, frame, current_fps_live, current_bitrate_live)
                                    self.last_callback_time = now
                                except Exception as e:
                                    print(f"[Stream{self.stream_id}] 视频帧回调错误: {e}")
                                    import traceback
                                    traceback.print_exc()
                            else:
                                # 继续显示帧，但不更新统计信息
                                if self.frame_callback:
                                    try:
                                        self.frame_callback(self.stream_id, frame, 0, 0)  # 传递0的统计，只显示帧
                                    except Exception:
                                        pass
                        
                        # 每5秒统计一次帧率和码率
                        if (now - self.last_stats_time).total_seconds() >= 5.0:
                            self._update_stats(now, frame_count_in_period, bytes_in_period)
                            # [性能统计] 输出解码耗时
                            if decode_time_count > 0:
                                avg_decode = decode_time_sum / decode_time_count
                                print(f"[性能-Stream{self.stream_id}] 近5秒解码: 平均{avg_decode:.2f}ms, 最大{decode_time_max:.2f}ms, 帧数{decode_time_count}")
                                # 重置统计
                                decode_time_sum = 0.0
                                decode_time_max = 0.0
                                decode_time_count = 0
                            frame_count_in_period = 0
                            bytes_in_period = 0
                            self.last_stats_time = now
                
                break  # 正常退出
                
            except Exception as e:
                print(f"[RTSP{self.stream_id}] PyAV拉流错误: {e}")
                retry_count += 1
                if self.av_container:
                    try:
                        self.av_container.close()
                    except:
                        pass
                time.sleep(2)
        
        if self.av_container:
            try:
                self.av_container.close()
            except:
                pass
    
    def _stream_loop_opencv(self):
        """使用OpenCV拉流（fallback，无法获取准确帧类型）"""
        retry_count = 0
        max_retries = 5
        transport = 'udp' if self.use_udp else 'tcp'
        
        # 设置环境变量控制FFmpeg的RTSP传输协议
        import os
        os.environ['OPENCV_FFMPEG_CAPTURE_OPTIONS'] = f'rtsp_transport;{transport}'
        print(f"[RTSP{self.stream_id}] 设置环境变量: OPENCV_FFMPEG_CAPTURE_OPTIONS=rtsp_transport;{transport}")
        
        while self.is_running and retry_count < max_retries:
            try:
                # 尝试打开RTSP流，根据use_udp设置传输协议
                print(f"[RTSP{self.stream_id}] 正在连接到: {self.url} (使用OpenCV, 协议: {transport.upper()})")
                
                # 尝试使用params参数（OpenCV 4.5+）
                try:
                    params = [
                        cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 5000,
                        cv2.CAP_PROP_READ_TIMEOUT_MSEC, 5000
                    ]
                    self.capture = cv2.VideoCapture(self.url, cv2.CAP_FFMPEG, params)
                    print(f"[RTSP{self.stream_id}] 使用params参数创建 VideoCapture")
                except Exception as e:
                    print(f"[RTSP{self.stream_id}] params参数方式失败: {e}，使用默认方式")
                    self.capture = cv2.VideoCapture(self.url, cv2.CAP_FFMPEG)
                    if hasattr(cv2, 'CAP_PROP_OPEN_TIMEOUT_MSEC'):
                        self.capture.set(cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 5000)
                
                if not self.capture.isOpened():
                    print(f"[RTSP{self.stream_id}] 无法打开RTSP流: {self.url}")
                    retry_count += 1
                    time.sleep(2)
                    continue
                
                print(f"[RTSP{self.stream_id}] RTSP流已连接: {self.url}")
                
                # 触发收流成功事件（第一次收到数据后）
                first_frame = True
                self.last_stats_time = datetime.now()
                frame_count_in_period = 0
                bytes_in_period = 0
                read_time_sum = 0.0
                read_time_max = 0.0
                read_time_count = 0
                
                while self.is_running:
                    # 检查capture是否已被释放（by stop()方法）
                    if not self.capture:
                        print(f"[RTSP{self.stream_id}] 检测到capture已被释放，退出读取循环")
                        break
                    
                    read_start = time.time()
                    ret, frame = self.capture.read()
                    read_cost_ms = (time.time() - read_start) * 1000.0
                    read_time_sum += read_cost_ms
                    read_time_max = max(read_time_max, read_cost_ms)
                    read_time_count += 1
                    if self.frame_count < 5 or self.frame_count % 30 == 1:
                        print(f"[RTSP{self.stream_id}] capture.read耗时: {read_cost_ms:.2f} ms")
                    
                    if not ret:
                        print(f"[RTSP{self.stream_id}] 无法读取帧或流断流: {self.url}")
                        event = RuntimeEvent(
                            line_log=f"rtsp断流：{self.url}",
                            event_level=EventLevel.EMERGE,
                            event_name="rtsp断流",
                            timestamp=datetime.now()
                        )
                        self._trigger_event(event)
                        break
                    
                    self.frame_count += 1
                    frame_count_in_period += 1
                    
                    # 估算帧大小
                    frame_size = frame.nbytes
                    self.total_bytes += frame_size
                    bytes_in_period += frame_size

                    # 基于当前周期的实时统计（用于GUI叠加）
                    now = datetime.now()
                    time_diff_live = (now - self.last_stats_time).total_seconds()
                    if time_diff_live > 0:
                        current_fps_live = frame_count_in_period / time_diff_live
                        current_bitrate_live = (bytes_in_period * 8) / (time_diff_live * 1000)
                    else:
                        current_fps_live = 0.0
                        current_bitrate_live = 0.0
                    
                    # 基于GOP间隔检测关键帧（不需要图像处理）
                    is_keyframe = self.is_keyframe(self.frame_count)
                    if is_keyframe:
                        self.keyframe_count += 1
                    
                    # 第一帧事件
                    if first_frame:
                        first_frame = False
                        self.stats.first_frame_time = datetime.now()
                        print(f"[Stream{self.stream_id}] 收到第一帧，URL: {self.url}")
                        
                        event = RuntimeEvent(
                            line_log=f"收到第一帧：{self.url}",
                            event_level=EventLevel.INFO,
                            event_name="收到第一帧",
                            timestamp=datetime.now()
                        )
                        self._trigger_event(event)
                        
                        # 触发视频窗口创建回调
                        if self.video_window_callback:
                            print(f"[Stream{self.stream_id}] 触发视频窗口创建回调...")
                            try:
                                self.video_window_callback(self.stream_id)
                            except Exception as e:
                                print(f"[Stream{self.stream_id}] 视频窗口创建回调失败: {e}")
                                import traceback
                                traceback.print_exc()
                        else:
                            print(f"[Stream{self.stream_id}] 警告：没有设置video_window_callback")
                    
                    # 保存关键帧（只保存检测到的I帧）
                    if is_keyframe and self.keyframe_images_saved < 3 and self.save_dir:
                        self._save_keyframe(frame)
                    
                    # 每帧都调用回调更新GUI显示，但限制统计信息更新频率为最高1秒一次
                    if self.frame_callback:
                        if self.last_callback_time is None or (now - self.last_callback_time).total_seconds() >= 1.0:
                            try:
                                # 更新统计信息
                                self.frame_callback(self.stream_id, frame, current_fps_live, current_bitrate_live)
                                self.last_callback_time = now
                            except Exception as e:
                                print(f"[Stream{self.stream_id}] 视频帧回调错误: {e}")
                                import traceback
                                traceback.print_exc()
                        else:
                            # 继续显示帧，但不更新统计信息（传0值）
                            try:
                                self.frame_callback(self.stream_id, frame, 0, 0)
                            except Exception:
                                pass
                    else:
                        if self.frame_count <= 3:  # 只在前3帧打印
                            print(f"[RTSP{self.stream_id}] 警告: frame_callback未设置（第{self.frame_count}帧）")
                    
                    # 每5秒统计一次帧率和码率
                    if (now - self.last_stats_time).total_seconds() >= 5.0:
                        self._update_stats(now, frame_count_in_period, bytes_in_period)
                        # [性能统计] 输出读取耗时（OpenCV）
                        if read_time_count > 0:
                            avg_read = read_time_sum / read_time_count
                            print(f"[性能-Stream{self.stream_id}] 近5秒读取: 平均{avg_read:.2f}ms, 最大{read_time_max:.2f}ms, 帧数{read_time_count}")
                            read_time_sum = 0.0
                            read_time_max = 0.0
                            read_time_count = 0
                        frame_count_in_period = 0
                        bytes_in_period = 0
                        self.last_stats_time = now
                
                break  # 正常退出循环
                
            except BaseException as e:
                print(f"RTSP拉流错误: {e}")
                # 如果已停止，不再重试，直接退出循环
                if not self.is_running:
                    break
                retry_count += 1
                time.sleep(2)
        
        # 如果多次重试失败，触发断流事件
        if retry_count >= max_retries:
            event = RuntimeEvent(
                line_log=f"rtsp断流：{self.url}",
                event_level=EventLevel.EMERGE,
                event_name="rtsp断流",
                timestamp=datetime.now()
            )
            self._trigger_event(event)
        
        # 确保capture被释放（使用异常捕获避免C++异常导致程序退出）
        try:
            if self.capture:
                print(f"[RTSP{self.stream_id}] 拉流线程结束，释放VideoCapture...")
                self.capture.release()
                self.capture = None
                print(f"[RTSP{self.stream_id}] 拉流线程的VideoCapture已释放")
        except BaseException as release_err:
            print(f"[RTSP{self.stream_id}] 拉流线程释放VideoCapture失败（忽略）: {type(release_err).__name__}")
    
    def _update_stats(self, now, frame_count_in_period, bytes_in_period):
        """更新统计信息"""
        time_diff = (now - self.last_stats_time).total_seconds()
        
        if time_diff > 0:
            # 计算帧率（所有帧）
            fps = frame_count_in_period / time_diff
            bitrate = (bytes_in_period * 8) / (time_diff * 1000)
            
            # 调试信息
            if len(self.stats.fps_history) == 0 or len(self.stats.bitrate_history) == 0:
                print(f"[Stream{self.stream_id}] _update_stats调用: now={now}, type={type(now)}, time_diff={time_diff}, fps={fps:.2f}, bitrate={bitrate:.2f}")
            
            self.stats.fps_history.append(StatData(timestamp=now, value=fps))
            self.stats.bitrate_history.append(StatData(timestamp=now, value=bitrate))
            
            # 再次检查
            if len(self.stats.fps_history) <= 3:
                latest_ts = self.stats.fps_history[-1].timestamp
                print(f"[Stream{self.stream_id}] 新增fps数据点#{len(self.stats.fps_history)}: timestamp={latest_ts}, type={type(latest_ts)}, value={fps:.2f}")
    
    def _save_keyframe(self, frame):
        """保存关键帧"""
        if not self.save_dir:
            return
        
        try:
            self.keyframe_images_saved += 1
            filename = f"{self.save_dir}/stream{self.stream_id}_keyframe{self.keyframe_images_saved}.jpg"
            cv2.imwrite(filename, frame)
            print(f"保存关键帧: {filename}")
        except Exception as e:
            print(f"保存关键帧失败: {e}")
    
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
    
    def set_frame_callback(self, callback: Callable[[int, any, float, float], None]):
        """设置视频帧回调（用于GUI显示），参数：stream_id, frame, fps, bitrate"""
        print(f"[RTSP{self.stream_id}] 正在设置frame_callback...")
        self.frame_callback = callback
        print(f"[RTSP{self.stream_id}] frame_callback设置成功，将在每帧调用")
    
    def set_video_window_callback(self, callback: Callable[[int], None]):
        """设置视频窗口回调（在首帧时创建独立窗口），参数：stream_id"""
        self.video_window_callback = callback
    
    def get_latest_fps(self) -> float:
        """获取最新帧率"""
        if self.stats.fps_history:
            return self.stats.fps_history[-1].value
        return 0.0
    
    def get_latest_bitrate(self) -> float:
        """获取最新码率"""
        if self.stats.bitrate_history:
            return self.stats.bitrate_history[-1].value
        return 0.0
    
    def get_frame_stats(self) -> dict:
        """获取帧统计信息"""
        return {
            "total_frames": self.frame_count,
            "keyframes": self.keyframe_count,
            "keyframe_ratio": f"{(self.keyframe_count / self.frame_count * 100):.2f}%" if self.frame_count > 0 else "0%"
        }
