@echo off
chcp 65001 >nul
title DockerConverter CLI

echo.
echo  ============================================
echo   DockerConverter CLI  v2.2
echo  ============================================
echo.
echo  [CLI 模式] 命令行转换 docker run 命令
echo  [Web UI]  请使用 RunWeb.bat
echo.

:: 检查 Python
python --version >nul 2>&1
if errorlevel 1 (
    echo  [ERROR] Python not found. Please install Python 3.8+.
    pause
    exit /b 1
)

:: 安装依赖
pip show pyyaml >nul 2>&1 || pip install pyyaml -q

echo.
echo  ============================================
echo   命令行用法
echo  ============================================
echo.
echo  基本用法:
echo    python DockerConverter.py [输入文件] [输出文件]
echo.
echo  示例:
echo    python DockerConverter.py
echo      - 默认转换 samples^/docker_commands.txt 到 docker-compose.yml
echo.
echo    python DockerConverter.py my_cmds.txt
echo      - 转换 my_cmds.txt 到 docker-compose.yml
echo.
echo    python DockerConverter.py input.txt output.yml
echo      - 转换 input.txt 到 output.yml
echo.

if "%1"=="" (
    echo  直接回车使用默认参数（samples^/docker_commands.txt）：
) else (
    echo  正在执行：%*
)

echo.
python DockerConverter.py %*

echo.
echo  按任意键退出...
pause >nul
