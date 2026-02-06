#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Proc信息解析模块
解析vi/vpss/venc/sys/cpm/isp等proc信息
"""

import re
from typing import Dict, List, Any, Optional


class ProcParser:
    """Proc信息解析器"""
    
    @staticmethod
    def parse_vi_info(output: str) -> Dict[str, Any]:
        """解析VI proc信息"""
        result = {
            'pipes': []  # 每个pipe的信息
        }
        
        try:
            lines = output.split('\n')
            
            # 尝试多种正则表达式模式
            patterns = [
                r'VI\s+PIPE\s+(\d+).*?(\d+)\s*x\s*(\d+).*?(\d+\.?\d*)\s*fps',
                r'PIPE\s+(\d+).*?(\d+)\s*x\s*(\d+)',
                r'PipeId=(\d+).*?Resolution=(\d+)x(\d+)',
            ]
            
            for pattern in patterns:
                matches = re.findall(pattern, output, re.IGNORECASE | re.DOTALL)
                if matches:
                    print(f"[VI调试] 使用模式找到 {len(matches)} 条匹配")
                    for match in matches:
                        if len(match) >= 3:
                            pipe_info = {
                                'pipe_id': int(match[0]),
                                'width': int(match[1]),
                                'height': int(match[2]),
                                'fps': float(match[3]) if len(match) > 3 and match[3] else 0
                            }
                            result['pipes'].append(pipe_info)
                    break
            
            if not result['pipes']:
                print(f"[VI调试] 未找到VI PIPE数据，原始输出长度={len(output)}")
        except Exception as e:
            print(f"[VI调试] 解析VI信息失败: {e}")
        
        return result
    
    @staticmethod
    def parse_vpss_info(output: str) -> Dict[str, Any]:
        """解析VPSS proc信息"""
        result = {
            'groups': []  # 每个group的信息
        }
        
        try:
            lines = output.split('\n')
            
            # 寻找 "VPSS CHN WORK STATUS" 部分
            vpss_chn_work_status_idx = -1
            for i, line in enumerate(lines):
                if 'VPSS CHN WORK STATUS' in line:
                    vpss_chn_work_status_idx = i
                    break
            
            if vpss_chn_work_status_idx < 0:
                # 如果没有找到，返回空结果
                return result
            
            # 跳过标题行，开始解析数据行
            # 数据行格式：GrpID  ChnID Enable  Depth  Width Height SendOk    Framerate    bDouble
            # 注意：行可能包含日志时间戳前缀 [YYYY-MM-DD HH:MM:SS.mmm]
            for i in range(vpss_chn_work_status_idx + 2, len(lines)):
                line = lines[i].strip()
                
                # 移除可能的日志时间戳前缀
                if line.startswith('[') and ']' in line:
                    line = line[line.index(']') + 1:].strip()
                
                # 遇到空行或其他标题行，停止解析
                if not line or '---' in line or '═' in line or not line[0].isdigit():
                    break
                
                # 分割数据行
                parts = line.split()
                if len(parts) >= 8:
                    try:
                        grp_id = int(parts[0])
                        chn_id = int(parts[1])
                        enable = int(parts[2])
                        # depth = int(parts[3])
                        width = int(parts[4])
                        height = int(parts[5])
                        send_ok = int(parts[6])  # 通道输出帧数
                        framerate = int(parts[7])
                        
                        # 查找或创建对应的group
                        group_info = None
                        for g in result['groups']:
                            if g['group_id'] == grp_id:
                                group_info = g
                                break
                        
                        if group_info is None:
                            group_info = {
                                'group_id': grp_id,
                                'width': width,
                                'height': height,
                                'channels': []
                            }
                            result['groups'].append(group_info)
                        
                        # 添加channel信息（包括SendOk）
                        channel_info = {
                            'channel_id': chn_id,
                            'enable': enable,
                            'width': width,
                            'height': height,
                            'fps': framerate,
                            'send_ok': send_ok  # 添加SendOk字段
                        }
                        group_info['channels'].append(channel_info)
                    except (ValueError, IndexError) as e:
                        # 跳过无法解析的行
                        continue
        except Exception as e:
            print(f"解析VPSS信息失败: {e}")
        
        return result
    
    @staticmethod
    def parse_venc_info(output: str) -> Dict[str, Any]:
        """解析VENC proc信息"""
        result = {
            'channels': []  # 每个通道的信息
        }
        
        try:
            lines = output.split('\n')
            
            # 第一步：从"VENC CHN ATTR 1"中提取Width和Height
            attr_idx = -1
            for i, line in enumerate(lines):
                if 'VENC CHN ATTR 1' in line:
                    attr_idx = i
                    print(f"[VENC调试] 找到'VENC CHN ATTR 1'在line {i}")
                    break
            
            # 先初始化channel信息（从ATTR中）
            if attr_idx >= 0:
                # 跳过标题行，找到数据行
                for i in range(attr_idx + 2, len(lines)):
                    line = lines[i].strip()
                    
                    # 移除可能的日志时间戳前缀
                    if line.startswith('[') and ']' in line:
                        line = line[line.index(']') + 1:].strip()
                    
                    # 遇到空行或其他标题行，停止解析
                    if not line or '---' in line or '═' in line:
                        break
                    
                    # 不以数字开头则跳过
                    if not line or not line[0].isdigit():
                        continue
                    
                    # 分割数据行: NO. Width Height Type ...
                    parts = line.split()
                    if len(parts) >= 3:
                        try:
                            chn_id = int(parts[0])
                            width = int(parts[1])
                            height = int(parts[2])
                            
                            # 查找或创建channel
                            channel_info = None
                            for ch in result['channels']:
                                if ch['channel_id'] == chn_id:
                                    channel_info = ch
                                    break
                            
                            if channel_info is None:
                                channel_info = {'channel_id': chn_id}
                                result['channels'].append(channel_info)
                            
                            # 设置宽高
                            channel_info['width'] = width
                            channel_info['height'] = height
                            print(f"[VENC调试] 从ATTR1提取：Channel {chn_id}: width={width}, height={height}")
                        except (ValueError, IndexError) as e:
                            print(f"[VENC调试] ATTR1解析失败: {line}, 错误: {e}")
                            continue
            
            # 第二步：从"VENC CHNL INFO"中提取StartOk
            chnl_info_idx = -1
            for i, line in enumerate(lines):
                if 'VENC CHNL INFO' in line:
                    chnl_info_idx = i
                    print(f"[VENC调试] 找到'VENC CHNL INFO'在line {i}")
                    break
            
            if chnl_info_idx >= 0:
                # 跳过标题行，找到数据行
                for i in range(chnl_info_idx + 2, len(lines)):
                    line = lines[i].strip()
                    
                    # 移除可能的日志时间戳前缀
                    if line.startswith('[') and ']' in line:
                        line = line[line.index(']') + 1:].strip()
                    
                    # 遇到空行或其他标题行，停止解析
                    if not line or '---' in line or '═' in line:
                        break
                    
                    # 不以数字开头则跳过
                    if not line or not line[0].isdigit():
                        continue
                    
                    # 分割数据行: NO. Inq InqOk Start StartOk Config ...
                    # 索引：      0   1   2     3     4       5
                    parts = line.split()
                    if len(parts) >= 5:
                        try:
                            chn_id = int(parts[0])
                            start_ok = int(parts[4])  # StartOk在第5列（索引4）
                            
                            # 查找或创建channel
                            channel_info = None
                            for ch in result['channels']:
                                if ch['channel_id'] == chn_id:
                                    channel_info = ch
                                    break
                            
                            if channel_info is None:
                                channel_info = {'channel_id': chn_id}
                                result['channels'].append(channel_info)
                            
                            # 设置StartOk
                            channel_info['start_ok'] = start_ok
                            print(f"[VENC调试] 从CHNL INFO提取：Channel {chn_id}: start_ok={start_ok}")
                        except (ValueError, IndexError) as e:
                            print(f"[VENC调试] CHNL INFO解析失败: {line}, 错误: {e}")
                            continue
            
            # 如果两个部分都没找到，尝试正则备选方案
            if not result['channels']:
                print(f"[VENC调试] 未找到标准格式，尝试正则表达式")
                # 尝试多种正则模式
                width_height_pattern = r'Width\s+(\d+)\s+Height\s+(\d+)'
                matches = re.findall(width_height_pattern, output, re.IGNORECASE)
                if matches:
                    for idx, (w, h) in enumerate(matches):
                        channel_info = {
                            'channel_id': idx,
                            'width': int(w),
                            'height': int(h)
                        }
                        result['channels'].append(channel_info)
                        print(f"[VENC调试] 正则提取Channel {idx}: width={w}, height={h}")
            
            if result['channels']:
                print(f"[VENC调试] 共提取{len(result['channels'])}个通道")
            else:
                print(f"[VENC调试] 未能提取任何通道数据")
        except Exception as e:
            print(f"[VENC调试] 解析VENC信息失败: {e}")
        
        return result
    
    @staticmethod
    def parse_sys_info(output: str) -> Dict[str, Any]:
        """解析SYS proc信息"""
        result = {
            'bindings': []  # 绑定关系
        }
        
        try:
            # 解析绑定关系
            binding_pattern = r'(\w+)\((\d+)\)\s*->\s*(\w+)\((\d+)\)'
            matches = re.findall(binding_pattern, output)
            
            for match in matches:
                binding = {
                    'src_mod': match[0],
                    'src_id': int(match[1]),
                    'dst_mod': match[2],
                    'dst_id': int(match[3])
                }
                result['bindings'].append(binding)
        except Exception as e:
            print(f"解析SYS信息失败: {e}")
        
        return result
    
    @staticmethod
    def parse_isp_stat(output: str) -> Dict[str, Any]:
        """解析ISP stat信息"""
        result = {
            'frame_id': 0,
            'fps': 0.0,
            'mode': 'unknown'  # linear, dual, wdr
        }
        
        try:
            # 查找frame_id（注意可能是frame_id或frameid）
            frame_pattern = r'frame.?id[:\s]+(\d+)'
            match = re.search(frame_pattern, output, re.IGNORECASE)
            if match:
                result['frame_id'] = int(match.group(1))
            
            # 查找frame rate (帧率)
            fps_pattern = r'frame\s+rate[:\s]+(\d+\.?\d*)'
            match = re.search(fps_pattern, output, re.IGNORECASE)
            if match:
                result['fps'] = float(match.group(1))
            
            # 检测模式
            if 'linear' in output.lower():
                result['mode'] = 'linear'
            elif 'dual' in output.lower():
                result['mode'] = 'dual'
            elif 'wdr' in output.lower():
                result['mode'] = 'wdr'
        except Exception as e:
            print(f"解析ISP stat信息失败: {e}")
        
        return result
    
    @staticmethod
    def parse_free_mem(output: str) -> int:
        """解析free命令输出，获取空闲内存(KB)"""
        try:
            # 查找Mem:行的第4列（free列）
            lines = output.split('\n')
            for line in lines:
                if 'Mem:' in line:
                    parts = line.split()
                    if len(parts) >= 4:
                        return int(parts[3])
        except Exception as e:
            print(f"解析free内存失败: {e}")
        
        return 0
    
    @staticmethod
    def parse_top_idle(output: str) -> float:
        """解析top命令输出，获取CPU idle百分比"""
        try:
            # 查找CPU行
            idle_pattern = r'(\d+\.?\d*)%\s*id'
            match = re.search(idle_pattern, output)
            if match:
                return float(match.group(1))
        except Exception as e:
            print(f"解析top idle失败: {e}")
        
        return 0.0
    
    @staticmethod
    def resolution_to_string(width: int, height: int) -> str:
        """将分辨率转换为字符串格式（如2mp, 1mp等）"""
        pixels = width * height
        mp = pixels / 1000000.0
        
        if mp >= 1.9:
            return "2mp"
        elif mp >= 0.9:
            return "1mp"
        else:
            return f"{width}x{height}"
    
    @staticmethod
    def string_to_resolution(res_str: str) -> tuple:
        """将分辨率字符串转换为(width, height)"""
        res_map = {
            '2mp': (1920, 1080),
            '1mp': (1280, 720),
            '720p': (1280, 720),
            '1080p': (1920, 1080),
            '4k': (3840, 2160)
        }
        
        if res_str.lower() in res_map:
            return res_map[res_str.lower()]
        
        # 尝试解析 widthxheight 格式
        if 'x' in res_str:
            parts = res_str.split('x')
            try:
                return (int(parts[0]), int(parts[1]))
            except:
                pass
        
        return (0, 0)
