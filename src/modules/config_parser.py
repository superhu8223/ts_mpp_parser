#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
配置文件解析模块
解析task.cfg和logmap.txt配置文件
"""

import re
import os
from typing import List, Dict, Any, Optional
from .models import (
    TestTask, TestCase, SerialConfig, CheckSpec, 
    DebugAction, LogMapConfig, LogPattern
)


class ConfigParser:
    """配置文件解析器"""
    
    @staticmethod
    def parse_logmap(file_path: str) -> LogMapConfig:
        """解析logmap.txt文件"""
        config = LogMapConfig()
        
        if not os.path.exists(file_path):
            print(f"警告: logmap.txt文件不存在: {file_path}")
            return config
        
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # 解析各个清单
        config.serial_emerge = ConfigParser._parse_log_section(content, 'serial_emerge')
        config.serial_error = ConfigParser._parse_log_section(content, 'serial_error')
        config.serial_warning = ConfigParser._parse_log_section(content, 'serial_warning')
        config.serial_info = ConfigParser._parse_log_section(content, 'serial_info')
        config.serial_ignore = ConfigParser._parse_log_section(content, 'serial_ignore')
        
        config.launch_emerge = ConfigParser._parse_log_section(content, 'launch_emerge')
        config.launch_error = ConfigParser._parse_log_section(content, 'launch_error')
        config.launch_warning = ConfigParser._parse_log_section(content, 'launch_warning')
        config.launch_info = ConfigParser._parse_log_section(content, 'launch_info')
        config.launch_ignore = ConfigParser._parse_log_section(content, 'launch_ignore')
        
        return config
    
    @staticmethod
    def _parse_log_section(content: str, section_name: str) -> List[LogPattern]:
        """解析日志清单章节"""
        patterns = []
        
        # 查找section的起始位置
        pattern = rf'{section_name}\s*=\s*\{{'
        match = re.search(pattern, content, re.IGNORECASE)
        if not match:
            return patterns
        
        start_pos = match.end()
        
        # 查找匹配的结束大括号
        brace_count = 1
        end_pos = start_pos
        for i in range(start_pos, len(content)):
            if content[i] == '{':
                brace_count += 1
            elif content[i] == '}':
                brace_count -= 1
                if brace_count == 0:
                    end_pos = i
                    break
        
        section_content = content[start_pos:end_pos]
        
        # 解析每一项: {"pattern", "name"}
        item_pattern = r'\{\s*"([^"]+)"\s*,\s*"([^"]*)"\s*\}'
        matches = re.findall(item_pattern, section_content)
        
        for pattern_str, name in matches:
            patterns.append(LogPattern(pattern=pattern_str, event_name=name))
        
        return patterns
    
    @staticmethod
    def parse_task_config(file_path: str) -> TestTask:
        """解析task.cfg文件"""
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"配置文件不存在: {file_path}")
        
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # 移除注释
        content = ConfigParser._remove_comments(content)
        
        # 解析串口配置
        serial_config = ConfigParser._parse_serial_config(content)
        
        # 解析基本配置
        task_name = ConfigParser._extract_value(content, 'task_name', '')
        evb_ip = ConfigParser._extract_value(content, 'evb_ip', '192.168.1.10')
        evb_port = int(ConfigParser._extract_value(content, 'evb_port', '23'))
        
        # 解析烧录配置
        fip_name = ConfigParser._extract_value(content, 'fip_name', None)
        kernel_name = ConfigParser._extract_value(content, 'kernel_name', None)
        rootfs_name = ConfigParser._extract_value(content, 'rootfs_name', None)
        userfs_name = ConfigParser._extract_value(content, 'userfs_name', None)
        
        burn_fip_cmd = ConfigParser._extract_value(content, 'burn_fip_cmd', None)
        burn_kernel_cmd = ConfigParser._extract_value(content, 'burn_kernel_cmd', None)
        burn_rootfs_cmd = ConfigParser._extract_value(content, 'burn_rootfs_cmd', None)
        burn_userfs_cmd = ConfigParser._extract_value(content, 'burn_userfs_cmd', None)
        
        burn_flash = ConfigParser._extract_bool(content, 'burn_flash', True)
        
        each_case_need_reboot = ConfigParser._extract_bool(
            content, 'eachCaseNeedReboot', True
        )
        
        # 解析case列表
        case_list = ConfigParser._parse_case_list(content)
        
        # 创建TestTask对象
        task = TestTask(
            task_name=task_name,
            serial_config=serial_config,
            evb_ip=evb_ip,
            evb_port=evb_port,
            fip_name=fip_name,
            kernel_name=kernel_name,
            rootfs_name=rootfs_name,
            userfs_name=userfs_name,
            burn_fip_cmd=burn_fip_cmd,
            burn_kernel_cmd=burn_kernel_cmd,
            burn_rootfs_cmd=burn_rootfs_cmd,
            burn_userfs_cmd=burn_userfs_cmd,
            burn_flash=burn_flash,
            case_list=case_list,
            each_case_need_reboot=each_case_need_reboot
        )
        
        return task
    
    @staticmethod
    def _remove_comments(content: str) -> str:
        """移除注释"""
        lines = content.split('\n')
        result = []
        for line in lines:
            # 查找#号位置（不在字符串内的）
            in_string = False
            for i, char in enumerate(line):
                if char == '"':
                    in_string = not in_string
                elif char == '#' and not in_string:
                    line = line[:i]
                    break
            result.append(line)
        return '\n'.join(result)
    
    @staticmethod
    def _parse_serial_config(content: str) -> SerialConfig:
        """解析串口配置"""
        port = ConfigParser._extract_value(content, 'port', 'COM1')
        baudrate = int(ConfigParser._extract_value(content, 'baudrate', '115200'))
        databits = int(ConfigParser._extract_value(content, 'databits', '8'))
        parity = ConfigParser._extract_value(content, 'parity', 'none')
        stopbits = int(ConfigParser._extract_value(content, 'stopbits', '1'))
        flowcontrol = ConfigParser._extract_value(content, 'flowcontrol', 'none')
        
        return SerialConfig(
            port=port,
            baudrate=baudrate,
            databits=databits,
            parity=parity,
            stopbits=stopbits,
            flowcontrol=flowcontrol
        )
    
    @staticmethod
    def _extract_value(content: str, key: str, default: Any) -> Any:
        """提取配置值"""
        # 先匹配带引号的值
        pattern_quoted = rf'{key}\s*=\s*"([^"]*)"'
        match = re.search(pattern_quoted, content)
        if match:
            return match.group(1)

        # 再尝试无引号的简易值（到空白/注释/换行/逗号/大括号为止）
        pattern_plain = rf'{key}\s*=\s*([^\s#\n\r\{{\}},]+)'
        match = re.search(pattern_plain, content)
        if match:
            return match.group(1)

        return default
    
    @staticmethod
    def _extract_bool(content: str, key: str, default: bool) -> bool:
        """提取布尔值"""
        value = ConfigParser._extract_value(content, key, str(default))
        if isinstance(value, str):
            return value.lower() in ['true', 'yes', '1']
        return bool(value)
    
    @staticmethod
    def _parse_case_list(content: str) -> List[TestCase]:
        """解析测试用例列表"""
        cases = []
        
        # 查找case_list的起始位置
        pattern = r'case_list\s*=\s*\{'
        match = re.search(pattern, content)
        if not match:
            return cases
        
        start_pos = match.end()
        
        # 查找匹配的结束大括号
        brace_count = 1
        end_pos = start_pos
        for i in range(start_pos, len(content)):
            if content[i] == '{':
                brace_count += 1
            elif content[i] == '}':
                brace_count -= 1
                if brace_count == 0:
                    end_pos = i
                    break
        
        case_list_content = content[start_pos:end_pos]
        
        # 分割每个case（通过顶层的大括号）
        case_blocks = ConfigParser._split_case_blocks(case_list_content)
        
        for case_block in case_blocks:
            case = ConfigParser._parse_single_case(case_block)
            if case:
                cases.append(case)
        
        return cases
    
    @staticmethod
    def _split_case_blocks(content: str) -> List[str]:
        """分割case块"""
        blocks = []
        brace_count = 0
        current_block = []
        in_block = False
        
        for char in content:
            if char == '{':
                brace_count += 1
                if brace_count == 1:
                    in_block = True
                    current_block = []
                else:
                    current_block.append(char)
            elif char == '}':
                brace_count -= 1
                if brace_count == 0 and in_block:
                    blocks.append(''.join(current_block))
                    in_block = False
                else:
                    current_block.append(char)
            elif in_block:
                current_block.append(char)
        
        return blocks
    
    @staticmethod
    def _parse_single_case(case_content: str) -> Optional[TestCase]:
        """解析单个测试用例"""
        try:
            # 提取基本字段
            case_id = int(ConfigParser._extract_field_value(case_content, 'case_id', '0'))
            case_name = ConfigParser._extract_field_value(case_content, 'case_name', '')

            # 解析runCmd（必须使用{}列表格式）
            run_cmd = ConfigParser._parse_cmd_list(case_content, 'runCmd')
            if not run_cmd:
                raise ValueError("runCmd 必须使用 { } 列表格式，例如 runCmd={\"cmd1\"}")
            
            stream_num = int(ConfigParser._extract_field_value(case_content, 'streamNum', '1'))
            hold_time = ConfigParser._extract_field_value(case_content, 'holdtime', '100')
            
            # 尝试转换holdtime为整数，如果失败则保持字符串
            try:
                hold_time = int(hold_time)
            except ValueError:
                pass  # 保持字符串（如"longtime"）
            
            # 提取可选字段（必须使用{}列表格式）
            uboot_cmd = ConfigParser._parse_cmd_list(case_content, 'ubootCmd')
            b_need_reboot_str = ConfigParser._extract_field_value(case_content, 'bNeedReboot', None)
            b_need_reboot = None
            if b_need_reboot_str:
                b_need_reboot = b_need_reboot_str.lower() in ['true', 'yes', '1']
            
            b_need_ctrl_c_str = ConfigParser._extract_field_value(case_content, 'bNeedCtrlC', None)
            b_need_ctrl_c = None
            if b_need_ctrl_c_str:
                b_need_ctrl_c = b_need_ctrl_c_str.lower() in ['true', 'yes', '1']
            
            # 解析preCmd（必须使用{}列表格式）
            pre_cmd = ConfigParser._parse_cmd_list(case_content, 'preCmd')
            
            # 解析postCmd
            post_cmd = ConfigParser._parse_cmd_list(case_content, 'postCmd')
            
            # 解析runCmdChecks
            run_cmd_checks = ConfigParser._parse_check_spec(case_content, 'runCmdChecks')
            
            # 解析debugActions
            debug_actions = ConfigParser._parse_debug_actions(case_content)
            
            return TestCase(
                case_id=case_id,
                case_name=case_name,
                run_cmd=run_cmd,
                stream_num=stream_num,
                hold_time=hold_time,
                uboot_cmd=uboot_cmd,
                pre_cmd=pre_cmd,
                post_cmd=post_cmd,
                b_need_reboot=b_need_reboot,
                b_need_ctrl_c=b_need_ctrl_c,
                run_cmd_checks=run_cmd_checks,
                debug_actions=debug_actions
            )
        except Exception as e:
            print(f"解析case失败: {e}")
            return None
    
    @staticmethod
    def _extract_field_value(content: str, key: str, default: Any) -> Any:
        """提取字段值"""
        # 尝试带引号的值
        pattern = rf'{key}\s*=\s*"([^"]*)"'
        match = re.search(pattern, content)
        if match:
            return match.group(1)
        
        # 尝试不带引号的值（数字或标识符）
        pattern = rf'{key}\s*=\s*([^,\}}\s]+)'
        match = re.search(pattern, content)
        if match:
            return match.group(1).strip()
        
        return default
    
    @staticmethod
    def _parse_cmd_list(content: str, key: str) -> List[str]:
        """解析命令列表"""
        cmds = []
        pattern = rf'{key}\s*=\s*\{{'
        match = re.search(pattern, content)
        if not match:
            return cmds
        
        start_pos = match.end()
        
        # 查找匹配的结束大括号
        brace_count = 1
        end_pos = start_pos
        for i in range(start_pos, len(content)):
            if content[i] == '{':
                brace_count += 1
            elif content[i] == '}':
                brace_count -= 1
                if brace_count == 0:
                    end_pos = i
                    break
        
        cmd_content = content[start_pos:end_pos]
        
        # 提取所有带引号的命令，支持转义的双引号
        cmd_pattern = r'"((?:[^"\\]|\\.)*)"'
        matches = re.findall(cmd_pattern, cmd_content)
        for m in matches:
            cmds.append(m.replace('\\"', '"'))
        
        return cmds
    
    @staticmethod
    def _parse_check_spec(content: str, key: str) -> Optional[CheckSpec]:
        """解析检查规格"""
        pattern = rf'{key}\s*=\s*\{{'
        match = re.search(pattern, content)
        if not match:
            return None
        
        start_pos = match.end()
        
        # 查找匹配的结束大括号
        brace_count = 1
        end_pos = start_pos
        for i in range(start_pos, len(content)):
            if content[i] == '{':
                brace_count += 1
            elif content[i] == '}':
                brace_count -= 1
                if brace_count == 0:
                    end_pos = i
                    break
        
        check_content = content[start_pos:end_pos]
        
        spec = CheckSpec()
        
        # 解析各个字段
        spec.vi_size = ConfigParser._parse_list_field(check_content, 'viSize')
        spec.vi_min_fps = [int(x) for x in ConfigParser._parse_list_field(check_content, 'viMinFps')]
        spec.vpss_in_size = ConfigParser._parse_list_field(check_content, 'vpssInSize')
        spec.vpss_crop_size = ConfigParser._parse_list_field(check_content, 'vpssCropSize')
        spec.vpss_out_size = ConfigParser._parse_list_field(check_content, 'vpssOutSize')
        spec.vpss_min_fps = [int(x) for x in ConfigParser._parse_list_field(check_content, 'vpssMinFps')]
        spec.venc_size = ConfigParser._parse_list_field(check_content, 'vencSize')
        spec.venc_min_fps = [int(x) for x in ConfigParser._parse_list_field(check_content, 'vencMinFps')]
        spec.venc_max_bitrate = [int(x) for x in ConfigParser._parse_list_field(check_content, 'vencMaxBitrate')]
        
        return spec
    
    @staticmethod
    def _parse_list_field(content: str, key: str) -> List[str]:
        """解析列表字段"""
        pattern = rf'{key}\s*=\s*\{{([^}}]*)\}}'
        match = re.search(pattern, content)
        if not match:
            return []
        
        values_str = match.group(1)
        # 分割并清理值
        values = [v.strip() for v in values_str.split(',') if v.strip()]
        return values
    
    @staticmethod
    def _parse_debug_actions(content: str) -> List[DebugAction]:
        """解析调试动作列表"""
        actions = []
        
        pattern = r'debugActions\s*=\s*\{'
        match = re.search(pattern, content)
        if not match:
            return actions
        
        start_pos = match.end()
        
        # 查找匹配的结束大括号
        brace_count = 1
        end_pos = start_pos
        for i in range(start_pos, len(content)):
            if content[i] == '{':
                brace_count += 1
            elif content[i] == '}':
                brace_count -= 1
                if brace_count == 0:
                    end_pos = i
                    break
        
        actions_content = content[start_pos:end_pos]
        
        # 分割每个action
        action_blocks = ConfigParser._split_case_blocks(actions_content)
        
        for action_block in action_blocks:
            try:
                time_gap = int(ConfigParser._extract_field_value(action_block, 'timeGap', '0'))
                action_cmd = ConfigParser._extract_field_value(action_block, 'actionCmd', '')
                action_checks = ConfigParser._parse_check_spec(action_block, 'actionChecks')
                
                if action_checks:
                    actions.append(DebugAction(
                        time_gap=time_gap,
                        action_cmd=action_cmd,
                        action_checks=action_checks
                    ))
            except Exception as e:
                print(f"解析debugAction失败: {e}")
        
        return actions
