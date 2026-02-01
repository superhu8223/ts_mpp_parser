# 串口与Telnet命令执行流程说明

## 概述

TsMpp自动化测试工具在执行测试case时，需要通过**串口**和**Telnet**两种方式与设备交互：
- **串口**：用于设备启动、登录、环境准备（preCmd）、网络配置
- **Telnet**：用于执行测试命令（runCmd）和监控

本文档详细说明命令执行的完整流程。

---

## 一、测试执行总体流程

```
1. 准备阶段 (_step1_prepare)
   └─ 连接串口并开始监控

2. 烧录阶段 (_step2_burn) [可选，burn_flash=true时]
   ├─ 进入uboot模式
   ├─ 烧录FIP/Kernel/Rootfs/Userfs
   ├─ reset重启
   └─ 进入Linux并登录

3. 执行测试case (_execute_case)
   ├─ 判断是否需要reboot
   ├─ 执行reboot（如果需要）
   │   ├─ 如果有ubootCmd：先进入uboot执行，再boot进Linux
   │   └─ 否则：直接reboot进入Linux
   ├─ 【串口】执行preCmd（完整环境准备，包括网络配置、telnetd启动等）
   ├─ 【Telnet】启动launch线程执行runCmd
   ├─ 【Telnet】启动monitor线程监控日志
   ├─ 【RTSP】启动视频流监控
   └─ 等待holdTime后生成报告
```

---

## 二、串口命令执行详细流程

### 2.1 串口连接与监控

**文件**：`src/modules/serial_handler.py`

```python
# 连接串口
serial_handler.connect()

# 启动后台监控线程
serial_handler.start_monitoring()

# 设置日志文件（每个case独立日志）
serial_handler.set_log_file("case_1/serial.log")
```

**监控线程**持续运行，实时读取串口数据：
- 解析时间戳并写入日志文件
- 匹配logmap.txt中的关键字（error/emerge/warning/info）
- 触发事件回调
- 更新GUI显示

---

### 2.2 进入Linux环境

**文件**：`src/modules/serial_handler.py` - `enter_linux_mode()`

#### 流程图

```
开始
  ↓
检查当前模式 (get_current_mode)
  ↓
┌──────────────────────┐
│ 当前模式？           │
├──────────────────────┤
│ linux        → 直接返回（已登录）
│ linux_login  → 发送"root"登录 → 确认成功 → 返回
│ uboot        → 执行boot命令 → 进入轮询
│ unknown      → 继续轮询
└──────────────────────┘
  ↓
【轮询等待】（最长60秒，间隔1.5秒）
  ├─ 检测到 linux_login → 立即发送"root" → 等待2秒 → 确认登录
  ├─ 检测到 linux → 返回成功
  └─ 检测到 unknown → 继续等待（系统启动中）
  ↓
超时失败
```

#### 模式检测逻辑 (get_current_mode)

```python
# 1. 清空日志队列
# 2. 发送回车获取提示符
# 3. 收集2秒内的响应
# 4. 检测提示符：
#    - "=>" 或 ">" → uboot模式
#    - "buildroot login:" → linux_login（需要登录）
#    - 行尾为"#" → linux模式（已登录）
#    - 其他 → unknown（可能正在启动）
```

**关键点**：
- 登录后必须调用 `get_current_mode()` **确认**检测到 `#` 提示符
- 避免在登录完成前发送其他命令导致混乱

---

### 2.3 执行preCmd（环境准备）

**文件**：`src/modules/test_engine.py` - `_execute_case()`

**时机**：在进入Linux并成功登录后立即执行

**职责**：preCmd负责所有环境准备工作，包括网络配置、NFS挂载、文件复制、telnetd启动等

**防重复机制**：
```python
if test_case.pre_cmd and not test_case.preCmd_executed:
    print(f"[DEBUG] 通过串口执行preCmd，preCmd_executed={test_case.preCmd_executed}")
    serial_handler.send_command(test_case.pre_cmd, wait_time=1)
    time.sleep(1)
    test_case.preCmd_executed = True  # 标记已执行，防止重复
```

**preCmd示例**（task.cfg）：
```python
preCmd={
    "insmod_nfs",                    # 加载NFS模块
    "ifconfig eth0 192.168.1.10",   # 配置IP（必须）
    "cd /tmp",                      # 切换目录
    "mkdir nfs",                    # 创建挂载点
    "sleep 10",                     # 等待网络稳定
    "mount -t nfs -o nolock,nfsvers=3 192.168.1.99:/home/user/filesys nfs",
    "cp /tmp/nfs/tests/st_debug_client /tmp/",
    "cp /tmp/nfs/libs/usr_library_forTS.so /tmp/",
    "telnetd"                        # 启动telnet服务（必须，供后续telnet连接）
}
```

**⚠️ 重要提示**：
- preCmd必须包含 `ifconfig` 配置网络IP
- preCmd必须包含 `telnetd` 启动telnet服务，否则后续launch/monitor线程无法连接

**执行方式**：
```python
# send_command支持字符串列表
serial_handler.send_command(command_list, wait_time=1)

# 内部循环逐条发送：
for cmd in commands:
    cmd = cmd.strip().strip('"').strip("'")  # 去除引号
    if not cmd.endswith('\n'):
        cmd += '\r\n'  # 添加换行
    serial_port.write(cmd.encode('utf-8'))
    time.sleep(wait_time)  # 每条命令间隔1秒
```

**日志输出示例**：
```
[2026-01-26 21:23:45.123] # insmod_nfs
[2026-01-26 21:23:46.234] # ifconfig eth0 192.168.1.10
[2026-01-26 21:23:47.345] # cd /tmp
...
```

---

## 三、Telnet命令执行详细流程

### 3.1 启动Launch线程

**文件**：`src/modules/test_engine.py` - `_start_launch_thread()`

**前提条件**：
- 串口已完成preCmd（包含网络配置和telnetd启动）
- telnetd服务已在preCmd中启动（端口23可连接）

**执行流程**：
```python
# 1. 创建TelnetHandler实例
launch_telnet = TelnetHandler(evb_ip, evb_port, logmap_config, log_type="launch")

# 2. 连接telnet
if not launch_telnet.connect():
    return  # 连接失败，放弃

# 3. 设置日志回调（更新GUI和文件）
launch_telnet.set_log_callback(gui.update_launch_log)
launch_telnet.add_event_callback(on_event)

# 4. 启动后台监控线程
launch_telnet.start_monitoring(log_file)

# 5. 登录root用户
launch_telnet.send_command("root")

# 6. 设置ISP日志环境变量
launch_telnet.send_command(
    "export ISP_CMD_LOG_PARAM=\\{\\\"log_level\\\":5\\,\\\"log_mode\\\":3\\}"
)
time.sleep(1)  # 等待命令执行

# 7. 执行runCmd（测试命令）
launch_telnet.send_command(test_case.run_cmd)
```

---

### 3.2 执行runCmd（测试命令）

**runCmd示例**（task.cfg）：
```python
runCmd={
    "sample_venc 0 --vpssSize=2mp,1mp"
}
```

**执行方式**：
```python
# telnet的send_command同样支持列表
def send_command(self, command):
    commands = command if isinstance(command, list) else [command]
    
    for cmd in commands:
        cmd = cmd.strip().strip('"').strip("'")  # 去除引号
        
        if not cmd:  # 跳过空命令
            continue
        
        # 添加换行
        if not cmd.endswith('\n'):
            cmd += '\n'
        
        # 发送到telnet连接
        telnet_connection.write(cmd.encode('utf-8'))
        time.sleep(0.5)
```

**日志输出示例**（launchThread.log）：
```
[2026-01-26 21:24:10.123] # root
[2026-01-26 21:24:11.234] # export ISP_CMD_LOG_PARAM=\{\"log_level\":5\,\"log_mode\":3\}
[2026-01-26 21:24:12.345] # sample_venc 0 --vpssSize=2mp,1mp
[2026-01-26 21:24:12.456] [INFO] VPSS init success
[2026-01-26 21:24:12.567] [INFO] VENC start encoding...
```

---

### 3.3 启动Monitor线程

**文件**：`src/modules/test_engine.py` - `_start_monitor_thread()`

**作用**：
- 监控设备运行状态
- 解析monitorThread日志中的关键信息
- 检查runCmdChecks配置项是否满足

**流程**：
```python
# 1. 创建第二个telnet连接
monitor_telnet = TelnetHandler(evb_ip, evb_port, logmap_config, log_type="monitor")

# 2. 连接并登录
monitor_telnet.connect()
monitor_telnet.set_log_callback(gui.update_monitor_log)
monitor_telnet.start_monitoring(log_file)
monitor_telnet.send_command("root")

# 3. 执行监控命令（通常为空，只监控日志）
if test_case.monitor_cmd:
    monitor_telnet.send_command(test_case.monitor_cmd)
```

---

## 四、命令执行时序图

```
Time    Serial                  Launch Telnet           Monitor Telnet      RTSP
──────────────────────────────────────────────────────────────────────────────
0s      connect()
        start_monitoring()
        
10s     enter_linux_mode()
         ├─ boot (if needed)
         ├─ 等待"buildroot login:"
         └─ send "root"
         
15s     [检测到 # 提示符]
        
16s     执行preCmd[0]
         └─ "insmod_nfs"
         
17s     执行preCmd[1]
         └─ "ifconfig eth0 192.168.1.10"
         
18s     执行preCmd[2-7]
         └─ mount, cp, cp...
         
26s     执行preCmd[8]
         └─ "telnetd"
         
28s                             connect()
                                send "root"
                                
29s                             export ISP_CMD_LOG_PARAM
                                
30s                             send runCmd
                                 └─ "sample_venc 0 --vpssSize=2mp,1mp"
                                 
31s                                                     connect()
                                                        send "root"
                                                        
32s                                                                         connect RTSP
                                                                            stream 0: rtsp://192.168.1.10:8554/live_0
                                                                            stream 1: rtsp://192.168.1.10:8554/live_1
                                                                            
33s     [持续监控]              [监控runCmd输出]        [监控系统状态]       [监控视频流]
...                             解析FPS/bitrate        解析free mem         计算FPS/bitrate
                                
130s    [holdTime结束]
        生成报告
```

---

## 五、关键实现细节

### 5.1 命令列表处理

**支持两种格式**：
```python
# 单条命令（字符串）
runCmd="sample_venc 0 --vpssSize=2mp"

# 多条命令（列表）
preCmd={
    "insmod_nfs",
    "ifconfig eth0 192.168.1.10",
    "cd /tmp"
}
```

**统一处理**：
```python
commands = command if isinstance(command, list) else [command]
for cmd in commands:
    # 处理每条命令
```

### 5.2 引号处理

配置文件中的命令可能带引号，需要清理：
```python
cmd = cmd.strip()
if cmd.startswith('"') and cmd.endswith('"'):
    cmd = cmd[1:-1]
elif cmd.startswith("'") and cmd.endswith("'"):
    cmd = cmd[1:-1]
```

### 5.3 空命令跳过

避免发送空字符串导致多余换行：
```python
if not cmd:  # 跳过空命令
    continue
```

### 5.4 防重复执行机制

**preCmd防重复**：
```python
class TestCase:
    preCmd_executed: bool = False  # 标志位
    
# 执行前检查
if test_case.pre_cmd and not test_case.preCmd_executed:
    serial_handler.send_command(test_case.pre_cmd, wait_time=1)
    test_case.preCmd_executed = True  # 执行后设置
```

**注意**：
- 每个case对象独立，不会影响其他case
- reboot后如果需要重新执行preCmd，需要手动重置标志

### 5.5 日志队列清理

在关键时刻清理日志队列，避免旧数据干扰：
```python
# 在reboot前清空队列
while not self.serial_handler.log_queue.empty():
    try:
        self.serial_handler.log_queue.get_nowait()
    except:
        break
```

---

## 六、常见问题与解决方案

### 6.1 preCmd执行两次

**原因**：`preCmd_executed` 标志未生效

**解决**：
1. 确保标志在第一次执行后立即设置为 `True`
2. 检查是否有多个地方调用了preCmd
3. 添加调试日志追踪执行次数

### 6.2 登录后立即发送命令导致混乱

**现象**：
```
buildroot login: cd /tmp
Password:
```

**原因**：`enter_linux_mode()` 未等待登录完成就返回，后续preCmd命令直接发送

**解决**：
1. 登录后必须调用 `get_current_mode()` 确认检测到 `#` 提示符
2. 等待足够时间（2秒）让登录过程完成
3. 返回 `unknown` 而不是默认 `linux`，确保真正登录成功

### 6.3 Telnet命令被分割

**现象**：
```
e
xport ISP_CMD_LOG_PARAM
```

**原因**：发送了空字符串 `send_command("")` 导致多余换行

**解决**：
```python
if not cmd:  # 跳过空命令
    continue
```

### 6.4 网络未就绪导致Telnet连接失败

**原因**：preCmd执行太快，网络未完全启动

**解决**：
1. preCmd中添加 `sleep 10` 等待网络稳定
2. Telnet连接失败时重试机制
3. 检查telnetd是否成功启动

---

## 七、调试技巧

### 7.1 查看日志文件

每个case生成独立的日志目录：
```
record/20260126_212105_5326+lunch231/
  └─ case_1/
      ├─ serial.log           # 串口完整日志
      ├─ launchThread.log     # launch telnet日志
      ├─ monitorThread.log    # monitor telnet日志
      └─ case_report.md       # 测试报告
```

### 7.2 启用调试输出

代码中添加 `[DEBUG]` 标记：
```python
print(f"[DEBUG] 通过串口执行preCmd，preCmd_executed={test_case.preCmd_executed}")
```

### 7.3 检查模式检测

手动测试 `get_current_mode()`：
```python
# 在串口监控开启后
mode = serial_handler.get_current_mode()
print(f"Current mode: {mode}")
```

### 7.4 监控GUI实时日志

程序运行时查看GUI窗口：
- **Serial日志**：显示串口所有输出
- **Launch日志**：显示runCmd执行过程
- **Monitor日志**：显示系统监控信息

---

## 八、最佳实践

### 8.1 preCmd设计建议

1. **按顺序组织**：从系统配置到应用准备
2. **添加等待**：关键步骤后加 `sleep` 确保完成
3. **幂等性**：多次执行不会出错（如 `mkdir -p`）
4. **错误处理**：关键命令加 `|| true` 避免中断

示例：
```python
preCmd={
    "insmod_nfs",                          # 1. 加载模块
    "sleep 2",                             # 2. 等待模块就绪
    "ifconfig eth0 192.168.1.10",         # 3. 配置网络（必须）
    "sleep 5",                             # 4. 等待网络稳定
    "mkdir -p /tmp/nfs",                   # 5. 创建目录（-p避免已存在报错）
    "mount -t nfs ... || true",            # 6. 挂载（允许失败）
    "cp /tmp/nfs/tests/* /tmp/ || true",   # 7. 复制文件
    "telnetd"                              # 8. 启动telnet服务（必须）
}
```

### 8.2 runCmd设计建议

1. **单一职责**：每个case只测试一个功能点
2. **参数明确**：使用完整参数名，避免缩写
3. **后台运行**：长时间运行的程序加 `&`

示例：
```python
runCmd="sample_venc 0 --vpssSize=2mp,1mp --camNum=1 > /tmp/venc.log 2>&1 &"
```

### 8.3 时序控制建议

1. **串口操作串行**：避免并发发送命令
2. **Telnet可并行**：launch和monitor独立连接
3. **等待充分**：关键步骤后sleep确保完成
4. **超时保护**：所有轮询都设置合理超时

---

## 九、代码文件索引

| 文件 | 主要功能 |
|------|---------|
| `src/modules/serial_handler.py` | 串口通信、模式检测、preCmd执行 |
| `src/modules/telnet_handler.py` | Telnet连接、runCmd执行、日志监控 |
| `src/modules/test_engine.py` | 测试流程编排、case执行 |
| `src/modules/config_parser.py` | 配置解析、命令列表处理 |
| `src/modules/models.py` | 数据模型、TestCase定义 |

---

## 十、总结

**串口命令流程**：
```
连接串口 → 进入Linux → 登录root → 执行preCmd（包含所有环境准备、网络配置、telnetd启动）
```

**Telnet命令流程**：
```
连接telnet → 登录root → 设置环境变量 → 执行runCmd
```

**关键点**：
1. ✅ 串口用于系统启动和环境准备
2. ✅ Telnet用于执行测试命令和监控
3. ✅ preCmd负责所有环境准备（网络、NFS、telnetd等）
4. ✅ preCmd必须包含ifconfig和telnetd命令
5. ✅ 使用标志位防止preCmd重复执行
6. ✅ 登录后必须确认检测到提示符再继续
7. ✅ 所有命令支持字符串或列表格式
8. ✅ 自动处理引号和换行符

**调试思路**：
1. 🔍 查看serial.log确认preCmd执行情况
2. 🔍 查看launchThread.log确认runCmd执行情况
3. 🔍 检查时间戳判断命令执行顺序
4. 🔍 添加[DEBUG]日志追踪关键变量

---

**文档版本**：v1.0  
**更新日期**：2026-01-26  
**维护者**：TsMpp Team
