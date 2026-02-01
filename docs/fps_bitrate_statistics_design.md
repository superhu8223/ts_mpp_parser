# 帧率和码率统计与绘图设计文档

## 概述

本文档梳理了系统中帧率(FPS)和码率(Bitrate)的两层逻辑：
1. **RTSP线程中的统计逻辑**：每5秒计算一次，存储到history中
2. **用户界面(GUI)的绘图逻辑**：直接读取history，实时显示曲线

---

## 第一层：RTSP线程中的统计逻辑

### 文件位置
- 主文件：`src/modules/rtsp_handler.py`
- 相关类：`RTSPHandler`

### 1.1 数据收集阶段（每帧）

**位置**：`RTSPHandler._pull_stream()` 中的主拉流循环

**流程**：
```
frame循环 (每从RTSP拉取一帧)
├─ 解码后获得OpenCV frame (height, width, 3)
├─ 累计帧计数：frame_count_in_period += 1
├─ 累计数据量：bytes_in_period += frame.nbytes
├─ 实时计算当前值：
│  ├─ current_fps_live = frame_count_in_period / time_diff (秒)
│  └─ current_bitrate_live = (bytes_in_period * 8) / (time_diff * 1000) kbps
└─ 每帧调用frame_callback(stream_id, frame, fps, bitrate)
```

**关键计算**：
```python
# 帧处理时的实时计算（用于GUI每帧更新）
frame_count_in_period += 1
bytes_in_period += frame.nbytes
now = datetime.now()
time_diff_live = (now - self.last_stats_time).total_seconds()
if time_diff_live > 0:
    current_fps_live = frame_count_in_period / time_diff_live
    current_bitrate_live = (bytes_in_period * 8) / (time_diff_live * 1000)  # kbps
```

### 1.2 统计聚合阶段（每5秒）

**位置**：`RTSPHandler._update_stats()` 方法

**触发条件**：
```python
if (now - self.last_stats_time).total_seconds() >= 5.0:
    self._update_stats(now, frame_count_in_period, bytes_in_period)
```

**统计计算**：
```python
def _update_stats(self, now, frame_count_in_period, bytes_in_period):
    """每5秒调用一次，计算该周期的平均FPS和码率"""
    time_diff = (now - self.last_stats_time).total_seconds()  # 应约等于5秒
    
    if time_diff > 0:
        # 计算5秒周期内的平均帧率
        fps = frame_count_in_period / time_diff
        
        # 计算5秒周期内的平均码率
        bitrate = (bytes_in_period * 8) / (time_diff * 1000)  # 单位：kbps
        
        # 追加到历史记录
        self.stats.fps_history.append(StatData(timestamp=now, value=fps))
        self.stats.bitrate_history.append(StatData(timestamp=now, value=bitrate))
        
        # 重置周期计数器
        frame_count_in_period = 0
        bytes_in_period = 0
        self.last_stats_time = now
```

### 1.3 获取最新值的接口

**位置**：`RTSPHandler` 类的公共方法

```python
def get_latest_fps(self) -> float:
    """获取最新帧率（最后一次5秒统计的结果）"""
    if self.stats.fps_history:
        return self.stats.fps_history[-1].value
    return 0.0

def get_latest_bitrate(self) -> float:
    """获取最新码率（最后一次5秒统计的结果）"""
    if self.stats.bitrate_history:
        return self.stats.bitrate_history[-1].value
    return 0.0
```

### 1.4 数据结构

**StatData 类**（用于存储单个统计点）：
```python
@dataclass
class StatData:
    timestamp: datetime  # 统计时刻
    value: float        # 统计值（fps或bitrate）
```

**RTSPHandler.stats 结构**：
```
stats
├─ fps_history: List[StatData]           # 帧率历史（5秒一个点）
├─ bitrate_history: List[StatData]       # 码率历史（5秒一个点）
├─ first_frame_time: datetime            # 首帧时刻
└─ ...其他统计字段
```

### 1.5 时间轴说明

| 事件 | 周期 | 操作 |
|-----|------|------|
| 每帧到达 | ~0.033-0.1秒（30-10fps） | 累计`frame_count_in_period`和`bytes_in_period` |
| frame_callback调用 | 每帧 | 传递当前实时的`current_fps_live`和`current_bitrate_live` |
| 5秒统计 | 5秒 | 调用`_update_stats()`，计算5秒内的平均值，存入history |

---

## 第二层：用户界面的绘图逻辑

### 文件位置
- 主文件：`src/gui/test_gui.py`
- 相关类：`TestGUI`

### 2.1 数据更新入口

**方法**：`TestGUI.update_chart()`

**调用来源**（来自test_engine.py）：
```python
# 每1秒调用一次
if current_time - last_chart_update_time[0] >= 1.0:
    fps_values = [h.get_latest_fps() for h in self.rtsp_handlers]
    bitrate_values = [h.get_latest_bitrate() for h in self.rtsp_handlers]
    free_mem_mb = mem_available / 1024
    self.gui.update_chart(
        datetime.now(), 
        fps_values,           # 最新的每路fps值
        bitrate_values,       # 最新的每路bitrate值
        free_mem_mb,          # 内存值
        self.rtsp_handlers    # rtsp处理器列表（用于读取history）
    )
```

### 2.2 数据检查和过滤

**位置**：`update_chart()` 开头

```python
def update_chart(self, time_point, fps_values, bitrate_values, mem_usage=None, rtsp_handlers=None):
    """
    参数说明：
    - time_point: datetime，当前时刻
    - fps_values: List[float]，当前每路流的fps
    - bitrate_values: List[float]，当前每路流的bitrate
    - mem_usage: float，当前内存(MB)
    - rtsp_handlers: List[RTSPHandler]，用于读取fps_history和bitrate_history
    """
    
    # 跳过启动初期的无效数据（所有bitrate都为0）
    if bitrate_values and all(v == 0 or v < 1 for v in bitrate_values):
        print(f"[GUI] 跳过无效数据：bitrate_values全为0或极小值")
        return
    
    # 累计时间序列（用于绘图的x轴）
    self.time_data.append(time_point)
    self.mem_data.append(mem_usage)
    self.rtsp_handlers = rtsp_handlers  # 保存handlers供绘图使用
```

### 2.3 Bitrate绘图逻辑（关键改进）

**位置**：`update_chart()` 中的bitrate子图绘制部分

**重要**：与fps处理方式**一致**，直接从`bitrate_history`读取完整数据

```python
# === 绘制码率子图 ===
if self.rtsp_handlers and len(self.rtsp_handlers) > 0:
    num_streams = len(self.rtsp_handlers)
    
    # 为每条线设置颜色（Green色系）
    bitrate_cmap = cm.get_cmap('Greens')
    if num_streams > 1:
        colors = [to_hex(bitrate_cmap(0.3 + 0.6 * i / (num_streams - 1))) 
                  for i in range(num_streams)]
    else:
        colors = [to_hex(bitrate_cmap(0.7))]
    
    # 绘制每条流的bitrate曲线
    for i, handler in enumerate(self.rtsp_handlers):
        if handler.stats.bitrate_history:
            # 从history中提取时间和值
            times = [mdates.date2num(h.timestamp) 
                    if isinstance(h.timestamp, datetime) 
                    else h.timestamp
                    for h in handler.stats.bitrate_history]
            vals = [h.value for h in handler.stats.bitrate_history]
            
            if times and vals and len(times) == len(vals):
                print(f"[GUI] Stream{i} Bitrate: 绘制{len(vals)}个数据点, 范围:{min(vals):.0f}-{max(vals):.0f} kbps")
                
                # 绘制曲线
                self.ax_bitrate.plot(
                    times, vals,
                    color=colors[i],
                    linewidth=2,
                    marker='o',
                    markersize=3,
                    label=f'Stream{i}',
                    linestyle='-',
                    alpha=0.8
                )
```

**关键点**：
- 不用`get_latest_bitrate()`的单个值，而用完整的`bitrate_history`
- 这样即使单个值相同，完整的历史也能显示波动
- 避免了"竖线"问题（所有值都是单个最新值）

### 2.4 FPS绘图逻辑

**位置**：`update_chart()` 中的fps子图绘制部分

**特点**：从`self.fps_data`列表读取，该列表存储每次`update_chart()`传入的fps值

```python
# === 绘制帧率子图 ===
if self.fps_data and len(self.fps_data) > 0 and len(self.fps_data[0]) > 0:
    num_streams = len(self.fps_data[0])
    
    # 为每条线设置颜色（Blue色系）
    fps_cmap = cm.get_cmap('Blues')
    if num_streams > 1:
        colors = [to_hex(fps_cmap(0.3 + 0.6 * i / (num_streams - 1))) 
                  for i in range(num_streams)]
    else:
        colors = [to_hex(fps_cmap(0.7))]
    
    # 绘制每条流的fps曲线
    for i in range(num_streams):
        # 从self.fps_data(列表)中提取第i条流的数据
        fps_series = [data[i] if i < len(data) else 0 
                     for data in self.fps_data]
        
        self.ax_fps.plot(
            time_numeric, fps_series,  # time_numeric是时间轴
            color=colors[i],
            linewidth=2,
            marker='o',
            markersize=3,
            label=f'Stream{i}',
            linestyle='-',
            alpha=0.8
        )
```

**特点**：
- 使用每次更新时的`time_numeric`（从`self.time_data`转换）
- 按时间序列存储fps值，形成时间序列

### 2.5 内存绘图逻辑

**位置**：`update_chart()` 中的内存子图绘制部分

```python
# === 绘制内存子图 ===
if self.mem_data and len(self.mem_data) > 0:
    mem_series = [m for m in self.mem_data]  # 单条线：总内存
    
    if len(mem_series) == len(time_numeric):
        self.ax_mem.plot(
            time_numeric, mem_series,
            color='dimgray',
            linewidth=2.5,
            marker='s',
            markersize=3,
            label='Free Memory',
            linestyle='-',
            alpha=0.8
        )
        
        # 填充区域
        self.ax_mem.fill_between(
            time_numeric, 0, mem_series,
            color='lightgray',
            alpha=0.3
        )
        
        print(f"[GUI图表] 绘制了{len(mem_series)}个内存数据点")
```

### 2.6 时间轴处理

**位置**：`update_chart()` 中的时间转换

```python
# 将datetime对象转换为matplotlib数字格式
def _to_mdate(tp):
    if isinstance(tp, datetime):
        return mdates.date2num(tp)
    try:
        return mdates.date2num(datetime.strptime(str(tp), '%H:%M:%S'))
    except Exception:
        return None

time_numeric = [_to_mdate(t) for t in self.time_data]
time_numeric = [t for t in time_numeric if t is not None]

# 设置x轴时间格式化为HH:MM:SS
self.ax_bitrate.xaxis.set_major_formatter(mdates.DateFormatter('%H:%M:%S'))
self.ax_fps.xaxis.set_major_formatter(mdates.DateFormatter('%H:%M:%S'))
self.ax_mem.xaxis.set_major_formatter(mdates.DateFormatter('%H:%M:%S'))
```

---

## 三层数据流关系图

```
┌─────────────────────────────────────────────────────────────┐
│                    RTSP线程主循环                             │
│                  (RTSPHandler._pull_stream)                  │
└────┬────────────────────────────────────────────────────────┘
     │
     ├──每帧（~0.033-0.1秒）──┐
     │                         │
     ├─> 累计: frame_count_in_period++
     │         bytes_in_period += frame.nbytes
     │
     ├─> 实时计算: current_fps_live
     │           current_bitrate_live
     │
     └─> frame_callback(stream_id, frame, fps_live, bitrate_live)
                                                    │
                                                    ▼
                    ┌──────────────────────────────────────────┐
                    │  GUI/VideoWindow (每帧更新)              │
                    │  - 显示实时FPS和Bitrate数值              │
                    │  - 更新视频窗口显示                      │
                    └──────────────────────────────────────────┘
     │
     │
     ├──每5秒──┐
     │         │
     └─> _update_stats(now, frame_count_in_period, bytes_in_period)
             │
             ├─> 计算平均值:
             │   fps = frame_count_in_period / 5秒
             │   bitrate = (bytes_in_period * 8) / (5秒 * 1000)
             │
             └─> stats.fps_history.append(StatData(timestamp, fps))
                 stats.bitrate_history.append(StatData(timestamp, bitrate))
                         │
                         ▼
        ┌────────────────────────────────────────┐
        │  test_engine.py (每1秒一次)            │
        │  - 获取: get_latest_fps()              │
        │  - 获取: get_latest_bitrate()          │
        │  - 获取: free_mem_mb                   │
        │  - 调用: gui.update_chart(...)         │
        └────────┬─────────────────────────────┘
                 │
                 ▼
        ┌────────────────────────────────────────┐
        │  TestGUI.update_chart()                │
        │  (从rtsp_handlers读取history)          │
        │                                        │
        │  绘制3个子图:                          │
        │  1. Bitrate (从bitrate_history)        │
        │  2. FPS (从时间序列)                   │
        │  3. Memory (从mem_data)                │
        │                                        │
        │  时间轴: datetime -> matplotlib数值    │
        │  显示: matplotlib图表                  │
        └────────────────────────────────────────┘
```

---

## 四、数据统计周期对照表

| 统计层级 | 周期 | 说明 | 数据结构 |
|---------|------|------|---------|
| **RTSP层（实时）** | 每帧 (~30-100ms) | 用于GUI每帧更新视频窗口数值 | 单个float |
| **RTSP层（聚合）** | 每5秒 | 计算5秒内的平均FPS和码率 | StatData(timestamp, value) |
| **Engine层** | 每1秒 | 从rtsp_handlers读最新值，传给GUI | List[float] |
| **GUI层** | 每次update_chart | 从history直接读取完整数据绘图 | List[StatData] |

---

## 五、关键设计原则

### 原则1：分层结构
- **RTSP层**：数据采集和聚合
- **Engine层**：数据转发和协调
- **GUI层**：数据可视化

### 原则2：双通道数据流
- **实时通道**：每帧的实时fps/bitrate → 用于显示数值标签
- **聚合通道**：5秒统计的history → 用于绘制曲线图

### 原则3：一致性
- bitrate处理与fps处理逻辑一致（都用history）
- 避免"竖线"问题（使用完整history而不是单个值）

### 原则4：时间同步
- 所有时间戳使用`datetime.now()`获取
- GUI统一使用matplotlib的date2num和DateFormatter处理时间

---

## 六、常见问题排查

### Q1: 为什么bitrate显示竖线？
**原因**：使用`get_latest_bitrate()`的单个值，每次都是相同值
**解决**：改用`bitrate_history`的完整列表

### Q2: FPS和Bitrate曲线对齐吗？
**答**：不完全对齐
- FPS：采样周期=1秒（update_chart调用频率）
- Bitrate：数据点周期=5秒（_update_stats调用频率）
- 这是正常的，两者统计粒度不同

### Q3: 内存数据为何与fps/bitrate点数不同？
**原因**：内存采集周期与fps/bitrate不同
- 内存：10秒采集一次
- FPS/Bitrate：5秒统计一次
- 时间线上有时差

### Q4: 如何调整统计周期？
| 要调整 | 修改位置 | 参数 |
|-------|--------|------|
| 5秒FPS/Bitrate统计 | rtsp_handler.py line 365 | `>= 5.0` |
| 1秒GUI更新频率 | test_engine.py line 531 | `>= 1.0` |
| 10秒内存采集 | test_engine.py line 1284 | `timeout=10` |

---

## 附录：数据流示例

```
时间轴示例：
├─ 0:00:00.100  frame #1  fps_live=30  bitrate_live=800kbps  (frame_callback)
├─ 0:00:00.200  frame #2  fps_live=30  bitrate_live=800kbps
├─ 0:00:00.300  frame #3  fps_live=30  bitrate_live=800kbps
├─ ...
│
├─ 0:00:05.000  [5秒统计] fps_history.append(32 kbps)
│                         bitrate_history.append(810 kbps)
│                         
├─ 0:00:05.100  frame #151 fps_live=32  bitrate_live=810kbps
├─ ...
│
├─ 0:00:06.000  [Engine] update_chart(fps=[32], bitrate=[810], handlers=[...])
│               [GUI]    从bitrate_history读取: [810 kbps (1个点)]
│                        绘制1个点
│
├─ 0:00:07.000  [Engine] update_chart(fps=[32], bitrate=[810], handlers=[...])
│               [GUI]    从bitrate_history读取: [810 kbps (仍然1个点)]
│
├─ 0:00:10.000  [5秒统计] fps_history.append(31 kbps)
│                         bitrate_history.append(805 kbps)
│
├─ 0:00:11.000  [Engine] update_chart(fps=[31], bitrate=[805], handlers=[...])
│               [GUI]    从bitrate_history读取: [810 kbps, 805 kbps (2个点)]
│                        绘制2个点的曲线
```

