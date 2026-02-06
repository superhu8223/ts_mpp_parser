#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
数据模型定义
包含RuntimeEvent、TestCase、TestTask等核心数据结构
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Dict, Optional, Any
from enum import Enum


class EventLevel(Enum):
    """事件等级枚举"""
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    EMERGE = "emerge"
    FATAL = "fatal"


@dataclass
class RuntimeEvent:
    """运行时事件"""
    line_log: str  # 行日志
    event_level: EventLevel  # 事件等级
    event_name: str  # 事件简称
    timestamp: datetime  # 时间戳
    
    def __str__(self):
        return f"[{self.timestamp.strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]}] [{self.event_level.value}] {self.event_name}: {self.line_log}"


@dataclass
class CheckSpec:
    """检查规格"""
    vi_size: List[str] = field(default_factory=list)  # vi输出分辨率
    vi_min_fps: List[int] = field(default_factory=list)  # vi最低帧率
    vpss_in_size: List[str] = field(default_factory=list)  # vpss输入分辨率
    vpss_out_size: List[str] = field(default_factory=list)  # vpss输出分辨率
    vpss_min_fps: List[int] = field(default_factory=list)  # vpss最低帧率
    vpss_crop_size: List[str] = field(default_factory=list)  # vpss裁剪分辨率
    venc_size: List[str] = field(default_factory=list)  # venc编码分辨率
    venc_min_fps: List[int] = field(default_factory=list)  # venc最低帧率
    venc_max_bitrate: List[int] = field(default_factory=list)  # venc最大码率


@dataclass
class DebugAction:
    """调试动作"""
    time_gap: int  # 距离上一次操作的间隔时间（秒）
    action_cmd: str  # 动作命令
    action_checks: CheckSpec  # 动作检查规格


@dataclass
class TestCase:
    """测试用例"""
    case_id: int  # case索引号
    case_name: str  # case名称
    run_cmd: List[str]  # 拉流命令列表（必须使用{}格式）
    stream_num: int  # rtsp码流数目
    hold_time: Any  # 持续时间（秒或"longtime"）
    
    # 可选字段
    uboot_cmd: Optional[List[str]] = None  # uboot命令列表（必须使用{}格式）
    pre_cmd: List[str] = field(default_factory=list)  # 拉流前命令列表（必须使用{}格式）
    post_cmd: List[str] = field(default_factory=list)  # 拉流后命令
    b_need_reboot: Optional[bool] = None  # 是否需要重启
    b_need_ctrl_c: Optional[bool] = None  # 是否需要周期性发送 Ctrl+C 退出并重启拉流
    run_cmd_checks: Optional[CheckSpec] = None  # 拉流检查规格
    debug_actions: List[DebugAction] = field(default_factory=list)  # 调试动作列表
    
    # 运行时数据
    event_list: List[RuntimeEvent] = field(default_factory=list)  # 事件队列
    free_mem_history: List[dict] = field(default_factory=list)  # 空闲内存历史记录
    isp_history: List[dict] = field(default_factory=list)  # ISP统计历史记录
    vi_history: List[dict] = field(default_factory=list)  # VI统计历史记录
    vpss_history: List[dict] = field(default_factory=list)  # VPSS统计历史记录
    venc_history: List[dict] = field(default_factory=list)  # VENC统计历史记录
    rtsp_stats_list: List = field(default_factory=list)  # 保存的RTSP统计数据（用于stop()调用时保留stats）
    preCmd_executed: bool = False  # preCmd是否已执行
    case_dir: Optional[str] = None  # case目录路径
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    is_longterm: bool = False  # 是否为长稳烤机模式
    ctrl_c_count: int = 0  # Ctrl+C发送次数（用于bNeedCtrlC模式）
    
    def __post_init__(self):
        """初始化后处理"""
        # 如果启用 Ctrl+C 循环模式，不再需要 holdtime 字段
        if self.b_need_ctrl_c:
            # bNeedCtrlC 模式下，等待所有RTSP流收到数据后立即发送Ctrl+C，不使用holdtime
            self.hold_time = None  # 设置为None表示不使用
            self.is_longterm = False  # Ctrl+C 模式不使用 is_longterm
            print(f"[Case {self.case_id}] [DEBUG] bNeedCtrlC模式已启用，将在所有RTSP流收到数据后立即发送Ctrl+C")
        elif isinstance(self.hold_time, str) and self.hold_time.lower() == "longtime":
            self.is_longterm = True
            self.hold_time = float('inf')
            print(f"[Case {self.case_id}] [DEBUG] 长稳烤机模式已启用")
        else:
            self.hold_time = int(self.hold_time) if self.hold_time else 100
            print(f"[Case {self.case_id}] [DEBUG] 普通模式，holdtime={self.hold_time}秒")


@dataclass
class SerialConfig:
    """串口配置"""
    port: str  # 串口号
    baudrate: int = 115200  # 波特率
    databits: int = 8  # 数据位
    parity: str = "none"  # 校验位
    stopbits: int = 1  # 停止位
    flowcontrol: str = "none"  # 流控


@dataclass
class TestTask:
    """测试任务"""
    task_name: str  # 任务名称
    serial_config: SerialConfig  # 串口配置
    evb_ip: str  # evb IP地址
    evb_port: int = 23  # evb telnet端口
    
    # 烧录文件配置
    fip_name: Optional[str] = None
    kernel_name: Optional[str] = None
    rootfs_name: Optional[str] = None
    userfs_name: Optional[str] = None
    
    # 烧录命令
    burn_fip_cmd: Optional[str] = None
    burn_kernel_cmd: Optional[str] = None
    burn_rootfs_cmd: Optional[str] = None
    burn_userfs_cmd: Optional[str] = None
    
    # 烧录控制
    burn_flash: bool = True  # 是否执行烧录阶段
    
    # 测试用例列表
    case_list: List[TestCase] = field(default_factory=list)
    
    # 全局配置
    each_case_need_reboot: bool = True  # 每个case是否需要重启
    
    # 运行时数据
    task_dir: Optional[str] = None  # task目录路径
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None


@dataclass
class LogPattern:
    """日志模式"""
    pattern: str  # 匹配模式
    event_name: str  # 事件简称


@dataclass
class LogMapConfig:
    """日志映射配置"""
    serial_emerge: List[LogPattern] = field(default_factory=list)
    serial_error: List[LogPattern] = field(default_factory=list)
    serial_warning: List[LogPattern] = field(default_factory=list)
    serial_info: List[LogPattern] = field(default_factory=list)
    serial_ignore: List[LogPattern] = field(default_factory=list)
    
    launch_emerge: List[LogPattern] = field(default_factory=list)
    launch_error: List[LogPattern] = field(default_factory=list)
    launch_warning: List[LogPattern] = field(default_factory=list)
    launch_info: List[LogPattern] = field(default_factory=list)
    launch_ignore: List[LogPattern] = field(default_factory=list)


@dataclass
class StatData:
    """统计数据"""
    timestamp: datetime
    value: float
    
    
@dataclass
class StreamStats:
    """码流统计数据"""
    stream_id: int
    url: str
    fps_history: List[StatData] = field(default_factory=list)  # 帧率历史
    bitrate_history: List[StatData] = field(default_factory=list)  # 码率历史
    first_frame_time: Optional[datetime] = None  # 第一帧时间
    keyframe_count: int = 0  # 关键帧计数
    frame_width: Optional[int] = None  # 视频帧宽度
    frame_height: Optional[int] = None  # 视频帧高度
