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
            # 查找VI PIPE ATTR段落
            pipe_pattern = r'VI PIPE.*?(\d+).*?(\d+)x(\d+).*?(\d+\.?\d*)\s*fps'
            matches = re.findall(pipe_pattern, output, re.IGNORECASE)
            
            for match in matches:
                pipe_info = {
                    'pipe_id': int(match[0]),
                    'width': int(match[1]),
                    'height': int(match[2]),
                    'fps': float(match[3])
                }
                result['pipes'].append(pipe_info)
        except Exception as e:
            print(f"解析VI信息失败: {e}")
        
        return result
    
    @staticmethod
    def parse_vpss_info(output: str) -> Dict[str, Any]:
        """解析VPSS proc信息"""
        result = {
            'groups': []  # 每个group的信息
        }
        
        try:
            # 查找VPSS GROUP信息
            group_pattern = r'VPSS\s+GRP\s+(\d+).*?(\d+)x(\d+)'
            group_matches = re.findall(group_pattern, output, re.IGNORECASE)
            
            for match in group_matches:
                group_info = {
                    'group_id': int(match[0]),
                    'width': int(match[1]),
                    'height': int(match[2]),
                    'channels': []
                }
                
                # 查找该group下的channel信息
                chn_pattern = r'CHN\s+(\d+).*?(\d+)x(\d+).*?(\d+\.?\d*)\s*fps'
                chn_matches = re.findall(chn_pattern, output, re.IGNORECASE)
                
                for chn_match in chn_matches:
                    channel_info = {
                        'channel_id': int(chn_match[0]),
                        'width': int(chn_match[1]),
                        'height': int(chn_match[2]),
                        'fps': float(chn_match[3])
                    }
                    group_info['channels'].append(channel_info)
                
                result['groups'].append(group_info)
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
            # 查找VENC CHN信息
            chn_pattern = r'VENC\s+CHN\s+(\d+).*?(\d+)x(\d+).*?(\d+)\s*frame.*?(\d+\.?\d*)\s*fps'
            matches = re.findall(chn_pattern, output, re.IGNORECASE)
            
            for match in matches:
                channel_info = {
                    'channel_id': int(match[0]),
                    'width': int(match[1]),
                    'height': int(match[2]),
                    'frame_count': int(match[3]),
                    'fps': float(match[4])
                }
                result['channels'].append(channel_info)
        except Exception as e:
            print(f"解析VENC信息失败: {e}")
        
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
            'mode': 'unknown'  # linear, dual, wdr
        }
        
        try:
            # 查找frame_id
            frame_pattern = r'frameid[:\s]+(\d+)'
            match = re.search(frame_pattern, output, re.IGNORECASE)
            if match:
                result['frame_id'] = int(match.group(1))
            
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
