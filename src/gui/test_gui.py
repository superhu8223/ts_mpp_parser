#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
GUI模块 - 使用tkinter实现用户界面
"""

import tkinter as tk
from tkinter import ttk
import threading
from datetime import datetime
from typing import Optional, List, Dict
import numpy as np
import cv2
try:
    from PIL import Image, ImageTk
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False
    print("警告: 未安装PIL库，视频显示功能不可用。请运行: pip install Pillow")
import matplotlib
matplotlib.use('TkAgg')
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure
import matplotlib.dates as mdates

# 配置matplotlib支持中文显示
try:
    plt.rcParams['font.sans-serif'] = ['Microsoft YaHei', 'SimHei', 'Arial Unicode MS', 'DejaVu Sans']
    plt.rcParams['axes.unicode_minus'] = False  # 解决负号显示问题
except Exception:
    pass  # 如果配置失败，使用默认字体


class TestGUI:
    """测试GUI界面"""
    
    def __init__(self, task_name: str = "Test Task"):
        self.root = tk.Tk()
        self.root.title(task_name)
        self.root.geometry("1200x800")
        
        self.is_running = False
        self.gui_active = True  # GUI是否活跃，用于防止关闭后线程调用GUI
        self.test_engine = None
        self.stream_num = 0  # 视频流数量
        self.stream_stats: Dict[int, Dict] = {}  # 每路流的统计信息 {stream_id: {'fps': 0, 'bitrate': 0}}
        self.last_status_update_time = 0  # 状态栏最后更新时间，用于限制更新频率
        self.runtime_events: List = []  # 收集的runtime事件
        self.video_checkboxes: Dict[int, Dict] = {}  # 存储复选框 {stream_id: {'var': BooleanVar, 'checkbox': Checkbutton}}
        self.current_case_id = 0  # 当前执行的case ID
        self.current_case_name = None  # 当前执行的case名称
        
        self._create_widgets()
        
    def _create_widgets(self):
        """创建界面组件"""
        # 顶部控制区
        control_frame = tk.Frame(self.root)
        control_frame.pack(side=tk.TOP, fill=tk.X, padx=5, pady=5)
        
        self.start_button = tk.Button(
            control_frame,
            text="启动测试",
            command=self.on_start,
            bg="green",
            fg="white",
            font=("Arial", 12, "bold")
        )
        self.start_button.pack(side=tk.LEFT, padx=5)
        
        self.stop_button = tk.Button(
            control_frame,
            text="停止测试",
            command=self.on_stop,
            bg="red",
            fg="white",
            font=("Arial", 12, "bold"),
            state=tk.DISABLED
        )
        self.stop_button.pack(side=tk.LEFT, padx=5)
        
        self.status_label = tk.Label(
            control_frame,
            text="状态: 未开始",
            font=("Arial", 12)
        )
        self.status_label.pack(side=tk.LEFT, padx=20)
        
        # 视频流控制区（复选框）
        self.video_control_frame = tk.LabelFrame(
            control_frame,
            text="视频显示控制",
            font=("Arial", 10, "bold")
        )
        self.video_control_frame.pack(side=tk.LEFT, padx=20, fill=tk.X, expand=False)
        
        # RTSP协议控制区
        self.protocol_frame = tk.LabelFrame(
            control_frame,
            text="RTSP协议",
            font=("Arial", 10, "bold")
        )
        self.protocol_frame.pack(side=tk.LEFT, padx=10, fill=tk.X, expand=False)
        
        self.use_udp_var = tk.BooleanVar(value=False)  # 默认使用TCP
        self.udp_checkbox = tk.Checkbutton(
            self.protocol_frame,
            text="使用UDP",
            variable=self.use_udp_var,
            command=self._on_protocol_change,
            font=("Arial", 10)
        )
        self.udp_checkbox.pack(side=tk.LEFT, padx=5, pady=2)
        
        # 主内容区
        main_frame = tk.Frame(self.root)
        # 去掉右侧多余间距，使日志区域贴齐窗口右边
        main_frame.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=(5, 0), pady=5)
        
        # 配置列权重：左侧60%（图表），右侧40%（日志）
        main_frame.grid_columnconfigure(0, weight=6, minsize=400)
        main_frame.grid_columnconfigure(1, weight=4, minsize=300)
        main_frame.grid_rowconfigure(0, weight=1)
        
        # 左侧：实时统计图表区（原来的视频区域改为图表）
        chart_frame = tk.LabelFrame(main_frame, text="实时统计", font=("Arial", 10, "bold"))
        chart_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 5))
        
        # 创建matplotlib图表，使用GridSpec分割为3个子图
        from matplotlib.gridspec import GridSpec
        self.figure = Figure(figsize=(7, 8), dpi=80)
        gs = GridSpec(3, 1, figure=self.figure, height_ratios=[2, 2, 1.2], hspace=0.35)
        
        self.ax_bitrate = self.figure.add_subplot(gs[0])
        self.ax_fps = self.figure.add_subplot(gs[1], sharex=self.ax_bitrate)
        self.ax_mem = self.figure.add_subplot(gs[2], sharex=self.ax_bitrate)
        
        # 配置码率子图
        self.ax_bitrate.set_ylabel('Bitrate (kbps)', color='purple', fontsize=10, fontweight='bold')
        self.ax_bitrate.set_ylim(0, 6000)
        self.ax_bitrate.grid(True, alpha=0.2, linestyle='--')
        self.ax_bitrate.tick_params(axis='y', labelcolor='purple')
        
        # 配置帧率子图
        self.ax_fps.set_ylabel('FPS', color='blue', fontsize=10, fontweight='bold')
        self.ax_fps.set_ylim(0, 40)
        self.ax_fps.grid(True, alpha=0.2, linestyle='--')
        self.ax_fps.tick_params(axis='y', labelcolor='blue')
        
        # 配置内存子图
        self.ax_mem.set_ylabel('Free Mem (MB)', color='gray', fontsize=10, fontweight='bold')
        self.ax_mem.set_xlabel('Time', fontsize=10)
        self.ax_mem.set_ylim(0, 64)
        self.ax_mem.grid(True, alpha=0.2, linestyle='--')
        self.ax_mem.tick_params(axis='y', labelcolor='gray')
        
        self.canvas = FigureCanvasTkAgg(self.figure, chart_frame)
        self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)
        
        # 初始化数据
        self.fps_data = []
        self.bitrate_data = []
        self.mem_data = []  # 内存使用率数据
        self.time_data = []
        self.rtsp_handlers = None  # 保存rtsp_handlers供绘图使用
        
        # 右侧区域（40%宽度），上下分割
        right_frame = tk.Frame(main_frame)
        right_frame.grid(row=0, column=1, sticky="nsew")
        right_frame.grid_rowconfigure(0, weight=1)  # 串口日志占50%
        right_frame.grid_rowconfigure(1, weight=1)  # Launch日志占50%
        right_frame.grid_columnconfigure(0, weight=1)
        
        # 上半部分：串口日志
        serial_frame = tk.LabelFrame(right_frame, text="串口日志", font=("Arial", 10, "bold"))
        serial_frame.grid(row=0, column=0, sticky="nsew", padx=0, pady=(0, 2))
        
        self.serial_log_text = tk.Text(serial_frame, height=8, width=50, font=("Consolas", 8))
        self.serial_log_text.pack(fill=tk.BOTH, expand=True, padx=2, pady=2)
        
        # 下半部分：Launch日志
        launch_frame = tk.LabelFrame(right_frame, text="Launch日志", font=("Arial", 10, "bold"))
        launch_frame.grid(row=1, column=0, sticky="nsew", padx=0, pady=(2, 0))
        
        self.launch_log_text = tk.Text(launch_frame, height=8, width=50, font=("Consolas", 8))
        self.launch_log_text.pack(fill=tk.BOTH, expand=True, padx=2, pady=2)
        
    def on_start(self):
        """启动按钮回调"""
        # 重新激活GUI（用于多次启动/停止）
        self.gui_active = True
        
        self.is_running = True
        self.start_button.config(state=tk.DISABLED)
        self.stop_button.config(state=tk.NORMAL)
        self.status_label.config(text="状态: 运行中")
        
        # 隐藏RTSP协议控制区（测试运行中不允许修改协议）
        self.protocol_frame.pack_forget()
        
        if self.test_engine:
            # 在新线程中运行测试
            test_thread = threading.Thread(target=self.test_engine.run, daemon=True)
            test_thread.start()
    
    def init_video_controls(self, stream_count: int):
        """初始化视频流控制复选框"""
        # 清除旧的复选框（使用更强健的方式）
        try:
            for widget in list(self.video_control_frame.winfo_children()):
                try:
                    widget.destroy()
                except tk.TclError:
                    pass  # widget已被销毁
        except tk.TclError:
            pass  # frame已被销毁
        
        self.video_checkboxes.clear()
        
        # 为每个视频流创建复选框
        for i in range(stream_count):
            try:
                var = tk.BooleanVar(value=True)  # 默认选中
                cb = tk.Checkbutton(
                    self.video_control_frame,
                    text=f"Stream{i}",
                    variable=var,
                    command=lambda sid=i: self._on_video_checkbox_changed(sid),
                    font=("Arial", 9)
                )
                cb.pack(side=tk.LEFT, padx=5)
                self.video_checkboxes[i] = {'var': var, 'checkbox': cb}
            except tk.TclError:
                print(f"[GUI] 创建Stream{i}复选框失败（可能GUI已关闭）")
                break
        
        print(f"[GUI] 已初始化{stream_count}个视频流控制复选框")
    
    def init_ctrl_c_display(self):
        """bNeedCtrlC模式：初始化Ctrl+C次数显示"""
        # 清除旧的控件（使用更强健的方式）
        try:
            for widget in list(self.video_control_frame.winfo_children()):
                try:
                    widget.destroy()
                except tk.TclError:
                    pass  # widget已被销毁
        except tk.TclError:
            pass  # frame已被销毁
        
        self.video_checkboxes.clear()
        
        # 创建Ctrl+C次数显示标签
        try:
            self.ctrl_c_label = tk.Label(
                self.video_control_frame,
                text="Ctrl+C次数: 0",
                font=("Arial", 10, "bold"),
                fg="blue"
            )
            self.ctrl_c_label.pack(side=tk.LEFT, padx=10)
        except tk.TclError:
            print("[GUI] 创建Ctrl+C次数显示失败（可能GUI已关闭）")
        
        print("[GUI] 已初始化Ctrl+C次数显示")
    
    def update_ctrl_c_count(self, count: int):
        """bNeedCtrlC模式：更新Ctrl+C次数显示"""
        if not self.gui_active:
            return
        def _update():
            if self.gui_active and hasattr(self, 'ctrl_c_label'):
                self.ctrl_c_label.config(text=f"Ctrl+C次数: {count}")
        try:
            self.root.after(0, _update)
        except Exception:
            pass  # GUI已关闭
    
    def _on_video_checkbox_changed(self, stream_id: int):
        """复选框状态改变回调"""
        if stream_id not in self.video_checkboxes:
            return
        
        show_video = self.video_checkboxes[stream_id]['var'].get()
        print(f"[GUI] Stream{stream_id}视频显示: {show_video}")
        
        # 通知VideoWindow改变显示模式或重新创建窗口
        if self.test_engine and hasattr(self.test_engine, 'video_windows'):
            if show_video:
                # 选中：如果窗口不存在，重新创建
                if stream_id not in self.test_engine.video_windows:
                    print(f"[GUI] 重新创建 Stream{stream_id} 的视频窗口")
                    self.test_engine.recreate_video_window(stream_id)
                else:
                    # 窗口存在，启用视频显示
                    self.test_engine.video_windows[stream_id].set_show_video(True)
            else:
                # 取消选中：只隐藏视频内容，不关闭窗口
                if stream_id in self.test_engine.video_windows:
                    self.test_engine.video_windows[stream_id].set_show_video(False)
    
    def on_video_window_closed(self, stream_id: int):
        """视频窗口被关闭时的回调（用户点击X或按q）"""
        print(f"[GUI] 收到 Stream{stream_id} 窗口关闭通知")
        # 更新复选框状态（线程安全）
        def _update_checkbox():
            if stream_id in self.video_checkboxes:
                self.video_checkboxes[stream_id]['var'].set(False)
                print(f"[GUI] 已自动取消 Stream{stream_id} 复选框")
        self.root.after(0, _update_checkbox)
    
    def on_stop(self):
        """停止按钮回调"""
        print("[GUI] 停止按钮被点击...")
        
        # 立即设置标志，阻止后续线程调用GUI方法
        self.gui_active = False
        print("[GUI] gui_active已设置为False，阻止线程更新GUI")
        
        # 停止后台任务并关闭窗口；让关闭按钮也能触发
        if not self.root.winfo_exists():
            return

        self.stop_button.config(state=tk.DISABLED)
        self.start_button.config(state=tk.NORMAL)
        self.status_label.config(text="状态: 已停止")
        
        # 隐藏RTSP协议控制区和视频播放控制区
        try:
            self.protocol_frame.pack_forget()
            print("[GUI] 已隐藏RTSP协议控制区")
        except tk.TclError:
            pass  # frame已被销毁
        
        try:
            self.video_control_frame.pack_forget()
            print("[GUI] 已隐藏视频播放控制区")
        except tk.TclError:
            pass  # frame已被销毁

        if self.is_running and self.test_engine:
            print("[GUI] 停止测试引擎...")
            self.test_engine.stop()

        self.is_running = False
        
        # 关闭所有独立视频窗口
        if self.test_engine and hasattr(self.test_engine, 'video_windows'):
            print(f"[GUI] 关闭{len(self.test_engine.video_windows)}个视频窗口...")
            for stream_id, window in list(self.test_engine.video_windows.items()):
                try:
                    window.close()
                except Exception as e:
                    print(f"[GUI] 关闭视频窗口{stream_id}失败: {e}")
            self.test_engine.video_windows.clear()
        
        # 强制销毁所有OpenCV窗口
        try:
            import cv2
            cv2.destroyAllWindows()
            cv2.waitKey(1)
            print("[GUI] 已销毁所有OpenCV窗口")
        except Exception as cv_err:
            print(f"[GUI] 销毁OpenCV窗口异常: {cv_err}")
        
        # 销毁窗口以确保完全退出
        print("[GUI] 销毁主窗口...")
        self.root.after(100, self._force_destroy)
    
    def _force_destroy(self):
        """强制销毁窗口"""
        try:
            self.root.quit()  # 停止mainloop
            self.root.destroy()  # 销毁窗口
            print("[GUI] 主窗口已销毁")
        except Exception as e:
            print(f"[GUI] 销毁窗口失败: {e}")
    
    def update_title(self, title: str):
        """更新窗口标题"""
        self.root.title(title)

    def update_status(self, text: str):
        """线程安全地更新状态栏文本"""
        if not self.gui_active:
            return
        def _update():
            if self.gui_active:
                self.status_label.config(text=text)
        try:
            self.root.after(0, _update)
        except Exception:
            pass  # GUI已关闭

    def show_case_status(self, case_id: int, case_name: Optional[str] = None):
        """在状态栏显示当前执行的case信息"""
        # 保存当前case的信息用于update_case_time使用
        self.current_case_id = case_id
        self.current_case_name = case_name
        label = f"状态: 运行中 | Case {case_id}"
        if case_name:
            label += f" - {case_name}"
        self.update_status(label)
    
    def update_case_time(self, elapsed: int, hold_time):
        """更新状态栏显示当前case的剩余时间"""
        if not self.gui_active:
            return
        
        # 计算剩余时间
        if hold_time and isinstance(hold_time, (int, float)) and hold_time > 0:
            remaining = int(hold_time - elapsed)
            remaining = max(0, remaining)  # 确保不会显示负数
            label = f"状态: 运行中 | Case {self.current_case_id}"
            if self.current_case_name:
                label += f" - {self.current_case_name}"
            label += f" | 已用时: {elapsed}s | 剩余: {remaining}s"
        else:
            label = f"状态: 运行中 | Case {self.current_case_id}"
            if self.current_case_name:
                label += f" - {self.current_case_name}"
            label += f" | 已用时: {elapsed}s | 模式: 长稳"
        
        def _update():
            if self.gui_active:
                self.status_label.config(text=label)
        try:
            self.root.after(0, _update)
        except Exception:
            pass  # GUI已关闭

    def show_case_finished(self, case_id: int, case_name: Optional[str] = None):
        """在状态栏显示case完成信息"""
        label = f"状态: Case {case_id} 完成"
        if case_name:
            label += f" - {case_name}"
        self.update_status(label)
    
    def update_serial_log(self, text: str):
        """更新串口日志（线程安全）"""
        if not self.gui_active:
            return
        def _update():
            if self.gui_active:
                self.serial_log_text.insert(tk.END, text + '\n')
                self.serial_log_text.see(tk.END)
        try:
            self.root.after(0, _update)
        except Exception:
            pass  # GUI已关闭
    
    def update_launch_log(self, text: str):
        """更新launch日志（线程安全）"""
        if not self.gui_active:
            return
        def _update():
            if self.gui_active:
                self.launch_log_text.insert(tk.END, text + '\n')
                self.launch_log_text.see(tk.END)
        try:
            self.root.after(0, _update)
        except Exception:
            pass  # GUI已关闭
    
    def _on_protocol_change(self):
        """协议切换回调"""
        use_udp = self.use_udp_var.get()
        protocol = 'UDP' if use_udp else 'TCP'
        print(f"[GUI] RTSP传输协议切换为: {protocol}")
        
        # 如果测试正在运行，动态更新所有RTSP handler的协议设置
        if self.test_engine and hasattr(self.test_engine, 'rtsp_handlers'):
            for handler in self.test_engine.rtsp_handlers:
                if handler:
                    handler.set_transport(use_udp)
    
    def reset_chart(self):
        """重置图表数据（用于切换case时）"""
        print("重置实时统计图表...")
        self.time_data.clear()
        self.fps_data.clear()
        self.bitrate_data.clear()
        self.mem_data.clear()
        self.runtime_events.clear()
        
        # 清空所有子图但保留坐标轴配置
        self.ax_bitrate.clear()
        self.ax_bitrate.set_ylabel('Bitrate (kbps)', color='purple', fontsize=10, fontweight='bold')
        self.ax_bitrate.set_ylim(0, 6000)
        self.ax_bitrate.grid(True, alpha=0.2, linestyle='--')
        self.ax_bitrate.tick_params(axis='y', labelcolor='purple')
        
        self.ax_fps.clear()
        self.ax_fps.set_ylabel('FPS', color='blue', fontsize=10, fontweight='bold')
        self.ax_fps.set_ylim(0, 40)
        self.ax_fps.grid(True, alpha=0.2, linestyle='--')
        self.ax_fps.tick_params(axis='y', labelcolor='blue')
        
        self.ax_mem.clear()
        self.ax_mem.set_ylabel('Free Mem (MB)', color='gray', fontsize=10, fontweight='bold')
        self.ax_mem.set_xlabel('Time', fontsize=10)
        self.ax_mem.set_ylim(0, 64)
        self.ax_mem.grid(True, alpha=0.2, linestyle='--')
        self.ax_mem.tick_params(axis='y', labelcolor='gray')
        
        # 绘制空白图表，显示坐标轴
        if self.canvas and self.figure:
            try:
                self.canvas.draw()
            except Exception:
                pass  # 窗口已关闭，忽略错误
        print("实时统计图表已重置，等待新数据...")
    
    def update_chart(self, time_point, fps_values, bitrate_values, mem_usage=None, rtsp_handlers=None):
        """更新图表（三子图版本）"""
        if not self.gui_active:
            return
        
        try:
            # 调试：打印输入数据
            if len(self.time_data) < 5:
                print(f"[GUI] update_chart输入: time={time_point}, fps={fps_values}, bitrate={bitrate_values}, mem={mem_usage}")
            
            # 跳过所有bitrate都为0的数据点（程序启动初期的无效数据）
            if bitrate_values and all(v == 0 or v < 1 for v in bitrate_values):
                print(f"[GUI] 跳过无效数据：bitrate_values全为0或极小值")
                return
            
            self.time_data.append(time_point)
            self.fps_data.append(fps_values)
            self.bitrate_data.append(bitrate_values)
            self.mem_data.append(mem_usage if mem_usage is not None else 0)
            self.rtsp_handlers = rtsp_handlers  # 保存rtsp_handlers供绘图使用
            
            if len(self.time_data) <= 5:
                print(f"[GUI] update_chart: 已保存数据，当前time_data长度={len(self.time_data)}, bitrate_data={[d[0] if d else 0 for d in self.bitrate_data[-3:]]}")
            
            # 保持最多100个数据点
            if len(self.time_data) > 100:
                self.time_data.pop(0)
                self.fps_data.pop(0)
                self.bitrate_data.pop(0)
                self.mem_data.pop(0)
            
            # 至少有1个数据点即可绘制（状态栏与图表同步更新）
            if len(self.time_data) < 1:
                return
            
            # 检查图表对象是否有效
            if not self.canvas or not self.figure:
                return
            
            # 将时间转换为matplotlib日期
            def _to_mdate(tp):
                if isinstance(tp, datetime):
                    return mdates.date2num(tp)
                try:
                    return mdates.date2num(datetime.strptime(str(tp), '%H:%M:%S'))
                except Exception:
                    return None

            time_numeric = [_to_mdate(t) for t in self.time_data]
            time_numeric = [t for t in time_numeric if t is not None]

            if not time_numeric:
                self.canvas.draw()
                return

            # === 清除并重绘三个子图 ===
            self.ax_bitrate.clear()
            self.ax_fps.clear()
            self.ax_mem.clear()
            
            # 重新配置子图
            self.ax_bitrate.set_ylabel('Bitrate (kbps)', color='purple', fontsize=10, fontweight='bold')
            self.ax_bitrate.set_ylim(0, 6000)
            self.ax_bitrate.grid(True, alpha=0.2, linestyle='--')
            self.ax_bitrate.tick_params(axis='y', labelcolor='purple')
            
            self.ax_fps.set_ylabel('FPS', color='blue', fontsize=10, fontweight='bold')
            self.ax_fps.set_ylim(0, 40)
            self.ax_fps.grid(True, alpha=0.2, linestyle='--')
            self.ax_fps.tick_params(axis='y', labelcolor='blue')
            
            self.ax_mem.set_ylabel('Free Mem (MB)', color='gray', fontsize=10, fontweight='bold')
            self.ax_mem.set_xlabel('Time', fontsize=10)
            self.ax_mem.set_ylim(0, 64)
            self.ax_mem.grid(True, alpha=0.2, linestyle='--')
            self.ax_mem.tick_params(axis='y', labelcolor='gray')
            
            # 颜色映射
            from matplotlib import cm
            from matplotlib.colors import to_hex
            max_bitrate_value = 0.0
            
            # === 绘制码率子图（优先使用统计历史，避免启动期全0） ===
            if self.rtsp_handlers and len(self.rtsp_handlers) > 0:
                num_streams = len(self.rtsp_handlers)
                bitrate_cmap = cm.get_cmap('Purples')
                if num_streams > 1:
                    colors = [to_hex(bitrate_cmap(0.3 + 0.6 * i / (num_streams - 1))) for i in range(num_streams)]
                else:
                    colors = [to_hex(bitrate_cmap(0.7))]
                
                try:
                    has_history = False
                    for i, handler in enumerate(self.rtsp_handlers):
                        if handler and handler.stats and handler.stats.bitrate_history:
                            has_history = True
                            times = [mdates.date2num(h.timestamp) if isinstance(h.timestamp, datetime) else h.timestamp
                                    for h in handler.stats.bitrate_history]
                            vals = [h.value for h in handler.stats.bitrate_history]
                            if times and vals and len(times) == len(vals):
                                max_bitrate_value = max(max_bitrate_value, max(vals))
                                print(f"[GUI绘制] Bitrate Stream{i}: points={len(vals)}, range={min(vals):.0f}-{max(vals):.0f} kbps")
                                self.ax_bitrate.plot(times, vals, color=colors[i],
                                                    linewidth=2, marker='o', markersize=3,
                                                    label=f'Stream{i}', linestyle='-', alpha=0.8)
                    # 若统计历史为空，则使用缓存数据回退绘制
                    if not has_history and self.bitrate_data and len(self.bitrate_data) > 0 and len(self.bitrate_data[0]) > 0:
                        for i in range(len(self.bitrate_data[0])):
                            bitrate_series = [data[i] if i < len(data) else 0 for data in self.bitrate_data]
                            filtered_times = []
                            filtered_vals = []
                            for t, b in zip(time_numeric, bitrate_series):
                                if b > 1:
                                    filtered_times.append(t)
                                    filtered_vals.append(b)
                            if filtered_times and filtered_vals:
                                max_bitrate_value = max(max_bitrate_value, max(filtered_vals))
                                print(f"[GUI绘制] Bitrate Stream{i} (fallback): points={len(filtered_vals)}, range={min(filtered_vals):.0f}-{max(filtered_vals):.0f} kbps")
                                self.ax_bitrate.plot(filtered_times, filtered_vals, color=colors[i],
                                                    linewidth=2, marker='o', markersize=3,
                                                    label=f'Stream{i}', linestyle='-', alpha=0.8)
                except Exception as plot_err:
                    print(f"[GUI] 绘制Bitrate失败: {plot_err}")
            elif self.bitrate_data and len(self.bitrate_data) > 0 and len(self.bitrate_data[0]) > 0:
                # fallback：无handler时使用历史缓存数据
                num_streams = len(self.bitrate_data[0])
                bitrate_cmap = cm.get_cmap('Purples')
                if num_streams > 1:
                    colors = [to_hex(bitrate_cmap(0.3 + 0.6 * i / (num_streams - 1))) for i in range(num_streams)]
                else:
                    colors = [to_hex(bitrate_cmap(0.7))]
                
                try:
                    for i in range(num_streams):
                        bitrate_series = [data[i] if i < len(data) else 0 for data in self.bitrate_data]
                        filtered_times = []
                        filtered_vals = []
                        for t, b in zip(time_numeric, bitrate_series):
                            if b > 1:
                                filtered_times.append(t)
                                filtered_vals.append(b)
                        if filtered_times and filtered_vals:
                            max_bitrate_value = max(max_bitrate_value, max(filtered_vals))
                            self.ax_bitrate.plot(filtered_times, filtered_vals, color=colors[i],
                                                linewidth=2, marker='o', markersize=3,
                                                label=f'Stream{i}', linestyle='-', alpha=0.8)
                except Exception as plot_err:
                    print(f"[GUI] 绘制Bitrate失败: {plot_err}")
            
            # === 绘制帧率子图 ===
            if self.fps_data and len(self.fps_data) > 0 and len(self.fps_data[0]) > 0:
                num_streams = len(self.fps_data[0])
                fps_cmap = cm.get_cmap('Blues')
                if num_streams > 1:
                    colors = [to_hex(fps_cmap(0.3 + 0.6 * i / (num_streams - 1))) for i in range(num_streams)]
                else:
                    colors = [to_hex(fps_cmap(0.7))]
                
                try:
                    for i in range(num_streams):
                        fps_series = [data[i] if i < len(data) else 0 for data in self.fps_data]
                        print(f"[GUI绘制] FPS Stream{i}: points={len(fps_series)}, range={min(fps_series):.1f}-{max(fps_series):.1f} fps")
                        self.ax_fps.plot(time_numeric, fps_series, color=colors[i],
                                        linewidth=2, marker='o', markersize=3,
                                        label=f'Stream{i}', linestyle='-', alpha=0.8)
                except Exception as plot_err:
                    print(f"[GUI] 绘制FPS失败: {plot_err}")

            # 根据实际码率动态调整Y轴范围，避免超出默认上限导致曲线不可见
            if max_bitrate_value > 0:
                self.ax_bitrate.set_ylim(0, max_bitrate_value * 1.1)
            
            # === 绘制内存子图 ===
            if self.mem_data and len(self.mem_data) > 0:
                try:
                    # 内存单位从百分比改为MB
                    mem_series = [m for m in self.mem_data]
                    if len(mem_series) == len(time_numeric):
                        self.ax_mem.plot(time_numeric, mem_series, color='dimgray',
                                        linewidth=2.5, marker='s', markersize=3,
                                        label='Free Memory', linestyle='-', alpha=0.8)
                        self.ax_mem.fill_between(time_numeric, 0, mem_series, color='lightgray', alpha=0.3)
                        print(f"[GUI图表] 绘制了{len(mem_series)}个内存数据点")
                        
                        # 在内存图上标记ERROR和FATAL事件
                        from ..modules.models import EventLevel
                        for event in self.runtime_events:
                            if event.event_level in [EventLevel.ERROR, EventLevel.FATAL]:
                                try:
                                    event_time = mdates.date2num(event.timestamp)
                                    if time_numeric[0] <= event_time <= time_numeric[-1]:
                                        # 在事件时刻的内存值位置标记
                                        idx = min(range(len(time_numeric)), key=lambda i: abs(time_numeric[i] - event_time))
                                        y_val = mem_series[idx]
                                        marker_color = 'red' if event.event_level == EventLevel.FATAL else 'orange'
                                        marker_symbol = 'X' if event.event_level == EventLevel.FATAL else 'v'
                                        self.ax_mem.plot(event_time, y_val, marker=marker_symbol, markersize=10,
                                                        color=marker_color, markeredgecolor='black', markeredgewidth=1.5,
                                                        zorder=10)
                                except Exception as marker_err:
                                    print(f"[GUI] 标记事件失败: {marker_err}")
                except Exception as plot_err:
                    print(f"[GUI] 绘制Memory失败: {plot_err}")
            
            # 时间轴格式化
            self.ax_bitrate.xaxis.set_major_formatter(mdates.DateFormatter('%H:%M:%S'))
            self.ax_fps.xaxis.set_major_formatter(mdates.DateFormatter('%H:%M:%S'))
            self.ax_mem.xaxis.set_major_formatter(mdates.DateFormatter('%H:%M:%S'))
            
            # 设置x轴显示范围，确保所有数据点都可见
            if time_numeric and len(time_numeric) > 0:
                x_min = min(time_numeric)
                x_max = max(time_numeric)
                # 添加5%的边距，让数据点不紧贴边缘
                x_margin = (x_max - x_min) * 0.05 if x_max > x_min else 0.0001
                self.ax_bitrate.set_xlim(x_min - x_margin, x_max + x_margin)
                self.ax_fps.set_xlim(x_min - x_margin, x_max + x_margin)
                self.ax_mem.set_xlim(x_min - x_margin, x_max + x_margin)
            
            # 添加图例
            if self.ax_bitrate.get_legend_handles_labels()[0]:
                self.ax_bitrate.legend(loc='upper right', fontsize=8, framealpha=0.8)
            if self.ax_fps.get_legend_handles_labels()[0]:
                self.ax_fps.legend(loc='upper right', fontsize=8, framealpha=0.8)
            if self.ax_mem.get_legend_handles_labels()[0]:
                self.ax_mem.legend(loc='upper right', fontsize=8, framealpha=0.8)
            
            self.figure.autofmt_xdate()

            # 安全绘制，避免窗口关闭时的错误
            try:
                if self.canvas:
                    self.canvas.draw()
            except Exception:
                pass  # 窗口已关闭或无效，忽略错误
        
        except Exception as e:
            print(f"[GUI] update_chart异常: {e}")
            import traceback
            traceback.print_exc()

    def add_runtime_event(self, event):
        """增加一条runtime事件并触发后续绘制"""
        if not self.gui_active:
            return
        def _add():
            if self.gui_active:
                self.runtime_events.append(event)
                if len(self.runtime_events) > 200:
                    self.runtime_events.pop(0)
        try:
            self.root.after(0, _add)
        except Exception:
            pass  # GUI已关闭
    
    def get_latest_mem_usage(self) -> float:
        """获取最新的内存使用率"""
        if self.mem_data and len(self.mem_data) > 0:
            return self.mem_data[-1]
        return 0.0

    @staticmethod
    def _strip_device_timestamp(line: str) -> str:
        """移除设备端时间戳/前缀，只保留日志主体。"""
        if not line:
            return ""
        import re
        # 匹配形如 "[1970-01-01 00:00:36.282486]", "1970_01_01 00:00:36.282486", "[    0.000000]" 等
        patterns = [
            r'^\s*\[\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}\.\d+\]\s*',  # [1970-01-01 HH:MM:SS.millisec]
            r'^\s*\d{4}_\d{2}_\d{2}\s+\d{2}:\d{2}:\d{2}\.\d+\s*',  # 1970_01_01 HH:MM:SS.millisec
            r'^\s*\[\s*\d+\.\d+\]\s*'  # [    0.000000]
        ]
        cleaned = line
        for pat in patterns:
            cleaned = re.sub(pat, '', cleaned)
        return cleaned.strip()
    
    def set_test_engine(self, engine):
        """设置测试引擎"""
        self.test_engine = engine
    
    def update_stream_stats(self, stream_id: int, fps: float, bitrate: float):
        """更新流统计信息
        
        注：仅用于内部数据记录，不再更新状态栏（状态栏显示case信息）
        """
        self.stream_stats[stream_id] = {
            'fps': fps,
            'bitrate': bitrate
        }
        # 状态栏现在显示case名称和运行状态，不再显示帧率和码率
    
    def setup_video_display(self, stream_num: int):
        """设置视频显示区域（已移除，视频现在在独立窗口中）"""
        self.stream_num = stream_num
        # 初始化统计信息
        for i in range(stream_num):
            self.stream_stats[i] = {'fps': 0, 'bitrate': 0}
    
    def update_video_frame(self, stream_id: int, frame):
        """更新视频帧（已移除，视频现在在独立窗口中）"""
        pass
    
    def run(self):
        """运行GUI主循环"""
        try:
            self.root.protocol("WM_DELETE_WINDOW", self.on_stop)
            print("[GUI] mainloop启动...")
            self.root.mainloop()
            print("[GUI] mainloop已退出")
        except Exception as e:
            print(f"[GUI] mainloop异常: {type(e).__name__} - {e}")
            import traceback
            traceback.print_exc()
