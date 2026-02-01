@echo off
chcp 65001 >nul
echo ========================================
echo TsMpp 自动化测试系统
echo ========================================
echo.

REM 检查Python是否安装
python --version >nul 2>&1
if errorlevel 1 (
    echo 错误: 未找到Python，请先安装Python 3.8或更高版本
    pause
    exit /b 1
)

REM 检查虚拟环境
if not exist ".venv\" (
    echo 首次运行，正在创建虚拟环境...
    python -m venv .venv
    if errorlevel 1 (
        echo 创建虚拟环境失败
        pause
        exit /b 1
    )
    echo 虚拟环境创建成功
    echo.
)

REM 激活虚拟环境
echo 激活虚拟环境...
call .venv\Scripts\activate.bat

REM 检查依赖是否安装
echo 检查依赖包...
pip show pyserial >nul 2>&1
if errorlevel 1 (
    echo 正在安装依赖包...
    pip install -r requirements.txt
    if errorlevel 1 (
        echo 安装依赖包失败
        pause
        exit /b 1
    )
    echo 依赖包安装成功
    echo.
)

REM 检查配置文件
if not exist "data\task.cfg" (
    echo 警告: 未找到data\task.cfg配置文件
    echo 请确保配置文件存在于data目录
    pause
)

if not exist "data\logmap.txt" (
    echo 警告: 未找到data\logmap.txt配置文件
    echo 请确保配置文件存在于data目录
    pause
)

REM 运行主程序
echo 启动测试程序...
echo.
python main.py

REM 保持窗口
if errorlevel 1 (
    echo.
    echo 程序异常退出
    pause
)
