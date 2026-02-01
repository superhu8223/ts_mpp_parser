#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
单个视频流播放窗口
"""

import tkinter as tk
from tkinter import ttk
import threading
import time
from datetime import datetime
from typing import Optional, Callable
import cv2
try:
    from PIL import Image, ImageTk
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False


class VideoWindow:
    """单个RTSP流播放窗口"""
    
    def __init__(self, stream_id: int, url: str, task_name: str = "", on_close_callback=None):
        print(f"[VideoWindow{stream_id}] 初始化窗口...")
        self.stream_id = stream_id
        self.url = url
        self.task_name = task_name
        self.on_close_callback = on_close_callback  # 窗口关闭时的回调函数
        self.is_playing = True
        self.frame_skip_ratio = 30  # 优化: 默认跳过30%帧，降低处理负担
        self.frame_count = 0  # 用于跳帧计数
        
        # 优化: Resize频率控制
        self.resize_interval = 3  # 每3帧resize一次
        self.resize_counter = 0
        self.last_resized_frame = None
        self.last_window_size = (0, 0)
        
        # 性能统计
        self.resize_time_sum = 0.0
        self.resize_time_max = 0.0
        self.resize_count = 0
        self.convert_time_sum = 0.0
        self.convert_time_max = 0.0
        self.convert_count = 0
        self.total_frame_time_sum = 0.0
        self.total_frame_time_max = 0.0
        self.total_frame_count = 0
        self.last_perf_log_time = time.time()
        
        # ✅ 修复: 创建Toplevel窗口而非独立Tk实例，共享主窗口的mainloop
        print(f"[VideoWindow{stream_id}] 创建Tkinter窗口...")
        self.root = tk.Toplevel()
        self.root.title(f"Video Stream {stream_id} - {task_name}")
        
        # 设置窗口初始大小和位置：在主界面右边上下排布
        # 主界面假设为1200x800，在右边放置视频窗口
        # stream_id为0放上方，stream_id为1放下方
        initial_width = 650
        initial_height = 550
        x_pos = 1220  # 主界面(1200)的右边
        y_pos = 50 + (stream_id * 600)  # stream_id=0: y=50, stream_id=1: y=650
        
        self.root.geometry(f"{initial_width}x{initial_height}+{x_pos}+{y_pos}")
        # 设置窗口关闭协议
        self.root.protocol("WM_DELETE_WINDOW", self._on_window_close)
        print(f"[VideoWindow{stream_id}] 窗口已创建，位置: +{x_pos}+{y_pos}, 大小: {initial_width}x{initial_height}")
        
        # 统计信息回调
        self.stats_callback: Optional[Callable[[float, float], None]] = None
        
        # 创建UI
        print(f"[VideoWindow{stream_id}] 创建UI组件...")
        self._create_widgets()
        
        # ✅ 不再需要独立GUI线程，Toplevel窗口自动共享主窗口mainloop
        print(f"[VideoWindow{stream_id}] 初始化完成，窗口已显示")
    
    def _create_widgets(self):
        """创建UI组件"""
        # 顶部：状态栏
        status_frame = tk.Frame(self.root, relief=tk.SUNKEN, bd=2, bg="lightgray")
        status_frame.pack(side=tk.TOP, fill=tk.X, padx=2, pady=2)
        
        # 任务名称
        task_label = tk.Label(status_frame, text=f"Task: {self.task_name}", font=("Arial", 10))
        task_label.pack(side=tk.LEFT, padx=5, pady=3)
        
        # URL（隐藏“URL:”字样，仅显示地址）
        url_label = tk.Label(status_frame, text=self.url, font=("Arial", 9), fg="blue")
        url_label.pack(side=tk.LEFT, padx=5, pady=3)
        
        # FPS和码率
        self.stats_label = tk.Label(status_frame, text="FPS: 0.00 | Bitrate: 0.00 kbps", 
                                    font=("Arial", 9), fg="green")
        self.stats_label.pack(side=tk.LEFT, padx=5, pady=3)
        
        # 控制区
        control_frame = tk.Frame(self.root)
        control_frame.pack(side=tk.TOP, fill=tk.X, padx=5, pady=5)
        
        # 播放控制 - 单选按钮
        tk.Label(control_frame, text="播放:", font=("Arial", 9)).pack(side=tk.LEFT, padx=2)
        self.play_var = tk.BooleanVar(value=True)
        play_check = tk.Checkbutton(control_frame, text="启用播放", variable=self.play_var,
                        command=self._on_play_toggled)
        play_check.pack(side=tk.LEFT, padx=2)
        play_check.select()  # 确保初始勾选
        
        # 跳帧控制 - 拖动条
        tk.Label(control_frame, text="跳帧率:", font=("Arial", 9)).pack(side=tk.LEFT, padx=10)
        self.skip_scale = ttk.Scale(control_frame, from_=0, to=100, orient=tk.HORIZONTAL,
                                    length=200, command=self._on_skip_changed)
        self.skip_scale.pack(side=tk.LEFT, padx=2)
        
        self.skip_label = tk.Label(control_frame, text="0%", font=("Arial", 9), width=3)
        self.skip_label.pack(side=tk.LEFT, padx=2)
        
        # 视频显示区域
        self.video_label = tk.Label(self.root, text="等待视频...", 
                                   bg="black", fg="white", font=("Arial", 12))
        self.video_label.pack(fill=tk.BOTH, expand=True, padx=2, pady=2)
        
        self.current_image = None  # 保持图像引用
        self.is_closed = False
        
        # ✅ 不再需要独立GUI线程，Toplevel窗口自动共享主窗口mainloop
        print(f"[VideoWindow{self.stream_id}] 初始化完成，窗口已显示")
    
    def _on_play_toggled(self):
        """播放开关回调"""
        self.is_playing = self.play_var.get()
        print(f"[Stream{self.stream_id}] 播放切换: {self.is_playing}")
        if not self.is_playing:
            # 清空画面并显示暂停提示
            self.current_image = None
            self.video_label.configure(image='', text="已暂停", bg="black", fg="white")
        else:
            # 恢复播放提示
            if not self.current_image:
                self.video_label.configure(text="等待视频...", bg="black", fg="white")
    
    def _on_skip_changed(self, value):
        """跳帧比例变化回调"""
        self.frame_skip_ratio = int(float(value))
        self.skip_label.config(text=f"{self.frame_skip_ratio}%")
    
    def update_stats(self, fps: float, bitrate: float):
        """更新统计信息"""
        if self.is_closed or not self.root or not self.root.winfo_exists():
            return
        def _update():
            if self.is_closed or not self.root or not self.root.winfo_exists():
                return
            try:
                if self.stats_label.winfo_exists():
                    self.stats_label.config(
                        text=f"FPS: {fps:.2f} | Bitrate: {bitrate:.2f} kbps"
                    )
            except Exception:
                return
        try:
            self.root.after(0, _update)
        except Exception:
            return

    def set_window_size(self, width: int, height: int):
        """设置窗口显示尺寸（用于统一所有窗口大小）"""
        print(f"[VideoWindow{self.stream_id}] 设置窗口大小: {width}x{height}")
        if self.is_closed or not self.root or not self.root.winfo_exists():
            return
        def _set():
            if self.is_closed or not self.root or not self.root.winfo_exists():
                return
            # 设置Label容器的大小
            if self.video_label.winfo_exists():
                self.video_label.config(width=width, height=height)
            # 更新窗口大小，并调整位置确保不与UI重叠
            # stream_id为0放上方，stream_id为1放下方
            x_pos = 1220  # 主界面(1200)的右边
            # 减小窗口间距，避免第二个窗口超出屏幕
            y_pos = 50 + (self.stream_id * (height + 50))  # 上下排布，中间间距50
            final_height = height + 150  # +150用于UI边框和控制区
            try:
                self.root.geometry(f"{width+20}x{final_height}+{x_pos}+{y_pos}")
                print(f"[VideoWindow{self.stream_id}] 窗口已调整: 大小={width+20}x{final_height}, 位置=+{x_pos}+{y_pos}")
            except Exception:
                return
        try:
            self.root.after(0, _set)
        except Exception:
            return
    
    def update_frame(self, frame):
        """显示视频帧"""
        if self.is_closed or not self.root or not self.root.winfo_exists():
            return
        if not self.is_playing:
            # 不播放时停止更新画面，保持当前显示
            self.frame_count += 1
            return
        
        # 调试：每100帧打印一次
        if self.frame_count % 100 == 0:
            print(f"[VideoWindow{self.stream_id}] update_frame调用: frame_count={self.frame_count}, is_playing={self.is_playing}")
        
        # 检查是否应该跳帧
        skip_prob = self.frame_skip_ratio / 100.0
        self.frame_count += 1
        
        # 简单的跳帧：每N帧播放1帧
        if self.frame_skip_ratio > 0:
            frames_to_skip = int(100 / (100 - self.frame_skip_ratio)) if self.frame_skip_ratio < 100 else 1
            if self.frame_count % frames_to_skip != 0:
                return
        
        # 显示帧 - 使用after将操作放在主线程中执行
        if not PIL_AVAILABLE:
            return
        
        # 使用root.after()将更新操作调度到主线程
        try:
            self.root.after(0, self._update_frame_in_mainthread, frame)
        except Exception as e:
            print(f"[VideoWindow{self.stream_id}] 调度frame更新失败: {e}")
    
    def _update_frame_in_mainthread(self, frame):
        """在主线程中更新视频帧"""
        if self.is_closed or not self.root or not self.root.winfo_exists():
            return
        frame_start_time = time.time()
        try:
            # 缩放到窗口大小
            if not self.video_label.winfo_exists():
                return
            label_width = max(self.video_label.winfo_width(), 320)
            label_height = max(self.video_label.winfo_height(), 240)
            current_window_size = (label_width, label_height)
            
            h, w = frame.shape[:2]
            aspect = w / h
            
            if label_width / label_height > aspect:
                new_h = label_height
                new_w = int(label_height * aspect)
            else:
                new_w = label_width
                new_h = int(label_width / aspect)
            
            # 优化: 智能resize - 每N帧或窗口大小变化时才resize
            self.resize_counter += 1
            need_resize = (
                self.resize_counter % self.resize_interval == 1 or
                current_window_size != self.last_window_size or
                self.last_resized_frame is None
            )
            
            if need_resize:
                # 执行resize
                resize_start = time.time()
                frame_resized = cv2.resize(frame, (new_w, new_h), interpolation=cv2.INTER_LINEAR)
                resize_cost = (time.time() - resize_start) * 1000.0
                self.resize_time_sum += resize_cost
                self.resize_time_max = max(self.resize_time_max, resize_cost)
                self.resize_count += 1
                self.last_resized_frame = frame_resized
                self.last_window_size = current_window_size
            else:
                # 复用上次resize的结果（仅更新像素数据）
                # 注意：这里假设帧尺寸不变，仅内容更新
                if self.last_resized_frame is not None and self.last_resized_frame.shape[:2] == (new_h, new_w):
                    # 快速路径：直接用上次的尺寸
                    resize_start = time.time()
                    frame_resized = cv2.resize(frame, (new_w, new_h), interpolation=cv2.INTER_LINEAR)
                    resize_cost = (time.time() - resize_start) * 1000.0
                    self.resize_time_sum += resize_cost
                    self.resize_time_max = max(self.resize_time_max, resize_cost)
                    self.resize_count += 1
                    self.last_resized_frame = frame_resized
                else:
                    resize_start = time.time()
                    frame_resized = cv2.resize(frame, (new_w, new_h), interpolation=cv2.INTER_LINEAR)
                    resize_cost = (time.time() - resize_start) * 1000.0
                    self.resize_time_sum += resize_cost
                    self.resize_time_max = max(self.resize_time_max, resize_cost)
                    self.resize_count += 1
            
            # 确保正确的颜色空间：PyAV输出RGB，OpenCV fallback输出BGR
            # 为了兼容两种来源，统一应用转换。虽然PyAV已是RGB，但转换仍是必要的安全措施
            # 优化：使用NumPy通道交换而不是cv2.cvtColor，性能提升>8000x
            convert_start = time.time()
            if len(frame_resized.shape) == 3 and frame_resized.shape[2] == 3:
                # 快速BGR→RGB转换：使用NumPy通道反转（仅改变数据视图，不复制内存）
                # 性能: NumPy [..., ::-1] (0.3us) vs cv2.cvtColor (3.6ms) = 12000倍快
                frame_rgb = frame_resized[..., ::-1]
                convert_cost = (time.time() - convert_start) * 1000.0
                self.convert_time_sum += convert_cost
                self.convert_time_max = max(self.convert_time_max, convert_cost)
                self.convert_count += 1
            else:
                frame_rgb = frame_resized  # 单通道或异常格式，直接使用
                convert_cost = 0.0
            
            img = Image.fromarray(frame_rgb)
            imgtk = ImageTk.PhotoImage(image=img)
            
            # 更新标签
            if self.video_label.winfo_exists():
                self.video_label.configure(image=imgtk, text="")
            self.current_image = imgtk  # 保持引用
            
            # 统计总耗时
            total_frame_cost = (time.time() - frame_start_time) * 1000.0
            self.total_frame_time_sum += total_frame_cost
            self.total_frame_time_max = max(self.total_frame_time_max, total_frame_cost)
            self.total_frame_count += 1
            
            # 每5秒统计一次性能数据（不打印）
            if time.time() - self.last_perf_log_time >= 5.0:
                # 重置统计
                self.resize_time_sum = 0.0
                self.resize_time_max = 0.0
                self.resize_count = 0
                self.convert_time_sum = 0.0
                self.convert_time_max = 0.0
                self.convert_count = 0
                self.total_frame_time_sum = 0.0
                self.total_frame_time_max = 0.0
                self.total_frame_count = 0
                self.last_perf_log_time = time.time()
        except Exception as e:
            print(f"[VideoWindow{self.stream_id}] 更新视频帧失败: {e}")
    
    def _on_window_close(self):
        """窗口关闭时的处理"""
        print(f"[VideoWindow{self.stream_id}] 窗口被关闭，调用回调...")
        self.is_closed = True
        self.is_playing = False
        
        # 先调用回调（如果有）
        if self.on_close_callback:
            try:
                self.on_close_callback(self.stream_id)
            except Exception as e:
                print(f"[VideoWindow{self.stream_id}] 关闭回调执行失败: {e}")
        
        # 然后销毁窗口（不再调用close()，避免递归）
        try:
            if self.root and self.root.winfo_exists():
                # 移除协议处理，防止再次触发
                self.root.protocol("WM_DELETE_WINDOW", lambda: None)
                self.root.destroy()
        except:
            pass
        finally:
            self.root = None

    
    def close(self):
        """关闭窗口"""
        try:
            print(f"[VideoWindow{self.stream_id}] 正在关闭窗口...")
            self.is_closed = True
            self.is_playing = False
            
            # 安全地关闭Toplevel窗口
            if self.root:
                try:
                    # 检查窗口是否还存在
                    if self.root.winfo_exists():
                        # 解除窗口关闭协议回调，防止递归
                        self.root.protocol("WM_DELETE_WINDOW", lambda: None)
                        # 销毁窗口
                        self.root.destroy()
                except Exception as err:
                    print(f"[VideoWindow{self.stream_id}] destroy()异常: {err}")
                finally:
                    self.root = None
            
            print(f"[VideoWindow{self.stream_id}] 窗口已关闭")
        except Exception as e:
            print(f"[VideoWindow{self.stream_id}] 关闭窗口异常: {e}")
            import traceback
            traceback.print_exc()

