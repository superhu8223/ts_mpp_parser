#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
使用OpenCV显示视频的轻量级窗口
避免Tkinter在非主线程运行的问题
"""

import cv2
import threading
import numpy as np
import time
from datetime import datetime
from typing import Optional


class VideoWindow:
    """单个RTSP流播放窗口（使用OpenCV）"""
    
    # 固定窗口尺寸
    WINDOW_WIDTH = 640
    WINDOW_HEIGHT = 360
    
    def __init__(self, stream_id: int, url: str, task_name: str = "", on_close_callback=None):
        self.stream_id = stream_id
        self.url = url
        self.task_name = task_name
        self.on_close_callback = on_close_callback  # 窗口关闭时的回调函数
        self.is_playing = True  # 默认播放
        self.frame_skip_ratio = 0  # 跳帧比例（0-100%）
        self.frame_count = 0
        self.is_running = True
        self.current_fps = 0.0
        self.current_bitrate = 0.0
        self.source_resolution = None  # (w, h)
        self.show_video = True  # 是否显示视频内容（默认显示）
        # 缩放耗时统计
        self.resize_time_sum = 0.0
        self.resize_time_max = 0.0
        self.resize_time_count = 0
        self.resize_last_log_time = time.time()
        
        # 线程安全：使用锁保护current_frame访问
        self.frame_lock = threading.Lock()
        self.current_frame = None
        
        # 窗口名称
        self.window_name = f"Stream{stream_id} - {task_name}"
        print(f"[VideoWindow{stream_id}] 准备在显示线程中创建OpenCV窗口（{self.WINDOW_WIDTH}x{self.WINDOW_HEIGHT}）")
        
        # 启动显示线程（在线程中创建窗口）
        self.thread = threading.Thread(target=self._display_loop, daemon=True)
        self.thread.start()
        print(f"[VideoWindow{stream_id}] 显示线程已启动")
    
    def update_stats(self, fps: float, bitrate: float):
        """更新统计信息"""
        self.current_fps = fps
        self.current_bitrate = bitrate
    
    def set_show_video(self, show: bool):
        """设置是否显示视频内容"""
        self.show_video = show
        print(f"[VideoWindow{self.stream_id}] 视频显示模式: {'显示视频' if show else '仅状态栏'}")
    
    def update_frame(self, frame):
        """接收视频帧"""
        if not self.is_running or not self.is_playing:
            self.frame_count += 1
            return
        
        # 跳帧处理
        self.frame_count += 1
        if self.frame_skip_ratio > 0:
            frames_to_skip = int(100 / (100 - self.frame_skip_ratio)) if self.frame_skip_ratio < 100 else 1
            if self.frame_count % frames_to_skip != 0:
                return
        
        # 参数类型检查
        if not hasattr(frame, 'shape'):
            if self.frame_count <= 5:  # 只打印前几次错误
                print(f"[VideoWindow{self.stream_id}] 错误：frame参数类型不正确，期望numpy数组，实际为{type(frame)}")
            return
        
        try:
            # 检查是否显示视频
            if not self.show_video:
                # 不显示视频时，只创建状态栏
                with self.frame_lock:
                    self.current_frame = self._create_stats_only_frame()
                return
            
            # 在显示前添加统计信息到画面
            h, w = frame.shape[:2]
            if self.source_resolution != (w, h):
                self.source_resolution = (w, h)
            resize_start = time.time()
            display_frame = cv2.resize(frame, (self.WINDOW_WIDTH, self.WINDOW_HEIGHT))
            resize_cost_ms = (time.time() - resize_start) * 1000.0
            self.resize_time_sum += resize_cost_ms
            self.resize_time_max = max(self.resize_time_max, resize_cost_ms)
            self.resize_time_count += 1
            
            if self.frame_count % 60 == 1:
                print(f"[VideoWindow{self.stream_id}] update_frame被调用 #{self.frame_count}, 原始: {h}x{w}, 缩放后: {self.WINDOW_HEIGHT}x{self.WINDOW_WIDTH}")

            # 每5秒打印一次缩放耗时统计
            now_ts = time.time()
            if now_ts - self.resize_last_log_time >= 5.0:
                avg_resize = self.resize_time_sum / self.resize_time_count if self.resize_time_count else 0.0
                print(f"[VideoWindow{self.stream_id}] 近5秒resize耗时: 平均{avg_resize:.2f} ms, 最大{self.resize_time_max:.2f} ms")
                self.resize_time_sum = 0.0
                self.resize_time_max = 0.0
                self.resize_time_count = 0
                self.resize_last_log_time = now_ts
            
            # 绘制状态栏
            self._draw_stats_overlay(display_frame)
            
            # 线程安全地存储帧供显示线程使用
            with self.frame_lock:
                self.current_frame = display_frame
                if self.frame_count % 60 == 1:
                    print(f"[VideoWindow{self.stream_id}] current_frame已设置，地址: {id(self.current_frame)}, 形状: {self.current_frame.shape}")
            
        except Exception as e:
            print(f"[VideoWindow{self.stream_id}] 帧处理失败: {e}")
            import traceback
            traceback.print_exc()
    
    def _display_loop(self):
        """显示线程"""
        print(f"[VideoWindow{self.stream_id}] _display_loop线程已启动")
        # 在显示线程中创建窗口
        window_created = False
        try:
            print(f"[VideoWindow{self.stream_id}] 尝试创建OpenCV窗口，窗口名: {self.window_name}")
            cv2.namedWindow(self.window_name, cv2.WINDOW_AUTOSIZE)
            window_created = True
            print(f"[VideoWindow{self.stream_id}] OpenCV窗口已创建（在显示线程中）")
        except Exception as e:
            print(f"[VideoWindow{self.stream_id}] 创建窗口失败: {e}")
            import traceback
            traceback.print_exc()
            # 即使窗口创建失败，也继续尝试，可能稍后能创建
            # return
        
        display_count = 0
        frame_to_display = None
        no_frame_logged = False
        loop_count = 0
        window_check_failures = 0  # 窗口检查失败计数
        
        while self.is_running:
            # 在循环开始处立即检查状态
            if not self.is_running:
                print(f"[VideoWindow{self.stream_id}] 检测到is_running=False，准备退出")
                break
            
            loop_count += 1
            if loop_count % 100 == 1:
                print(f"[VideoWindow{self.stream_id}] 显示循环 #{loop_count}, frame_to_display is None: {frame_to_display is None}")
            try:
                # 线程安全地读取当前帧
                with self.frame_lock:
                    frame_to_display = self.current_frame
                
                if frame_to_display is not None:
                    display_count += 1
                    if display_count % 30 == 1:
                        print(f"[VideoWindow{self.stream_id}] 正在显示第{display_count}帧，帧尺寸: {frame_to_display.shape}, 地址: {id(frame_to_display)}")
                    try:
                        # 如果窗口未创建，尝试创建
                        if not window_created:
                            try:
                                print(f"[VideoWindow{self.stream_id}] 尝试创建窗口（在frame回调中）...")
                                cv2.namedWindow(self.window_name, cv2.WINDOW_AUTOSIZE)
                                window_created = True
                                print(f"[VideoWindow{self.stream_id}] 窗口已创建")
                            except Exception as window_create_err:
                                print(f"[VideoWindow{self.stream_id}] 再次创建窗口失败: {window_create_err}")
                        
                        # 显示帧
                        cv2.imshow(self.window_name, frame_to_display)
                        if display_count <= 3:
                            print(f"[VideoWindow{self.stream_id}] 第一帧已显示到窗口: {self.window_name}")
                    except cv2.error as imshow_err:
                        # imshow失败通常意味着窗口已被关闭
                        print(f"[VideoWindow{self.stream_id}] imshow失败，窗口可能已关闭: {imshow_err}")
                        self.is_running = False
                        if self.on_close_callback:
                            try:
                                self.on_close_callback(self.stream_id)
                            except Exception as cb_err:
                                print(f"[VideoWindow{self.stream_id}] 关闭回调执行失败: {cb_err}")
                        break
                    no_frame_logged = False  # 重置标志
                else:
                    # 当没有帧可显示时，也需要保持窗口活跃
                    if not no_frame_logged:
                        print(f"[VideoWindow{self.stream_id}] 显示线程开始，等待第一帧数据...")
                        no_frame_logged = True
                    # 仍然调用imshow以保持窗口响应
                    placeholder = self._create_placeholder_frame()
                    if placeholder is not None:
                        try:
                            cv2.imshow(self.window_name, placeholder)
                        except cv2.error as imshow_err:
                            print(f"[VideoWindow{self.stream_id}] imshow失败，窗口可能已关闭: {imshow_err}")
                            self.is_running = False
                            if self.on_close_callback:
                                try:
                                    self.on_close_callback(self.stream_id)
                                except Exception as cb_err:
                                    print(f"[VideoWindow{self.stream_id}] 关闭回调执行失败: {cb_err}")
                            break
                
                # 处理键盘事件
                key = cv2.waitKey(10) & 0xFF
                
                # 每50个循环检查一次窗口是否存在（降低检查频率，减少误判）
                if loop_count % 50 == 0:
                    try:
                        # 尝试获取窗口属性，如果窗口不存在会抛出异常
                        prop = cv2.getWindowProperty(self.window_name, cv2.WND_PROP_AUTOSIZE)
                        if prop == -1:
                            # 窗口可能被关闭，增加失败计数
                            window_check_failures += 1
                            if window_check_failures >= 3:  # 连续3次检查失败才认为窗口真的关闭
                                print(f"[VideoWindow{self.stream_id}] 检测到窗口被关闭（property=-1，连续{window_check_failures}次）")
                                self.is_running = False
                                if self.on_close_callback:
                                    try:
                                        self.on_close_callback(self.stream_id)
                                    except Exception as cb_err:
                                        print(f"[VideoWindow{self.stream_id}] 关闭回调执行失败: {cb_err}")
                                break
                        else:
                            # 检查成功，重置失败计数
                            window_check_failures = 0
                    except cv2.error as prop_err:
                        # 获取属性失败，增加失败计数
                        window_check_failures += 1
                        if window_check_failures >= 3:  # 连续3次失败才认为窗口真的关闭
                            print(f"[VideoWindow{self.stream_id}] 窗口属性检查失败（连续{window_check_failures}次），窗口已关闭: {prop_err}")
                            self.is_running = False
                            if self.on_close_callback:
                                try:
                                    self.on_close_callback(self.stream_id)
                                except Exception as cb_err:
                                    print(f"[VideoWindow{self.stream_id}] 关闭回调执行失败: {cb_err}")
                            break
                
                if key == ord('q'):
                    print(f"[VideoWindow{self.stream_id}] 用户关闭窗口")
                    self.is_running = False  # 确保设置标志
                    # 触发关闭回调
                    if self.on_close_callback:
                        try:
                            self.on_close_callback(self.stream_id)
                        except Exception as cb_err:
                            print(f"[VideoWindow{self.stream_id}] 关闭回调执行失败: {cb_err}")
                    break
                elif key == ord(' '):
                    # 空格切换播放/暂停
                    self.is_playing = not self.is_playing
                    print(f"[VideoWindow{self.stream_id}] 播放状态: {'播放' if self.is_playing else '暂停'}")
                elif key == ord('+') or key == ord('='):
                    # +增加跳帧
                    self.frame_skip_ratio = min(100, self.frame_skip_ratio + 10)
                    print(f"[VideoWindow{self.stream_id}] 跳帧率: {self.frame_skip_ratio}%")
                elif key == ord('-') or key == ord('_'):
                    # -减少跳帧
                    self.frame_skip_ratio = max(0, self.frame_skip_ratio - 10)
                    print(f"[VideoWindow{self.stream_id}] 跳帧率: {self.frame_skip_ratio}%")
                
            except cv2.error as e:
                # 窗口被关闭或OpenCV错误
                print(f"[VideoWindow{self.stream_id}] OpenCV错误，窗口可能已关闭: {e}")
                self.is_running = False
                if self.on_close_callback:
                    try:
                        self.on_close_callback(self.stream_id)
                    except Exception as cb_err:
                        print(f"[VideoWindow{self.stream_id}] 关闭回调执行失败: {cb_err}")
                break
            except Exception as e:
                print(f"[VideoWindow{self.stream_id}] 显示线程错误: {e}")
                import traceback
                traceback.print_exc()
                self.is_running = False
                if self.on_close_callback:
                    try:
                        self.on_close_callback(self.stream_id)
                    except Exception as cb_err:
                        print(f"[VideoWindow{self.stream_id}] 关闭回调执行失败: {cb_err}")
                break
        
        # 退出循环后，销毁窗口
        print(f"[VideoWindow{self.stream_id}] 显示线程已退出，正在销毁窗口...")
        try:
            cv2.destroyWindow(self.window_name)
            cv2.waitKey(1)  # 触发窗口销毁
        except Exception as destroy_err:
            print(f"[VideoWindow{self.stream_id}] 销毁窗口时出错: {destroy_err}")
    
    def _draw_stats_overlay(self, frame):
        """在帧上绘制状态栏信息"""
        font = cv2.FONT_HERSHEY_SIMPLEX
        font_scale = 0.4
        font_color = (0, 255, 0)  # 绿色
        thickness = 1
        
        # 显示Stream ID
        cv2.putText(frame, f"Stream{self.stream_id}", 
                   (5, 15), font, font_scale, font_color, thickness)
        
        # 显示FPS、码率和原始分辨率
        res_text = f"{self.source_resolution[0]}x{self.source_resolution[1]}" if self.source_resolution else "--x--"
        fps_text = f"FPS: {self.current_fps:.1f} | Bitrate: {self.current_bitrate/1000:.0f} kbps"
        cv2.putText(frame, f"Src: {res_text}", 
               (5, 45), font, font_scale, font_color, thickness)
    
    def _create_stats_only_frame(self):
        """创建仅包含状态栏的帧（不显示视频）"""
        try:
            # 创建黑色背景
            stats_frame = np.zeros((self.WINDOW_HEIGHT, self.WINDOW_WIDTH, 3), dtype=np.uint8)
            
            font = cv2.FONT_HERSHEY_SIMPLEX
            font_scale = 0.5
            font_color = (0, 255, 0)
            thickness = 1
            
            # 居中显示信息
            y_offset = self.WINDOW_HEIGHT // 2 - 30
            
            # Stream ID
            text = f"Stream {self.stream_id}"
            cv2.putText(stats_frame, text, (self.WINDOW_WIDTH//2 - 60, y_offset), 
                       font, font_scale, font_color, thickness)
            
            # FPS
            text = f"FPS: {self.current_fps:.2f}"
            cv2.putText(stats_frame, text, (self.WINDOW_WIDTH//2 - 60, y_offset + 30), 
                       font, font_scale, font_color, thickness)
            
            # Bitrate
            text = f"Bitrate: {self.current_bitrate/1000:.2f} kbps"
            cv2.putText(stats_frame, text, (self.WINDOW_WIDTH//2 - 100, y_offset + 60), 
                       font, font_scale, font_color, thickness)
            
            # 原始分辨率
            res_text = f"Src: {self.source_resolution[0]}x{self.source_resolution[1]}" if self.source_resolution else "Src: --x--"
            cv2.putText(stats_frame, res_text, (self.WINDOW_WIDTH//2 - 100, y_offset + 90), 
                       font, 0.45, font_color, thickness)
            
            # 提示信息
            text = "(Video display disabled)"
            cv2.putText(stats_frame, text, (self.WINDOW_WIDTH//2 - 120, y_offset + 120), 
                       font, 0.4, (128, 128, 128), 1)
            
            return stats_frame
        except Exception as e:
            print(f"[VideoWindow{self.stream_id}] 创建状态帧失败: {e}")
            return np.zeros((self.WINDOW_HEIGHT, self.WINDOW_WIDTH, 3), dtype=np.uint8)
    
    def _create_placeholder_frame(self):
        """创建占位符框架（用于窗口等待数据时显示）"""
        try:
            # 创建黑色帧（使用固定尺寸）
            placeholder = np.zeros((self.WINDOW_HEIGHT, self.WINDOW_WIDTH, 3), dtype=np.uint8)
            # 添加文字
            font = cv2.FONT_HERSHEY_SIMPLEX
            text = "Waiting for frame data..."
            cv2.putText(placeholder, text, (self.WINDOW_WIDTH//2 - 150, self.WINDOW_HEIGHT//2), 
                       font, 0.6, (0, 255, 0), 2)
            return placeholder
        except Exception as e:
            print(f"[VideoWindow{self.stream_id}] 创建占位符帧失败: {e}")
            # 返回空白帧
            return np.zeros((self.WINDOW_HEIGHT, self.WINDOW_WIDTH, 3), dtype=np.uint8)
    
    def run(self):
        """兼容旧接口（实际显示在后台线程）"""
        # 已在__init__中启动显示线程
        pass
    
    def close(self):
        """关闭窗口"""
        print(f"[VideoWindow{self.stream_id}] 开始关闭窗口...")
        try:
            # 设置停止标志
            self.is_running = False
            
            # 先销毁窗口，帮助解除imshow/waitKey阻塞
            try:
                cv2.destroyWindow(self.window_name)
                cv2.waitKey(1)
            except Exception as cv_err:
                print(f"[VideoWindow{self.stream_id}] 销毁OpenCV窗口异常: {cv_err}")
            
            # 等待显示线程结束（短超时），超时后继续退出
            if self.thread and self.thread.is_alive():
                print(f"[VideoWindow{self.stream_id}] 等待显示线程结束...")
                self.thread.join(timeout=1.0)
                if self.thread.is_alive():
                    print(f"[VideoWindow{self.stream_id}] 警告: 显示线程未能在时限内结束，强制继续退出")
            
            print(f"[VideoWindow{self.stream_id}] 窗口已关闭")
        except Exception as e:
            print(f"[VideoWindow{self.stream_id}] 关闭窗口异常: {e}")
            import traceback
            traceback.print_exc()
