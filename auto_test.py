#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
自动化测试启动脚本
"""

import os
import sys
import time
import threading
from src.modules.config_parser import ConfigParser
from src.modules.test_engine import TestEngine

def run_auto_test():
    """自动运行测试而不需要GUI交互"""
    
    # 获取当前目录
    base_dir = os.path.dirname(os.path.abspath(__file__))
    print(f"工作目录: {base_dir}")
    
    # 解析logmap.txt
    logmap_file = os.path.join(base_dir, "logmap.txt")
    try:
        logmap_config = ConfigParser.parse_logmap(logmap_file)
    except Exception as e:
        print(f"解析logmap.txt失败: {e}")
        return
    
    # 找到配置文件
    cfg_dir = os.path.dirname(os.path.abspath(__file__))
    config_files = []
    for f in os.listdir(cfg_dir):
        if f.endswith('.cfg'):
            config_files.append(os.path.join(cfg_dir, f))
    
    print(f"\n找到 {len(config_files)} 个配置文件:")
    for cfg in config_files:
        print(f"  - {os.path.basename(cfg)}")
    
    # 解析配置
    tasks = []
    for cfg_file in config_files:
        try:
            task = ConfigParser.parse_task_config(cfg_file)
            tasks.append(task)
            print(f"\n解析: {os.path.basename(cfg_file)}")
            print(f"  - Task名称: {task.task_name}")
            print(f"  - Case数量: {len(task.case_list)}")
        except Exception as e:
            print(f"解析 {cfg_file} 失败: {e}")
    
    # 运行每个任务
    for task in tasks:
        print(f"\n\n{'='*80}")
        print(f"开始任务: {task.task_name}")
        print(f"{'='*80}")
        
        # 创建测试引擎
        engine = TestEngine(task, logmap_config)
        
        # 直接运行引擎，不需要GUI
        try:
            engine.run()
        except Exception as e:
            print(f"任务执行出错: {e}")
            import traceback
            traceback.print_exc()
        
        print(f"任务完成: {task.task_name}\n")


if __name__ == "__main__":
    run_auto_test()
