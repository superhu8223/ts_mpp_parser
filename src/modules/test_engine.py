#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
测试引擎模块
负责执行测试任务的核心逻辑
"""

import os
import time
import threading
from datetime import datetime
from typing import List, Optional
from .models import TestTask, TestCase, RuntimeEvent, EventLevel
from .serial_handler import SerialHandler
from .telnet_handler import TelnetHandler
from .rtsp_handler import RTSPHandler
from .proc_parser import ProcParser
from .report_generator import ReportGenerator
from ..gui.video_window import VideoWindow


class TestEngine:
    """测试执行引擎"""
    
    # 类级别的烧录锁，所有 TestEngine 实例共享
    # 确保同一时间只有一个测试序列可以进行烧录
    _burn_lock = threading.Lock()
    
    def __init__(self, test_task: TestTask, logmap_config, base_dir: str = "record"):
        self.test_task = test_task
        self.logmap_config = logmap_config
        self.base_dir = base_dir
        self.is_running = False
        self.current_case: Optional[TestCase] = None
        
        # 处理器
        self.serial_handler: Optional[SerialHandler] = None
        self.launch_telnet: Optional[TelnetHandler] = None
        self.monitor_telnet: Optional[TelnetHandler] = None
        self.rtsp_handlers: List[RTSPHandler] = []
        self.video_windows: dict = {}  # 独立视频窗口字典 {stream_id: VideoWindow}
        self.mem_monitor_thread: Optional[threading.Thread] = None
        self.mem_monitor_running = False
        self.mem_monitor_stop_event = threading.Event()  # 用于立即停止内存采集线程
        
        # GUI回调
        self.gui = None
        
        # 烧录状态标志
        self.burn_completed = False  # 标记是否刚完成烧录
    
    def set_gui(self, gui):
        """设置GUI对象，用于日志回调"""
        self.gui = gui
        
    def run(self):
        """运行测试任务"""
        self.is_running = True
        self.test_task.start_time = datetime.now()
        
        try:
            # Step1: 准备阶段
            self._step1_prepare()
            
            # Step2: 烧录（如果需要且配置了烧录命令）
            if self.test_task.burn_flash and (self.test_task.fip_name or self.test_task.kernel_name):
                self._step2_burn()
            elif not self.test_task.burn_flash:
                print("跳过烧录阶段（burn_flash=false）")
                # 即使跳过烧录，也需要确保进入Linux并登录
                print("确保设备已进入Linux系统并完成登录...")
                if not self.serial_handler.enter_linux_mode():
                    print("警告：无法进入Linux模式")
            
            # Step3: 执行测试case列表
            print(f"[DEBUG] 开始case循环，总共{len(self.test_task.case_list)}个case，is_running={self.is_running}")
            for i, case in enumerate(self.test_task.case_list):
                print(f"\n{'='*70}")
                print(f"[Case循环] 准备执行 Case {i+1}/{len(self.test_task.case_list)}: {case.case_name}")
                print(f"[DEBUG] 当前is_running={self.is_running}")
                print(f"{'='*70}")
                if not self.is_running:
                    print(f"[Case循环] is_running={self.is_running}，停止执行")
                    break
                try:
                    print(f"[DEBUG] 正在调用_execute_case()...")
                    self._execute_case(case)
                    print(f"[Case循环] Case {i+1} 执行完成，is_running={self.is_running}")
                except BaseException as case_err:
                    # 捕获所有异常（包括系统异常），但继续执行下一个case
                    print(f"[Case循环] Case {i+1} 执行异常: {type(case_err).__name__}")
                    import traceback
                    traceback.print_exc()
                    print(f"[Case循环] 继续执行下一个case...")
            print(f"[DEBUG] case循环已结束，is_running={self.is_running}")
            
            # Step4: 生成task报告
            # 为当前task生成报告（无论current_case是否存在，都必须生成）
            try:
                self.test_task.end_time = datetime.now()
                print(f"[Task报告] 开始生成task报告")
                
                # 确定task_dir：优先使用self.test_task.task_dir，否则从current_case推导
                task_dir = self.test_task.task_dir
                if not task_dir and self.current_case:
                    # 从case_dir推导task_dir（case_dir = task_dir/case_N）
                    case_dir = self.current_case.case_dir
                    if case_dir:
                        task_dir = os.path.dirname(case_dir)
                
                print(f"[Task报告] task_dir={task_dir}")
                
                if task_dir and isinstance(task_dir, str) and os.path.isdir(task_dir):
                    print(f"[Task报告] task_dir有效，准备生成task_report.md...")
                    ReportGenerator.generate_task_report(self.test_task, task_dir)
                    print(f"[Task报告] task报告生成完成")
                else:
                    print(f"[Task报告] task_dir无效或不存在: {task_dir}")
            except Exception as task_report_err:
                print(f"[Task报告] 生成task报告失败: {task_report_err}")
                import traceback
                traceback.print_exc()
        except Exception as e:
            print(f"测试任务执行错误: {e}")
        finally:
            try:
                self._cleanup()
            except Exception as cleanup_err:
                print(f"清理资源时出错: {cleanup_err}")

    def _step1_prepare(self):
        """准备阶段"""
        try:
            # 创建task目录
            timestamp_str = datetime.now().strftime('%Y%m%d_%H%M%S')
            task_dir_name = f"{timestamp_str}_{self.test_task.task_name}"
            self.test_task.task_dir = os.path.join(self.base_dir, task_dir_name)
            os.makedirs(self.test_task.task_dir, exist_ok=True)
            
            print(f"开始测试任务: {task_dir_name}")
            print(f"Task目录: {self.test_task.task_dir}")
            
            # 为第一个case创建目录并设置日志文件（这样从烧录开始就记录日志）
            if self.test_task.case_list:
                first_case = self.test_task.case_list[0]
                case_dir_name = f"case_{first_case.case_id}"
                first_case.case_dir = os.path.join(self.test_task.task_dir, case_dir_name)
                os.makedirs(first_case.case_dir, exist_ok=True)
                print(f"为第一个case创建目录: {first_case.case_dir}")
                
                # 设置初始日志文件（烧录阶段的日志会写入这里）
                initial_log_file = os.path.join(first_case.case_dir, "serial.log")
                print(f"设置初始串口日志文件: {initial_log_file}")
            else:
                print("警告: case列表为空！")
            
            # 初始化串口
            self.serial_handler = SerialHandler(
                self.test_task.serial_config,
                self.logmap_config
            )
            
            # 设置GUI日志回调
            if self.gui:
                self.serial_handler.set_log_callback(self.gui.update_serial_log)
            
            if not self.serial_handler.connect():
                raise Exception("串口连接失败")
            
            # 启动串口监控并立即设置日志文件
            self.serial_handler.start_monitoring()
            
            # 设置初始日志文件
            if self.test_task.case_list:
                initial_log_file = os.path.join(self.test_task.case_list[0].case_dir, "serial.log")
                print(f"正在设置初始日志文件...")
                self.serial_handler.set_log_file(initial_log_file)
                print(f"初始日志文件已设置: {initial_log_file}")
            
        except Exception as e:
            print(f"准备阶段异常: {e}")
            import traceback
            traceback.print_exc()
            raise


    def _step2_burn(self):
        """烧录阶段"""
        # 获取全局烧录锁，确保同一时间只有一个序列在烧录
        print(f"[{self.test_task.task_name}] 等待烧录锁...")
        with TestEngine._burn_lock:
            print(f"[{self.test_task.task_name}] 获取烧录锁，开始烧录...")
            
            # 进入uboot模式
            if not self.serial_handler.enter_uboot_mode():
                print("警告：无法进入uboot模式，跳过烧录")
                return
            
            # 【重要】先设置 uboot 环境变量 fip_name 和 kernel_name
            # 这是烧录的前置步骤，不依赖于 task.cfg 中的 ubootCmd
            # 使用 setenv 命令而不是 editenv（editenv 是交互式编辑器）
            if self.test_task.fip_name:
                setenv_fip = f"setenv fip_name {self.test_task.fip_name}"
                print(f"设置 fip_name: {setenv_fip}")
                self.serial_handler.send_command(setenv_fip)
                time.sleep(1)
            
            if self.test_task.kernel_name:
                setenv_kernel = f"setenv kernel_name {self.test_task.kernel_name}"
                print(f"设置 kernel_name: {setenv_kernel}")
                self.serial_handler.send_command(setenv_kernel)
                time.sleep(1)
            
            # 保存环境变量到 flash（可选，但建议添加）
            if self.test_task.fip_name or self.test_task.kernel_name:
                print("保存环境变量到 flash")
                self.serial_handler.send_command("saveenv")
                time.sleep(2)
            
            # 烧录顺序：fip -> kernel -> rootfs -> userfs
            
            # 1. 烧录FIP
            if self.test_task.burn_fip_cmd:
                print(f"烧录FIP: {self.test_task.burn_fip_cmd}")
                self.serial_handler.send_command(self.test_task.burn_fip_cmd)
                time.sleep(10)  # 等待烧录完成
            
            # 2. 烧录Kernel
            if self.test_task.burn_kernel_cmd:
                print(f"烧录Kernel: {self.test_task.burn_kernel_cmd}")
                self.serial_handler.send_command(self.test_task.burn_kernel_cmd)
                time.sleep(10)
            
            # 3. 烧录Rootfs
            if self.test_task.burn_rootfs_cmd:
                print(f"烧录Rootfs: {self.test_task.burn_rootfs_cmd}")
                self.serial_handler.send_command(self.test_task.burn_rootfs_cmd)
                time.sleep(15)
            
            # 4. 烧录Userfs
            if self.test_task.burn_userfs_cmd and self.test_task.userfs_name:
                print(f"烧录Userfs: {self.test_task.burn_userfs_cmd}")
                self.serial_handler.send_command(self.test_task.burn_userfs_cmd)
                time.sleep(10)
            
            # 重启
            print("烧录完成，重启设备...")
            self.serial_handler.send_command("reset\r\n")
            
            # 等待进入Linux系统
            print("等待系统启动...")
            time.sleep(30)  # 等待重启完成
            
            # 进入Linux环境
            self.serial_handler.enter_linux_mode()
            
            print(f"[{self.test_task.task_name}] 烧录完成，释放烧录锁")
            self.burn_completed = True  # 标记烧录完成
        # with 语句结束时自动释放锁
        print("等待系统启动...")
        time.sleep(30)  # 等待重启完成
        
        # 进入Linux环境
        self.serial_handler.enter_linux_mode()
    
    def _execute_case(self, test_case: TestCase):
        """执行单个测试case"""
        print(f"\n{'='*60}")
        print(f"开始执行 Case {test_case.case_id}: {test_case.case_name}")
        print(f"[DEBUG] case holdtime={test_case.hold_time}, is_longterm={test_case.is_longterm}")
        print(f"{'='*60}\n")
        
        self.current_case = test_case
        test_case.start_time = datetime.now()

        if self.gui:
            try:
                self.gui.show_case_status(test_case.case_id, test_case.case_name)
            except Exception as gui_status_err:
                print(f"更新GUI状态失败: {gui_status_err}")
        
        # 创建case目录
        case_dir_name = f"case_{test_case.case_id}"
        test_case.case_dir = os.path.join(self.test_task.task_dir, case_dir_name)
        os.makedirs(test_case.case_dir, exist_ok=True)
        print(f"Case目录已创建: {test_case.case_dir}")
        
        # 重置GUI实时统计窗口
        if self.gui:
            try:
                self.gui.reset_chart()
            except Exception as e:
                print(f"重置图表失败: {e}")
        
        # 初始化内存历史
        test_case.free_mem_history = []
        test_case.isp_history = []
        test_case.vi_history = []
        test_case.vpss_history = []
        test_case.venc_history = []
        
        # 用于FATAL事件提前终止当前case，但不影响后续 case
        case_should_stop = False
        
        # 添加事件回调
        def on_event(event: RuntimeEvent):
            nonlocal case_should_stop
            test_case.event_list.append(event)
            print(f"[EVENT] {event}")
            if self.gui:
                try:
                    self.gui.add_runtime_event(event)
                except Exception as gui_event_err:
                    print(f"GUI事件推送失败: {gui_event_err}")
            
            # 检测FATAL事件，设置当前case停止标志
            if event.event_level == EventLevel.FATAL:
                print(f"[FATAL事件] 检测到FATAL级别事件: {event.event_name}，将提前终止当前case")
                case_should_stop = True
        
        try:
            # 如果当前case的日志文件已经在_step1_prepare中设置过（第一个case），则跳过
            # 否则为后续case切换日志文件（必须在重启之前设置，这样重启日志才能被正确保存）
            if test_case != self.test_task.case_list[0]:
                serial_log_file = os.path.join(test_case.case_dir, "serial.log")
                print(f"切换到case {test_case.case_id}的日志文件: {serial_log_file}")
                # 为后续case创建新的干净日志文件（clear_existing=True）
                self.serial_handler.set_log_file(serial_log_file, clear_existing=True)
            
            # 判断是否需要reboot
            need_reboot = self._need_reboot(test_case)
            
            if need_reboot:
                self._do_reboot(test_case)
            
            self.serial_handler.add_event_callback(on_event)
            
            # 在串口执行preCmd（如果有且未执行过）
            # preCmd应包含所有环境准备工作（网络配置、NFS挂载、文件复制、telnetd启动等）
            if test_case.pre_cmd and not test_case.preCmd_executed:
                print(f"[DEBUG] 通过串口执行preCmd，preCmd_executed={test_case.preCmd_executed}")
                self.serial_handler.send_command(test_case.pre_cmd, wait_time=0.5)
                print(f"[DEBUG] preCmd发送完成，等待环境准备...")
                time.sleep(5)  # 等待NFS挂载、文件复制等操作完成
                test_case.preCmd_executed = True
                print(f"[DEBUG] preCmd执行完成，设置preCmd_executed={test_case.preCmd_executed}")
            else:
                print(f"[DEBUG] 跳过preCmd执行: pre_cmd存在={bool(test_case.pre_cmd)}, preCmd_executed={test_case.preCmd_executed}")
            
            # 启动launchThread
            print("[DEBUG] 准备启动launch线程...")
            self._start_launch_thread(test_case, on_event)
            print("[DEBUG] launch线程已启动")
            
            # 启动rtspThread
            print("[DEBUG] 准备启动rtsp线程...")
            self._start_rtsp_threads(test_case, on_event)
            print(f"[DEBUG] 启动了{len(self.rtsp_handlers)}路rtsp")
            
            # 启动monitorThread（bNeedCtrlC模式下不需要启动）
            if not test_case.b_need_ctrl_c:
                print("[DEBUG] 准备启动monitor线程...")
                self._start_monitor_thread(test_case, on_event)
                print("[DEBUG] monitor线程已启动")
            else:
                print("[DEBUG] bNeedCtrlC模式，跳过monitor线程启动")
            
            # 等待holdTime或用户停止
            print("[DEBUG] 所有子线程已启动，准备调用_wait_for_completion...")
            print(f"[DEBUG] 当前时刻即将等待: test_case.hold_time={test_case.hold_time}, is_longterm={test_case.is_longterm}, is_running={self.is_running}")
            self._wait_for_completion(test_case, lambda: case_should_stop)
            print("[DEBUG] _wait_for_completion已返回")
            
            # 生成case报告
            test_case.end_time = datetime.now()
            
            # 调试信息：检查rtsp_handlers
            print(f"[生成报告] self.rtsp_handlers长度={len(self.rtsp_handlers)}")
            for idx, handler in enumerate(self.rtsp_handlers):
                fps_count = len(handler.stats.fps_history) if handler.stats.fps_history else 0
                bitrate_count = len(handler.stats.bitrate_history) if handler.stats.bitrate_history else 0
                print(f"[生成报告] rtsp_handlers[{idx}]: stream_id={handler.stream_id}, fps_count={fps_count}, bitrate_count={bitrate_count}, first_frame_time={handler.stats.first_frame_time}")
            
            # 如果rtsp_handlers已被清空（stop()被调用），使用保存的rtsp_stats_list
            if test_case.rtsp_stats_list:
                stats_list = test_case.rtsp_stats_list
                print(f"[生成报告] 使用保存的rtsp_stats_list，长度={len(stats_list)}")
            else:
                stats_list = [h.stats for h in self.rtsp_handlers]
            
            print(f"[生成报告] stats_list长度={len(stats_list)}")
            for idx, stats in enumerate(stats_list):
                fps_count = len(stats.fps_history) if stats.fps_history else 0
                bitrate_count = len(stats.bitrate_history) if stats.bitrate_history else 0
                print(f"[生成报告] stats_list[{idx}]: stream_id={stats.stream_id}, fps_count={fps_count}, bitrate_count={bitrate_count}")
            
            ReportGenerator.generate_case_report(
                test_case,
                stats_list,
                test_case.free_mem_history,
                test_case.case_dir
            )
            
        except Exception as e:
            print(f"[ERROR] Case执行错误: {e}")
            import traceback
            traceback.print_exc()
        finally:
            print(f"[DEBUG] _execute_case进入finally块，is_running={self.is_running}")
            if self.gui:
                try:
                    self.gui.show_case_finished(test_case.case_id, test_case.case_name)
                except Exception as gui_status_err:
                    print(f"[WARNING] 更新GUI状态失败: {gui_status_err}")
            
            # 无条件执行清理，即使出现任何异常也继续
            try:
                self._cleanup_case()
            except BaseException as cleanup_err:
                # 捕获所有异常（包括系统异常和信号）
                print(f"[WARNING] 清理case资源出错: {type(cleanup_err).__name__} - {cleanup_err}")
                import traceback
                traceback.print_exc()
                # 继续执行，不抛出异常
            
            print(f"[DEBUG] _execute_case的finally块执行完成，即将返回，is_running={self.is_running}")

    def _start_launch_thread(self, test_case: TestCase, event_callback):
        """启动launch线程"""
        # 启动launch线程（telnet监控）
        launch_log_file = os.path.join(test_case.case_dir, "launch.log")
        self.launch_telnet = TelnetHandler(
            self.test_task.evb_ip,
            self.test_task.evb_port,
            self.logmap_config,
            log_type="launch"
        )

        if not self.launch_telnet.connect():
            print("[ERROR] launch_telnet连接失败")
            return

        # 设置GUI日志回调
        if self.gui:
            self.launch_telnet.set_log_callback(self.gui.update_launch_log)

        self.launch_telnet.add_event_callback(event_callback)
        self.launch_telnet.start_monitoring(launch_log_file)

        # 等待登录提示符出现
        print("等待登录提示符...")
        if not self.launch_telnet.wait_for_pattern("login:", timeout=10.0):
            print("警告：未检测到登录提示符")

        # 登录
        print("发送登录命令: root")
        self.launch_telnet.send_command("root", wait_time=0.3)

        # 设置ISP日志
        isp_cmd = "export ISP_CMD_LOG_PARAM='log:levelMask=0x4444444434,ispMask=0xffffffffff,sw3aMask=0x7,logPath=/tmp/log.txt'"
        print(f"发送ISP日志配置: {isp_cmd}")
        self.launch_telnet.send_command(isp_cmd, wait_time=0.5)

        # 执行runCmd
        print(f"执行runCmd: {test_case.run_cmd}")
        if isinstance(test_case.run_cmd, list):
            for cmd in test_case.run_cmd:
                self.launch_telnet.send_command(cmd, wait_time=0.5)
        else:
            self.launch_telnet.send_command(test_case.run_cmd, wait_time=0.5)
        time.sleep(1)  # 等待启动

    def _need_reboot(self, test_case: TestCase) -> bool:
        """判断当前case是否需要重启"""
        if test_case.b_need_reboot is not None:
            return test_case.b_need_reboot
        return bool(self.test_task.each_case_need_reboot)

    def _do_reboot(self, test_case: TestCase):
        """执行重启流程（含可选的uboot命令）"""
        print(f"[重启] 执行case {test_case.case_id}的重启流程...")

        # 如果有uboot命令，需要进入uboot执行
        if test_case.uboot_cmd:
            print(f"[重启] case包含uboot命令，进入uboot模式执行...")
            if not self.serial_handler.enter_uboot_mode():
                print("[重启] 警告：无法进入uboot模式，跳过uboot命令")
            else:
                for cmd in test_case.uboot_cmd:
                    print(f"[重启] uboot命令: {cmd}")
                    self.serial_handler.send_command(cmd)
                    time.sleep(1)

                # 执行完uboot命令后启动系统
                print("[重启] uboot命令执行完毕，启动系统...")
                self.serial_handler.send_command("boot")
                time.sleep(2)
        else:
            # 无uboot命令时，直接重启到Linux
            print("[重启] 无uboot命令，发送reboot...")
            self.serial_handler.send_command("reboot\r\n")
            time.sleep(30)

        # 进入Linux环境
        print("[重启] 等待系统进入Linux...")
        self.serial_handler.enter_linux_mode()
    
    def _start_rtsp_threads(self, test_case: TestCase, event_callback):
        """启动RTSP拉流线程"""
        from src.gui.video_window import VideoWindow
        
        print(f"启动{test_case.stream_num}路RTSP拉流...")
        
        # 简化模式（bNeedCtrlC）不需要设置GUI视频显示区域
        if self.gui and not test_case.b_need_ctrl_c:
            self.gui.setup_video_display(test_case.stream_num)
        
        self.rtsp_handlers = []
        self.video_windows = {}  # 存储独立视频窗口
        resolution_unified = [False]  # 标记分辨率是否已统一（使用列表以在闭包中修改）
        
        for i in range(test_case.stream_num):
            url = f"rtsp://{self.test_task.evb_ip}/live_{i}"
            print(f"[Engine] 创建Stream{i}，URL: {url}")
            # 从GUI获取UDP/TCP协议设置
            use_udp = False
            if self.gui and hasattr(self.gui, 'use_udp_var'):
                use_udp = self.gui.use_udp_var.get()
            # bNeedCtrlC模式使用简化模式：只检测是否收到数据，不解码显示
            handler = RTSPHandler(i, url, simplified_mode=test_case.b_need_ctrl_c, use_udp=use_udp)
            print(f"[Engine] Stream{i}的RTSPHandler已创建，stream_id={handler.stream_id}")
            handler.add_event_callback(event_callback)
            
            # 简化模式下不需要设置视频帧回调和视频窗口回调
            if not test_case.b_need_ctrl_c:
                # 设置视频帧回调（使用默认参数捕获当前stream_id）
                if self.gui:
                    frame_call_count = [0]  # 使用列表来追踪调用次数
                    callback_time_sum = [0.0]  # 回调链总耗时
                    callback_time_max = [0.0]  # 回调链最大耗时
                    last_callback_log_time = [time.time()]  # 上次输出日志时间
                    last_chart_update_time = [time.time()]  # 上次更新图表的时间
                    
                    def frame_callback_wrapper(stream_id, frame, fps, bitrate, sid=i):
                        callback_start = time.time()
                        # 统计调用次数
                        frame_call_count[0] += 1
                        # 每30帧打印一次，避免刷屏（包括第1, 31, 61...帧）
                        if frame_call_count[0] == 1 or frame_call_count[0] % 30 == 1:
                            print(f"[Stream{sid}] frame_callback调用 #{frame_call_count[0]}: fps={fps:.2f}, bitrate={bitrate:.0f}, frame_shape={frame.shape if hasattr(frame, 'shape') else 'N/A'}")
                        
                        # 在第一帧时，收集分辨率信息
                        if self.rtsp_handlers[sid].stats.frame_width is None and hasattr(frame, 'shape') and len(frame.shape) >= 2:
                            height, width = frame.shape[:2]
                            self.rtsp_handlers[sid].stats.frame_width = width
                            self.rtsp_handlers[sid].stats.frame_height = height
                            print(f"[Stream{sid}] 分辨率已设定: {width}x{height}")
                            
                            # 检查所有流是否都已收集分辨率，并且还没有统一过
                            if not resolution_unified[0]:
                                all_resolutions_ready = all(
                                    h.stats.frame_width is not None 
                                    for h in self.rtsp_handlers
                                )
                                if all_resolutions_ready:
                                    resolution_unified[0] = True  # 标记为已统一
                                    # 找出最小分辨率
                                    min_width = min(h.stats.frame_width for h in self.rtsp_handlers)
                                    min_height = min(h.stats.frame_height for h in self.rtsp_handlers)
                                    print(f"[Engine] 所有流分辨率已收集，最小分辨率: {min_width}x{min_height}")
                                    
                                    # 统一所有视频窗口到最小分辨率（避免字典大小改变错误）
                                    windows_snapshot = list(self.video_windows.items())
                                    for window_id, window in windows_snapshot:
                                        try:
                                            window.set_window_size(min_width, min_height)
                                            print(f"[Engine] 视频窗口{window_id}已调整大小: {min_width}x{min_height}")
                                        except Exception as resize_err:
                                            print(f"[Engine] 视频窗口{window_id}调整大小失败: {resize_err}")
                        
                        # 更新视频帧
                        self.gui.update_video_frame(sid, frame)
                        
                        # 仅当fps或bitrate > 0时才更新统计信息（跳过占位符更新）
                        if fps > 0 or bitrate > 0:
                            # 优化: 降低图表更新频率，每5秒更新一次（matplotlib绘图耗时较高）
                            current_time = time.time()
                            if current_time - last_chart_update_time[0] >= 5.0:
                                last_chart_update_time[0] = current_time
                                try:
                                    fps_values = [h.get_latest_fps() for h in self.rtsp_handlers]
                                    bitrate_values = [h.get_latest_bitrate() for h in self.rtsp_handlers]
                                    # 状态栏统计也改为5秒更新一次，使用统计周期数据
                                    for idx, handler in enumerate(self.rtsp_handlers):
                                        try:
                                            self.gui.update_stream_stats(handler.stream_id, fps_values[idx], bitrate_values[idx])
                                        except Exception as stats_err:
                                            print(f"更新状态栏统计失败: {stats_err}")
                                    # 获取最新的空闲内存（MB）
                                    free_mem_mb = 0
                                    if test_case.free_mem_history:
                                        latest_mem = test_case.free_mem_history[-1]
                                        free_mem_mb = latest_mem.get('free', 0) / 1024  # KB转MB
                                    # 传入rtsp_handlers让GUI直接读取history
                                    self.gui.update_chart(datetime.now(), fps_values, bitrate_values, free_mem_mb, self.rtsp_handlers)
                                except Exception as chart_err:
                                    print(f"更新实时统计失败: {chart_err}")
                        
                        # 更新独立视频窗口
                        if sid in self.video_windows:
                            try:
                                self.video_windows[sid].update_frame(frame)
                                # 仅当fps或bitrate > 0时才更新窗口上的统计信息
                                if fps > 0 or bitrate > 0:
                                    self.video_windows[sid].update_stats(fps, bitrate)
                            except Exception as window_err:
                                print(f"[Stream{sid}] 更新视频窗口失败: {window_err}")
                        # 窗口创建中或已销毁，静默跳过
                        
                        # 统计回调链耗时
                        callback_cost = (time.time() - callback_start) * 1000.0
                        callback_time_sum[0] += callback_cost
                        callback_time_max[0] = max(callback_time_max[0], callback_cost)
                        
                        # 每5秒输出回调链性能统计
                        if time.time() - last_callback_log_time[0] >= 5.0:
                            if frame_call_count[0] > 0:
                                avg_callback = callback_time_sum[0] / frame_call_count[0]
                                print(f"[性能-Callback{sid}] 近5秒回调链: 平均{avg_callback:.2f}ms, 最大{callback_time_max[0]:.2f}ms, 调用{frame_call_count[0]}次")
                                # 重置统计
                                callback_time_sum[0] = 0.0
                                callback_time_max[0] = 0.0
                                frame_call_count[0] = 0
                                last_callback_log_time[0] = time.time()
                    
                    print(f"[Engine] 为Stream{i}设置frame_callback...")
                    handler.set_frame_callback(frame_callback_wrapper)
                    print(f"[Engine] Stream{i}的frame_callback已设置")
                
                # 设置视频窗口创建回调（使用默认参数捕获当前stream_id）
                def window_callback_wrapper(stream_id, sid=i):
                    try:
                        print(f"[Stream{sid}] 准备创建独立视频窗口...")
                        url_str = f"rtsp://{self.test_task.evb_ip}/live_{sid}"
                        
                        # ✅ 修复: 在主线程创建Tkinter窗口
                        def create_in_main_thread():
                            try:
                                print(f"[Stream{sid}] 在主线程创建VideoWindow对象...")
                                # 传入关闭回调
                                def on_window_close(stream_id):
                                    print(f"[Engine] 窗口{stream_id}被关闭，从字典中移除")
                                    if stream_id in self.video_windows:
                                        del self.video_windows[stream_id]
                                    # 通知GUI更新复选框
                                    if self.gui and hasattr(self.gui, 'on_video_window_closed'):
                                        self.gui.on_video_window_closed(stream_id)
                                
                                window = VideoWindow(sid, url_str, self.test_task.task_name, on_close_callback=on_window_close)
                                print(f"[Stream{sid}] VideoWindow对象创建成功")
                                self.video_windows[sid] = window
                                print(f"[Stream{sid}] 窗口已创建 (字典大小={len(self.video_windows)})")
                                
                                # 如果分辨率已经统一过，立即调整这个新窗口的大小
                                if resolution_unified[0]:
                                    all_widths = [h.stats.frame_width for h in self.rtsp_handlers if h.stats.frame_width is not None]
                                    all_heights = [h.stats.frame_height for h in self.rtsp_handlers if h.stats.frame_height is not None]
                                    if all_widths and all_heights:
                                        min_width = min(all_widths)
                                        min_height = min(all_heights)
                                        print(f"[Stream{sid}] 窗口创建后应用已统一的分辨率: {min_width}x{min_height}")
                                        window.set_window_size(min_width, min_height)
                            except Exception as window_err:
                                print(f"[Stream{sid}] VideoWindow创建异常: {window_err}")
                                import traceback
                                traceback.print_exc()
                        
                        # 通过GUI主线程的事件循环创建窗口
                        if self.gui and hasattr(self.gui, 'root'):
                            self.gui.root.after(0, create_in_main_thread)
                        else:
                            print(f"[Stream{sid}] 警告: GUI主窗口不可用，无法创建视频窗口")
                        
                    except Exception as e:
                        print(f"[Stream{sid}] 创建视频窗口回调失败: {e}")
                        import traceback
                        traceback.print_exc()
                
                # 设置视频窗口回调
                handler.set_video_window_callback(window_callback_wrapper)
            
            handler.start(test_case.case_dir)
            self.rtsp_handlers.append(handler)
            print(f"[Engine] Stream{i}已添加到处理器列表，共{len(self.rtsp_handlers)}个")
            time.sleep(0.5)
        
        # 等待一段时间后检查是否全部收流成功
        time.sleep(10)
        
        # 通知GUI初始化视频控制复选框或Ctrl+C次数显示
        if self.gui and hasattr(self.gui, 'init_video_controls'):
            stream_count = len(self.rtsp_handlers)
            if test_case.b_need_ctrl_c:
                # bNeedCtrlC模式：显示Ctrl+C次数而不是复选框
                self.gui.init_ctrl_c_display()
                print(f"[Engine] 已通知GUI初始化Ctrl+C次数显示")
            else:
                # 普通模式：显示视频流复选框
                self.gui.init_video_controls(stream_count)
                print(f"[Engine] 已通知GUI初始化{stream_count}个视频流控制复选框")
        
        # 检查RTSP收流状态（仅记录，不主动创建窗口）
        print("[Engine] 检查RTSP收流状态...")
        for i, handler in enumerate(self.rtsp_handlers):
            if handler.stats.first_frame_time is None:
                print(f"[Engine] Stream{i}未收到第一帧，等待收流...")
            else:
                print(f"[Engine] Stream{i}已正常收流")
        
        all_success = all(h.stats.first_frame_time is not None for h in self.rtsp_handlers)
        if all_success:
            event = RuntimeEvent(
                line_log="所有rtsp收流成功",
                event_level=EventLevel.INFO,
                event_name="收流成功",
                timestamp=datetime.now()
            )
            event_callback(event)
        
        # 最终调试信息：总结所有启动的handlers
        print(f"[Engine] _start_rtsp_threads完成总结:")
        print(f"[Engine] self.rtsp_handlers总数={len(self.rtsp_handlers)}")
        for idx, h in enumerate(self.rtsp_handlers):
            print(f"[Engine]   [{idx}] stream_id={h.stream_id}, url={h.url}, simplified_mode={h.simplified_mode}")
    
    def _start_monitor_thread(self, test_case: TestCase, event_callback):
        """启动monitor线程"""
        print("启动monitor线程...")
        
        # 清理上一个case的monitor_telnet（如果存在）
        if self.monitor_telnet:
            try:
                print("[DEBUG] 清理上一个case的monitor_telnet...")
                self.monitor_telnet.disconnect()
            except Exception as e:
                print(f"[DEBUG] 清理monitor_telnet失败: {e}")
            self.monitor_telnet = None
        
        monitor_log_file = os.path.join(test_case.case_dir, "monitorThread.log")
        self.monitor_telnet = TelnetHandler(
            self.test_task.evb_ip,
            self.test_task.evb_port,
            self.logmap_config,
            log_type="serial"
        )
        
        if not self.monitor_telnet.connect():
            return
        
        self.monitor_telnet.start_monitoring(monitor_log_file)
        
        # 等待登录提示符出现
        print("等待登录提示符...")
        if not self.monitor_telnet.wait_for_pattern("login:", timeout=10.0):
            print("警告：未检测到登录提示符")
        
        # 登录
        print("发送登录命令: root")
        self.monitor_telnet.send_command("root", wait_time=1.0)
        
        # 等待登录完成（等待shell提示符）
        time.sleep(2)

        # 启动内存监控线程
        self.mem_monitor_running = True
        self.mem_monitor_thread = threading.Thread(
            target=self._collect_free_mem_loop,
            args=(test_case,),
            daemon=True
        )
        self.mem_monitor_thread.start()
        
        # 将视频通路检查和debug动作在后台线程中异步执行，不阻塞主流程
        def monitor_bg_task():
            print("[后台监控] 等待15秒后进行第一次检查...")
            time.sleep(15)
            self._check_video_pipeline(test_case, test_case.run_cmd_checks, event_callback)
            
            # 如果是功能验证模式，执行debugActions
            if not test_case.is_longterm and test_case.debug_actions:
                self._execute_debug_actions(test_case, event_callback)
            print("[后台监控] 检查和调试动作完成")
        
        monitor_bg_thread = threading.Thread(target=monitor_bg_task, daemon=True)
        monitor_bg_thread.start()
        print("monitor线程已启动（异步模式）")
    
    def _check_video_pipeline(self, test_case: TestCase, check_spec, event_callback):
        """检查视频通路"""
        if not check_spec:
            return
        
        print("检查视频通路...")
        
        # 检查monitor_telnet是否已初始化
        if not self.monitor_telnet:
            print("[警告] monitor_telnet未初始化，跳过视频通路检查")
            return
        
        # 获取proc信息
        vi_output = self.monitor_telnet.execute_command("cat /proc/mpp/vi")
        vpss_output = self.monitor_telnet.execute_command("cat /proc/mpp/vpss")
        venc_output = self.monitor_telnet.execute_command("cat /proc/mpp/venc")
        
        # 解析信息（这里简化实现，实际需要更详细的检查）
        vi_info = ProcParser.parse_vi_info(vi_output)
        vpss_info = ProcParser.parse_vpss_info(vpss_output)
        venc_info = ProcParser.parse_venc_info(venc_output)
        
        # 检查是否满足规格（简化版）
        check_passed = True
        
        # TODO: 实现详细的规格检查逻辑
        
        if not check_passed:
            event = RuntimeEvent(
                line_log="拉流数据异常",
                event_level=EventLevel.EMERGE,
                event_name="拉流数据异常",
                timestamp=datetime.now()
            )
            event_callback(event)
    
    def _execute_debug_actions(self, test_case: TestCase, event_callback):
        """执行调试动作"""
        for action in test_case.debug_actions:
            print(f"等待{action.time_gap}秒...")
            time.sleep(action.time_gap)
            
            print(f"执行action: {action.action_cmd}")
            # TODO: 需要通过debug_telnet执行st_debug_client
            
            # 检查action效果
            self._check_video_pipeline(test_case, action.action_checks, event_callback)
    
    def _wait_for_completion(self, test_case: TestCase, should_stop_func=None):
        """等待完成
        
        Args:
            test_case: 当前case
            should_stop_func: 可选的callable，返回True时提前终止case
        """
        print(f"[DEBUG] _wait_for_completion开始: is_longterm={test_case.is_longterm}, hold_time={test_case.hold_time}, b_need_ctrl_c={test_case.b_need_ctrl_c}")
        
        # 检查是否需要周期性 Ctrl+C 循环
        if test_case.b_need_ctrl_c:
            print("启用 Ctrl+C 循环模式，将在所有RTSP流收到数据后立即发送Ctrl+C并重启拉流...")
            self._wait_with_ctrl_c_loop(test_case)
        elif test_case.is_longterm:
            print("长稳烤机模式，等待用户停止...")
            while self.is_running:
                time.sleep(1)
        else:
            # 保险检查：确保hold_time有有效值
            if test_case.hold_time is None or (isinstance(test_case.hold_time, (int, float)) and test_case.hold_time <= 0):
                print(f"[WARNING] hold_time无效: {test_case.hold_time}，使用默认值100")
                test_case.hold_time = 100
            
            print(f"等待{test_case.hold_time}秒... (is_running={self.is_running})")
            start_time = time.time()
            elapsed = 0
            while self.is_running and elapsed < test_case.hold_time:
                # 检查是否因FATAL事件需要提前终止
                if should_stop_func and should_stop_func():
                    print(f"[DEBUG] 检测到should_stop信号，提前终止等待")
                    break
                elapsed = time.time() - start_time
                
                # 更新GUI显示剩余时间（每秒更新一次）
                if self.gui and hasattr(self.gui, 'update_case_time'):
                    try:
                        self.gui.update_case_time(int(elapsed), test_case.hold_time)
                    except Exception as e:
                        print(f"[DEBUG] 更新GUI剩余时间失败: {e}")
                
                print(f"[DEBUG] 等待中: 已等待{elapsed:.1f}秒/{test_case.hold_time}秒")
                time.sleep(1)
            print(f"[DEBUG] 等待完成: 总共等待了{elapsed:.1f}秒")
    
    def _wait_with_ctrl_c_loop(self, test_case: TestCase):
        """周期性 Ctrl+C 循环拉流模式 - 只有用户主动停止或fatal事件才退出"""
        cycle_count = 0
        
        while self.is_running:
            cycle_count += 1
            test_case.ctrl_c_count = cycle_count  # 记录发送次数
            
            # 更新GUI显示的Ctrl+C次数
            if self.gui and hasattr(self.gui, 'update_ctrl_c_count'):
                self.gui.update_ctrl_c_count(cycle_count)
            
            print(f"\n{'='*60}")
            print(f"[Ctrl+C循环] 第 {cycle_count} 轮，等待所有RTSP流收到数据后发送 Ctrl+C...")
            print(f"{'='*60}")
            
            # 等待所有RTSP流都收到第一帧数据
            start_time = time.time()
            max_wait_time = 60  # 最长等待60秒
            all_streams_ready = False
            
            while self.is_running and (time.time() - start_time) < max_wait_time:
                # 检查所有RTSP handler是否都收到了第一帧
                all_ready = all(h.stats.first_frame_time is not None for h in self.rtsp_handlers)
                
                if all_ready:
                    elapsed = time.time() - start_time
                    print(f"[Ctrl+C循环] 轮次 {cycle_count}: 所有RTSP流已收到数据 (耗时 {elapsed:.1f}秒)")
                    all_streams_ready = True
                    break
                
                # 每2秒检查一次状态
                time.sleep(2)
                elapsed = time.time() - start_time
                ready_count = sum(1 for h in self.rtsp_handlers if h.stats.first_frame_time is not None)
                if int(elapsed) % 10 < 2:  # 每10秒打印一次
                    print(f"[Ctrl+C循环] 轮次 {cycle_count}: 等待收流中... ({ready_count}/{len(self.rtsp_handlers)} 已就绪, 已等待 {elapsed:.1f}秒)")
            
            if not self.is_running:
                print("[Ctrl+C循环] 收到用户停止信号，退出循环")
                break
            
            if not all_streams_ready:
                print(f"[Ctrl+C循环] 轮次 {cycle_count}: 警告：等待超时，部分RTSP流未收到数据")
                # 记录warning事件但继续执行
                event = RuntimeEvent(
                    line_log=f"RTSP收流超时 (轮次{cycle_count})",
                    event_level=EventLevel.WARNING,
                    event_name="收流超时",
                    timestamp=datetime.now()
                )
                test_case.event_list.append(event)
            
            # 检查是否有fatal级别的事件
            has_fatal = any(e.event_level == EventLevel.FATAL for e in test_case.event_list)
            if has_fatal:
                print("[Ctrl+C循环] 检测到FATAL级别事件，退出循环")
                break
            
            # ===== 第1步：停止 RTSP 收流线程 =====
            print(f"[Ctrl+C循环] 轮次 {cycle_count}: 停止 RTSP 收流...")
            for i, handler in enumerate(self.rtsp_handlers):
                try:
                    print(f"[Ctrl+C循环] 停止 RTSP 处理器 #{i} (stream_id={handler.stream_id})...")
                    handler.stop()
                except Exception as e:
                    print(f"[Ctrl+C循环] 停止 RTSP 处理器#{i}失败: {e}")
            
            # ===== 第2步：发送 Ctrl+C 停止拉流程序 =====
            print(f"[Ctrl+C循环] 轮次 {cycle_count}: 发送 Ctrl+C 停止拉流程序...")
            try:
                if self.launch_telnet:
                    # 在 telnet 日志中记录 Ctrl+C 操作
                    log_msg = f"\n========== [Ctrl+C循环-轮次{cycle_count}] 发送 Ctrl+C ==========\n"
                    if hasattr(self.launch_telnet, 'log_file_handle') and self.launch_telnet.log_file_handle:
                        self.launch_telnet.log_file_handle.write(log_msg)
                        self.launch_telnet.log_file_handle.flush()
                    
                    self.launch_telnet.send_ctrl_c()
                    time.sleep(2)  # 等待程序退出
                    print(f"[Ctrl+C循环] 轮次 {cycle_count}: Ctrl+C已发送")
                else:
                    print("[Ctrl+C循环] 错误: launch_telnet不可用")
                    # 记录fatal事件
                    event = RuntimeEvent(
                        line_log="launch_telnet不可用",
                        event_level=EventLevel.FATAL,
                        event_name="系统错误",
                        timestamp=datetime.now()
                    )
                    test_case.event_list.append(event)
                    break
            except Exception as e:
                print(f"[Ctrl+C循环] 发送 Ctrl+C 失败: {e}")
                # 记录fatal事件
                event = RuntimeEvent(
                    line_log=f"发送Ctrl+C失败: {e}",
                    event_level=EventLevel.FATAL,
                    event_name="系统错误",
                    timestamp=datetime.now()
                )
                test_case.event_list.append(event)
                break
            
            # 等待 2 秒，确保程序完全退出
            time.sleep(2)
            
            # ===== 第3步：重新启动拉流程序 =====
            print(f"[Ctrl+C循环] 轮次 {cycle_count}: 重新发送 runCmd 启动拉流...")
            try:
                if self.launch_telnet:
                    # 在 telnet 日志中记录重启操作
                    log_msg = f"\n========== [Ctrl+C循环-轮次{cycle_count}] 重新启动拉流 ==========\n"
                    if hasattr(self.launch_telnet, 'log_file_handle') and self.launch_telnet.log_file_handle:
                        self.launch_telnet.log_file_handle.write(log_msg)
                        self.launch_telnet.log_file_handle.flush()
                    
                    print(f"执行runCmd: {test_case.run_cmd}")
                    self.launch_telnet.send_command(test_case.run_cmd, wait_time=1.5)
                    time.sleep(3)  # 等待启动
                    print(f"[Ctrl+C循环] 轮次 {cycle_count}: 拉流程序已重新启动")
                else:
                    print("[Ctrl+C循环] 错误: launch_telnet不可用")
                    break
            except Exception as e:
                print(f"[Ctrl+C循环] 重新启动拉流失败: {e}")
                # 记录fatal事件
                event = RuntimeEvent(
                    line_log=f"重新启动拉流失败: {e}",
                    event_level=EventLevel.FATAL,
                    event_name="系统错误",
                    timestamp=datetime.now()
                )
                test_case.event_list.append(event)
                break
            
            # ===== 第4步：重新启动 RTSP 收流线程（简化模式）=====
            print(f"[Ctrl+C循环] 轮次 {cycle_count}: 重新启动 RTSP 收流线程（简化模式）...")
            self.rtsp_handlers.clear()  # 清空旧的处理器列表
            
            try:
                # 获取事件回调函数
                def on_event(event):
                    test_case.event_list.append(event)
                    # bNeedCtrlC模式下也要及时检查fatal事件
                    if event.event_level == EventLevel.FATAL:
                        print(f"[Ctrl+C循环] 检测到FATAL事件: {event.line_log}")
                
                # 重新启动 RTSP 线程（使用简化模式）
                self._start_rtsp_threads(test_case, on_event)
                print(f"[Ctrl+C循环] 轮次 {cycle_count}: RTSP 收流线程已重新启动")
            except Exception as e:
                print(f"[Ctrl+C循环] 重新启动 RTSP 收流失败: {e}")
                import traceback
                traceback.print_exc()
                # 记录fatal事件
                event = RuntimeEvent(
                    line_log=f"重新启动RTSP收流失败: {e}",
                    event_level=EventLevel.FATAL,
                    event_name="系统错误",
                    timestamp=datetime.now()
                )
                test_case.event_list.append(event)
                break
        
        print(f"[Ctrl+C循环] 退出循环，共执行了 {cycle_count} 轮")
    
    def _cleanup_case(self):
        """清理case资源"""
        print("清理case资源...")
        
        try:
            # 第一步：停止内存采集线程（防止继续发送telnet命令）
            try:
                print("停止内存采集线程...")
                self.mem_monitor_running = False
                self.mem_monitor_stop_event.set()
                if self.mem_monitor_thread and self.mem_monitor_thread.is_alive():
                    print("等待内存采集线程结束（最多2秒）...")
                    self.mem_monitor_thread.join(timeout=2)
                    if self.mem_monitor_thread.is_alive():
                        print("[WARNING] 内存采集线程未能在时限内结束")
                self.mem_monitor_thread = None
                self.mem_monitor_stop_event.clear()
            except Exception as e:
                print(f"[WARNING] 停止内存采集出错: {type(e).__name__}")
            
            # 第二步：停止RTSP（释放拉流资源）
            try:
                print(f"停止{len(self.rtsp_handlers)}个RTSP处理线程...")
                
                import threading
                stop_threads = []
                for i, handler in enumerate(self.rtsp_handlers):
                    def stop_handler_in_thread(idx, h):
                        try:
                            print(f"正在停止RTSP处理器 #{idx} (stream_id={h.stream_id})...")
                            h.stop()
                            print(f"RTSP处理器 #{idx} 已停止")
                        except BaseException as e:
                            print(f"[WARNING] 停止RTSP处理器#{idx}失败: {type(e).__name__}")
                    
                    t = threading.Thread(target=stop_handler_in_thread, args=(i, handler), daemon=True)
                    t.daemon = True
                    t.start()
                    stop_threads.append((i, t))
                
                # 等待所有stop线程完成，每个最多等8秒（给VideoCapture充足时间释放）
                for idx, t in stop_threads:
                    t.join(timeout=8)
                    if t.is_alive():
                        print(f"[WARNING] RTSP处理器 #{idx} 停止超时8秒，强制继续")
                
                self.rtsp_handlers.clear()
                print("所有RTSP处理线程已停止")
            except Exception as e:
                print(f"[WARNING] 停止RTSP出错: {type(e).__name__}")
            
            # 第三步：关闭视频窗口
            try:
                print(f"关闭case相关的视频窗口...（当前有{len(self.video_windows)}个窗口）")
                self._close_video_windows()
                print("视频窗口已全部关闭")
            except Exception as e:
                print(f"[WARNING] 关闭视频窗口出错: {type(e).__name__}")
            
            # 第四步：关闭串口日志
            try:
                if self.serial_handler and self.current_case:
                    print(f"关闭串口日志文件")
                    if self.serial_handler.log_file_handle:
                        self.serial_handler.log_file_handle.flush()
                        self.serial_handler.log_file_handle.close()
                        self.serial_handler.log_file_handle = None
                        print("串口日志文件已关闭并保存")
            except Exception as e:
                print(f"[WARNING] 关闭串口日志出错: {type(e).__name__}")
            
            # 第五步：停止launch telnet
            try:
                if self.launch_telnet:
                    self.launch_telnet.send_ctrl_c()
                    time.sleep(2)
                    self.launch_telnet.disconnect()
                    self.launch_telnet = None
                    print("launch telnet已断开")
            except Exception as e:
                print(f"[WARNING] 停止launch telnet出错: {type(e).__name__}")
            
            # 第六步：收集ISP日志
            try:
                if self.monitor_telnet:
                    print("从monitor线程获取ISP日志...")
                    isp_log = self.monitor_telnet.execute_command("cat /tmp/log.txt")
                    if isp_log and self.current_case:
                        isp_log_file = os.path.join(self.current_case.case_dir, "ispLog.txt")
                        with open(isp_log_file, 'w', encoding='utf-8') as f:
                            f.write(isp_log)
                        print(f"ISP日志已保存")
            except Exception as e:
                print(f"[WARNING] 获取ISP日志出错: {type(e).__name__}")
        
        finally:
            print("[DEBUG] _cleanup_case()执行完毕")
    
    def _cleanup(self):
        """清理资源"""
        print("清理资源...")
        # 关闭所有独立视频窗口
        self._close_video_windows()
        
        # 断开monitor_telnet（如果仍然存在）
        if self.monitor_telnet:
            try:
                print("断开monitor telnet连接...")
                self.monitor_telnet.disconnect()
            except Exception as e:
                print(f"断开monitor telnet失败: {e}")
            self.monitor_telnet = None
        
        # 断开launch_telnet（如果仍然存在）
        if self.launch_telnet:
            try:
                print("断开launch telnet连接...")
                self.launch_telnet.disconnect()
            except Exception as e:
                print(f"断开launch telnet失败: {e}")
            self.launch_telnet = None
        
        # 确保串口日志被保存
        if self.serial_handler:
            print("保存串口日志...")
            self.serial_handler.disconnect()
            self.serial_handler = None

        # 停止内存监控
        self.mem_monitor_running = False
        if self.mem_monitor_thread and self.mem_monitor_thread.is_alive():
            self.mem_monitor_thread.join(timeout=2)
        self.mem_monitor_thread = None

    def _close_video_windows(self):
        """关闭并清空独立视频窗口"""
        print(f"[DEBUG] _close_video_windows 开始，窗口数={len(self.video_windows)}")
        
        import threading
        
        # 异步关闭窗口，避免阻塞清理流程
        for stream_id in list(self.video_windows.keys()):
            window = self.video_windows.get(stream_id)
            if not window:
                continue
            
            print(f"[DEBUG] 请求关闭Stream{stream_id}窗口...")
            
            try:
                # 优先通过GUI主线程关闭窗口
                if self.gui and hasattr(self.gui, 'root') and self.gui.root:
                    try:
                        self.gui.root.after(0, window.close)
                        print(f"[DEBUG] Stream{stream_id}窗口关闭请求已提交")
                    except Exception as gui_err:
                        print(f"[DEBUG] 提交窗口关闭请求失败: {gui_err}")
                else:
                    # 没有GUI时，主线程直接关闭
                    if threading.current_thread() is threading.main_thread():
                        window.close()
                        print(f"[DEBUG] Stream{stream_id}窗口已在主线程关闭")
                    else:
                        # 非主线程时仅标记，避免阻塞
                        window.is_closed = True
                        window.is_playing = False
                        print(f"[DEBUG] Stream{stream_id}窗口已标记关闭")
            except Exception as e:
                print(f"[ERROR] 关闭视频窗口{stream_id}失败: {e}")
        
        self.video_windows.clear()
        print(f"[DEBUG] _close_video_windows 完成，已清空窗口字典")
    
    def recreate_video_window(self, stream_id: int):
        """重新创建视频窗口"""
        if stream_id in self.video_windows:
            print(f"[Engine] Stream{stream_id}的窗口已存在，无需重建")
            return
        
        if stream_id >= len(self.rtsp_handlers):
            print(f"[Engine] 警告: Stream{stream_id}的RTSP处理器不存在")
            return
        
        print(f"[Engine] 重新创建 Stream{stream_id} 的视频窗口")
        try:
            url_str = f"rtsp://{self.test_task.evb_ip}/live_{stream_id}"
            
            # 创建关闭回调
            def on_window_close(sid):
                print(f"[Engine] 窗口{sid}被关闭，从字典中移除")
                if sid in self.video_windows:
                    del self.video_windows[sid]
                # 通知GUI更新复选框
                if self.gui and hasattr(self.gui, 'on_video_window_closed'):
                    self.gui.on_video_window_closed(sid)
            
            window = VideoWindow(stream_id, url_str, self.test_task.task_name, on_close_callback=on_window_close)
            self.video_windows[stream_id] = window
            print(f"[Engine] Stream{stream_id} 窗口重建成功")
            
            # 设置帧回调，让窗口开始接收帧数据
            handler = self.rtsp_handlers[stream_id]
            
            # 创建frame_callback_wrapper
            frame_call_count = [0]
            def frame_callback_wrapper(stream_id_arg, frame, fps, bitrate):
                frame_call_count[0] += 1
                if frame_call_count[0] == 1 or frame_call_count[0] % 30 == 1:
                    print(f"[Stream{stream_id}] frame_callback调用 #{frame_call_count[0]}: fps={fps:.2f}, bitrate={bitrate:.0f}, frame_shape={frame.shape if hasattr(frame, 'shape') else 'N/A'}")
                
                # 在第一帧时，收集分辨率信息
                if self.rtsp_handlers[stream_id].stats.frame_width is None and hasattr(frame, 'shape') and len(frame.shape) >= 2:
                    height, width = frame.shape[:2]
                    self.rtsp_handlers[stream_id].stats.frame_width = width
                    self.rtsp_handlers[stream_id].stats.frame_height = height
                    print(f"[Stream{stream_id}] 分辨率已设定: {width}x{height}")
                    
                    # 检查所有流是否都已收集分辨率
                    all_resolutions_ready = all(
                        h.stats.frame_width is not None 
                        for h in self.rtsp_handlers
                    )
                    if all_resolutions_ready:
                        # 找出最小分辨率
                        min_width = min(h.stats.frame_width for h in self.rtsp_handlers)
                        min_height = min(h.stats.frame_height for h in self.rtsp_handlers)
                        print(f"[Engine] 所有流分辨率已收集，最小分辨率: {min_width}x{min_height}")
                        
                        # 统一所有视频窗口到最小分辨率（避免字典大小改变错误）
                        windows_snapshot = list(self.video_windows.items())
                        for window_id, window in windows_snapshot:
                            try:
                                window.set_window_size(min_width, min_height)
                                print(f"[Engine] 视频窗口{window_id}已调整大小: {min_width}x{min_height}")
                            except Exception as resize_err:
                                print(f"[Engine] 视频窗口{window_id}调整大小失败: {resize_err}")
                
                try:
                    if stream_id in self.video_windows:
                        self.video_windows[stream_id].update_frame(frame)
                        self.video_windows[stream_id].update_stats(fps, bitrate)
                except Exception as e:
                    if frame_call_count[0] <= 3:
                        print(f"[Stream{stream_id}] update_frame失败: {e}")
            
            handler.set_frame_callback(frame_callback_wrapper)
            print(f"[Engine] Stream{stream_id} 的frame_callback已重新设置")
            
        except Exception as e:
            print(f"[Engine] 重建 Stream{stream_id} 窗口失败: {e}")
            import traceback
            traceback.print_exc()

    def _collect_free_mem_loop(self, test_case: TestCase):
        """定期采集free内存信息"""
        import re
        print("[内存监控] 内存采集线程已启动")
        consecutive_failures = 0  # 连续失败次数
        max_failures = 3  # 最大连续失败次数
        
        while self.mem_monitor_running and self.monitor_telnet:
            try:
                # 检查telnet连接状态
                if not self.monitor_telnet.telnet:
                    print("[内存监控] 警告: monitor_telnet连接已断开")
                    time.sleep(5)
                    continue
                
                # 优先使用/proc/meminfo，更加可靠
                output = self.monitor_telnet.execute_command("cat /proc/meminfo", wait_time=1.0)  # 增加等待时间
                
                if not output or len(output.strip()) == 0:
                    consecutive_failures += 1
                    print(f"[内存监控] 警告: /proc/meminfo返回空 (连续失败{consecutive_failures}次)")
                    
                    if consecutive_failures >= max_failures:
                        print(f"[内存监控] 连续失败{consecutive_failures}次，尝试重新登录...")
                        # 尝试发送回车激活终端
                        self.monitor_telnet.send_command("", wait_time=0.5)
                        consecutive_failures = 0
                    
                    time.sleep(5)
                    continue
                
                print(f"[内存监控] /proc/meminfo输出长度: {len(output)} 字节")
                if len(output) < 100:
                    print(f"[内存监控] /proc/meminfo原始输出:\n{repr(output)}")
                
                mem_total = None
                mem_available = None
                
                # 解析/proc/meminfo格式: MemTotal:        1024000 kB
                for line in output.split('\n'):
                    line = line.strip()
                    if line.startswith('MemTotal:'):
                        try:
                            parts = re.split(r'\s+', line)
                            mem_total = int(parts[1])  # 值
                            print(f"[内存监控] 解析MemTotal: {mem_total} KB")
                        except (ValueError, IndexError) as e:
                            print(f"[内存监控] MemTotal解析失败: {e}, line={repr(line)}")
                    elif line.startswith('MemAvailable:'):
                        try:
                            parts = re.split(r'\s+', line)
                            mem_available = int(parts[1])  # 值
                            print(f"[内存监控] 解析MemAvailable: {mem_available} KB")
                        except (ValueError, IndexError) as e:
                            print(f"[内存监控] MemAvailable解析失败: {e}, line={repr(line)}")
                
                # 如果没有MemAvailable，尝试用free
                if mem_available is None and mem_total:
                    print(f"[内存监控] 没有MemAvailable，尝试使用free命令...")
                    free_output = self.monitor_telnet.execute_command("free", wait_time=1.0)
                    print(f"[内存监控] free输出长度: {len(free_output)} 字节")
                    
                    if free_output and len(free_output.strip()) > 0:
                        for line in free_output.split('\n'):
                            line = line.strip()
                            if 'Mem:' in line:
                                parts = re.split(r'\s+', line)
                                print(f"[内存监控] free解析: {parts}")
                                if len(parts) >= 4 and parts[0] == 'Mem:':
                                    try:
                                        mem_available = int(parts[3])  # free字段
                                        print(f"[内存监控] 从free提取可用内存: {mem_available} KB")
                                    except (ValueError, IndexError) as e:
                                        print(f"[内存监控] free解析失败: {e}")
                                break
                    else:
                        print(f"[内存监控] free命令也返回空")
                
                if mem_total and mem_available is not None:
                    consecutive_failures = 0  # 重置失败计数
                    usage = (1 - mem_available / mem_total) * 100 if mem_total else 0
                    current_time = datetime.now()
                    sample = {
                        "timestamp": current_time,
                        "free": mem_available,
                        "total": mem_total,
                        "usage": usage
                    }
                    test_case.free_mem_history.append(sample)
                    print(f"[内存监控] 采集成功: Free: {mem_available/1024:.2f}MB, Usage: {usage:.2f}%, 历史记录数: {len(test_case.free_mem_history)}")
                    
                    # 同时采集proc数据
                    try:
                        # 采集ISP统计数据
                        isp_output = self.monitor_telnet.execute_command("cat /proc/isp_stat", wait_time=0.5)
                        if isp_output and len(isp_output.strip()) > 0:
                            isp_info = ProcParser.parse_isp_stat(isp_output)
                            if isp_info:
                                isp_info["timestamp"] = current_time
                                test_case.isp_history.append(isp_info)
                                print(f"[ISP监控] 采集成功: frameid={isp_info.get('frame_id')}, fps={isp_info.get('fps', 0):.2f}, 历史记录数: {len(test_case.isp_history)}")
                        
                        # 采集VI数据 - 可能有多个pipe，逐个存储
                        vi_output = self.monitor_telnet.execute_command("cat /proc/mpp/vi", wait_time=0.5)
                        if vi_output and len(vi_output.strip()) > 0:
                            vi_result = ProcParser.parse_vi_info(vi_output)
                            if vi_result and 'pipes' in vi_result and vi_result['pipes']:
                                for pipe_info in vi_result['pipes']:
                                    pipe_record = {
                                        'timestamp': current_time,
                                        'pipe_id': pipe_info.get('pipe_id', 0),
                                        'fps': pipe_info.get('fps', 0),
                                        'width': pipe_info.get('width', 0),
                                        'height': pipe_info.get('height', 0)
                                    }
                                    test_case.vi_history.append(pipe_record)
                                print(f"[VI监控] 采集成功: {len(vi_result['pipes'])}个pipe, 历史记录数: {len(test_case.vi_history)}")
                        
                        # 采集VPSS数据 - 可能有多个group/channel，逐个存储
                        vpss_output = self.monitor_telnet.execute_command("cat /proc/mpp/vpss", wait_time=0.5)
                        if vpss_output and len(vpss_output.strip()) > 0:
                            vpss_result = ProcParser.parse_vpss_info(vpss_output)
                            print(f"[VPSS调试] parse_vpss_info返回: {vpss_result}")
                            if vpss_result and 'groups' in vpss_result and vpss_result['groups']:
                                for group_info in vpss_result['groups']:
                                    for channel_info in group_info.get('channels', []):
                                        vpss_record = {
                                            'timestamp': current_time,
                                            'group_id': group_info.get('group_id', 0),
                                            'channel_id': channel_info.get('channel_id', 0),
                                            'fps': channel_info.get('fps', 0),
                                            'send_ok': channel_info.get('send_ok', 0),  # 添加SendOk字段
                                            'out_width': channel_info.get('width', 0),
                                            'out_height': channel_info.get('height', 0)
                                        }
                                        test_case.vpss_history.append(vpss_record)
                                print(f"[VPSS监控] 采集成功: {len(vpss_result['groups'])}个group, 历史记录数: {len(test_case.vpss_history)}")
                            else:
                                print(f"[VPSS调试] vpss_result为空或groups为空")
                        
                        # 采集VENC数据 - 可能有多个channel
                        venc_output = self.monitor_telnet.execute_command("cat /proc/mpp/venc", wait_time=0.5)
                        if venc_output and len(venc_output.strip()) > 0:
                            venc_result = ProcParser.parse_venc_info(venc_output)
                            print(f"[VENC调试] parse_venc_info返回: {venc_result}")
                            if venc_result and 'channels' in venc_result and venc_result['channels']:
                                for channel_info in venc_result['channels']:
                                    venc_record = {
                                        'timestamp': current_time,
                                        'channel_id': channel_info.get('channel_id', 0),
                                        'width': channel_info.get('width', 0),  # 编码宽度
                                        'height': channel_info.get('height', 0),  # 编码高度
                                        'start_ok': channel_info.get('start_ok', 0),  # 编码成功帧数
                                        'fps': channel_info.get('fps', 0),
                                        'frame_count': channel_info.get('frame_count', 0)
                                    }
                                    test_case.venc_history.append(venc_record)
                                print(f"[VENC监控] 采集成功: {len(venc_result['channels'])}个channel, 历史记录数: {len(test_case.venc_history)}")
                            else:
                                print(f"[VENC调试] venc_result为空或channels为空")
                    except Exception as proc_err:
                        print(f"[Proc监控] 采集proc数据异常: {proc_err}")
                        import traceback
                        traceback.print_exc()
                    
                    # 更新GUI图表，传递空闲内存（MB）而非使用率（%）
                    if self.gui and self.rtsp_handlers:
                        try:
                            fps_values = [h.get_latest_fps() for h in self.rtsp_handlers]
                            bitrate_values = [h.get_latest_bitrate() for h in self.rtsp_handlers]
                            free_mem_mb = mem_available / 1024  # KB转MB
                            self.gui.update_chart(sample["timestamp"], fps_values, bitrate_values, free_mem_mb, self.rtsp_handlers)
                        except Exception as gui_err:
                            print(f"[内存监控] 更新GUI图表失败: {gui_err}")
                else:
                    consecutive_failures += 1
                    print(f"[内存监控] 警告：解析内存信息失败 (连续失败{consecutive_failures}次), total={mem_total}, available={mem_available}")
            except Exception as e:
                consecutive_failures += 1
                print(f"[内存监控] 采集内存信息异常 (连续失败{consecutive_failures}次): {e}")
                import traceback
                traceback.print_exc()
                import traceback
                traceback.print_exc()
            # 采样周期：使用Event.wait()允许立即唤醒线程，而不是被time.sleep()阻塞
            # timeout=10表示等待10秒或直到被set()唤醒
            self.mem_monitor_stop_event.wait(timeout=10)
            if self.mem_monitor_stop_event.is_set():
                print("[内存监控] 接收到停止信号，立即停止")
                break
        
        print("[内存监控] 内存采集线程已停止")
    
    def stop(self):
        """停止测试"""
        print("[STOP] 停止测试被调用！调用栈：")
        import traceback
        traceback.print_stack()
        self.is_running = False
        print(f"[STOP] is_running已设置为False")
        
        # 第一优先级：立即停止内存采集线程（避免继续发送telnet命令）
        print("停止内存采集线程...")
        self.mem_monitor_running = False
        self.mem_monitor_stop_event.set()  # 立即唤醒线程，不再等待time.sleep()
        if self.mem_monitor_thread and self.mem_monitor_thread.is_alive():
            print("等待内存采集线程结束（最多2秒）...")
            self.mem_monitor_thread.join(timeout=2)
            if self.mem_monitor_thread.is_alive():
                print("警告: 内存采集线程未能在时限内结束")
        self.mem_monitor_thread = None
        self.mem_monitor_stop_event.clear()  # 重置Event以便下次使用
        
        # 第二优先级：断开monitor_telnet连接（阻止任何新命令）
        if self.monitor_telnet:
            print("断开monitor telnet连接...")
            try:
                self.monitor_telnet.disconnect()
            except Exception as e:
                print(f"断开monitor telnet失败: {e}")
            self.monitor_telnet = None
        
        # 第三优先级：关闭所有视频窗口
        print(f"关闭{len(self.video_windows)}个视频窗口...")
        self._close_video_windows()
        print("所有视频窗口已关闭")
        
        # 第四优先级：停止所有RTSP处理线程
        # 使用后台线程停止，避免主线程被阻塞在capture.release()上
        print(f"停止{len(self.rtsp_handlers)}个RTSP处理线程...")
        import threading
        stop_threads = []
        for i, handler in enumerate(self.rtsp_handlers):
            def stop_handler_in_thread(idx, h):
                try:
                    print(f"正在停止RTSP处理器 #{idx} (stream_id={h.stream_id})...")
                    h.stop()
                    print(f"RTSP处理器 #{idx} 已停止")
                except Exception as e:
                    print(f"停止RTSP处理器#{idx}失败: {e}")
                    import traceback
                    traceback.print_exc()
            
            t = threading.Thread(target=stop_handler_in_thread, args=(i, handler), daemon=True)
            t.start()
            stop_threads.append((i, t))
        
        # 等待所有stop线程完成，每个最多等3秒
        for idx, t in stop_threads:
            t.join(timeout=3)
            if t.is_alive():
                print(f"[WARNING] RTSP处理器 #{idx} 停止超时，继续清理")
        
        # 在清空rtsp_handlers之前，保存stats数据用于报告生成
        stats_list = [h.stats for h in self.rtsp_handlers] if self.rtsp_handlers else []
        print(f"[STOP] 收集到stats_list，长度={len(stats_list)}")
        if stats_list:
            for idx, stats in enumerate(stats_list):
                fps_count = len(stats.fps_history) if stats.fps_history else 0
                bitrate_count = len(stats.bitrate_history) if stats.bitrate_history else 0
                print(f"[STOP] stats_list[{idx}]: stream_id={stats.stream_id}, fps_count={fps_count}, bitrate_count={bitrate_count}")
        
        # 保存到current_case，以便_execute_case能够访问
        if self.current_case:
            self.current_case.rtsp_stats_list = stats_list
            print(f"[STOP] 已保存stats_list到current_case.rtsp_stats_list")
        
        self.rtsp_handlers.clear()
        print("所有RTSP处理线程已停止")
        
        # 触发停止事件
        if self.current_case:
            event = RuntimeEvent(
                line_log="用户主动退出",
                event_level=EventLevel.ERROR,
                event_name="测试中断",
                timestamp=datetime.now()
            )
            self.current_case.event_list.append(event)
            # 记录stats数据
            print(f"[中断报告] 收集stats_list，长度={len(stats_list)}")

            # 清理case资源
            try:
                print(f"清理并保存Case {self.current_case.case_id}的资源...")
                self._cleanup_case()
            except Exception as cleanup_err:
                print(f"清理case资源时出错: {cleanup_err}")

            print("[DEBUG] _cleanup_case()返回，准备生成case报告")
            # 为当前case生成报告（即使未完成）
            try:
                print(f"为当前case生成报告: Case {self.current_case.case_id}")
                self.current_case.end_time = datetime.now()
                self.current_case.result = "中断"  # 标记为中断

                # 生成case报告（markdown和Excel）
                ReportGenerator.generate_case_report(
                    self.current_case,
                    stats_list,
                    self.current_case.free_mem_history,
                    self.current_case.case_dir
                )
                print(f"Case报告已生成")

                # 保存实时统计图表截图
                if self.gui:
                    try:
                        chart_path = os.path.join(self.current_case.case_dir, "runtime_chart.png")
                        self.gui.figure.savefig(chart_path, dpi=100, bbox_inches='tight')
                        print(f"实时统计图表已保存: {chart_path}")
                    except Exception as chart_err:
                        print(f"保存图表截图失败: {chart_err}")
            except Exception as report_err:
                print(f"生成中断报告失败: {report_err}")
        
        print("[DEBUG] stop()方法即将生成task报告")
        
        # 写入日志文件以确保追踪
        try:
            log_path = "stop_method_execution.log"
            with open(log_path, 'a', encoding='utf-8') as f:
                f.write(f"[{datetime.now()}] Reached task report section in stop()\n")
                f.flush()
        except:
            pass
        
        # 为当前task生成报告（无论current_case是否存在，都必须生成）
        try:
            self.test_task.end_time = datetime.now()
            print(f"[Task报告] 开始生成task报告")
            
            try:
                log_path = "stop_method_execution.log"
                with open(log_path, 'a', encoding='utf-8') as f:
                    f.write(f"[{datetime.now()}] About to set end_time\n")
                    f.flush()
            except:
                pass
            
            # 确定task_dir：优先使用self.test_task.task_dir，否则从current_case推导
            task_dir = self.test_task.task_dir
            if not task_dir and self.current_case:
                # 从case_dir推导task_dir（case_dir = task_dir/case_N）
                case_dir = self.current_case.case_dir
                if case_dir:
                    task_dir = os.path.dirname(case_dir)
            
            print(f"[Task报告] task_dir={task_dir}")
            
            try:
                log_path = "stop_method_execution.log"
                with open(log_path, 'a', encoding='utf-8') as f:
                    f.write(f"[{datetime.now()}] task_dir={task_dir}, is_dir={os.path.isdir(task_dir) if task_dir else 'N/A'}\n")
                    f.flush()
            except:
                pass
            
            if task_dir and isinstance(task_dir, str) and os.path.isdir(task_dir):
                print(f"[Task报告] task_dir有效，准备生成task_report.md...")
                
                try:
                    log_path = "stop_method_execution.log"
                    with open(log_path, 'a', encoding='utf-8') as f:
                        f.write(f"[{datetime.now()}] Calling ReportGenerator.generate_task_report()\n")
                        f.flush()
                except:
                    pass
                
                ReportGenerator.generate_task_report(self.test_task, task_dir)
                print(f"[Task报告] task报告生成完成(中断)")
                
                try:
                    log_path = "stop_method_execution.log"
                    with open(log_path, 'a', encoding='utf-8') as f:
                        f.write(f"[{datetime.now()}] task_report generation completed successfully\n")
                        f.flush()
                except:
                    pass
            else:
                print(f"[Task报告] task_dir无效或不存在: {task_dir}")
                
                try:
                    log_path = "stop_method_execution.log"
                    with open(log_path, 'a', encoding='utf-8') as f:
                        f.write(f"[{datetime.now()}] task_dir invalid or not a directory: {task_dir}\n")
                        f.flush()
                except:
                    pass
        except Exception as task_report_err:
            print(f"[Task报告] 生成task报告失败: {task_report_err}")
            import traceback
            traceback.print_exc()
            
            try:
                log_path = "stop_method_execution.log"
                with open(log_path, 'a', encoding='utf-8') as f:
                    f.write(f"[{datetime.now()}] Exception in task report: {task_report_err}\n")
                    f.flush()
            except:
                pass
