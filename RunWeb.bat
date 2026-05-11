@echo off
chcp 65001 >nul
title DockerConverter Web UI

echo.
echo  ============================================
echo   DockerConverter Web UI  v2.2
echo  ============================================
echo.
echo  [Web UI 模式] 浏览器访问界面
echo  [CLI 模式]   请使用 RunCLI.bat
echo.

:: 检查 Python
python --version >nul 2>&1
if errorlevel 1 (
    echo  [ERROR] Python not found. Please install Python 3.8+.
    pause
    exit /b 1
)

:: 安装依赖
pip show flask >nul 2>&1 || pip install flask -q
pip show flask-login >nul 2>&1 || pip install flask-login -q
pip show flask-sqlalchemy >nul 2>&1 || pip install flask-sqlalchemy -q
pip show bcrypt >nul 2>&1 || pip install bcrypt -q
pip show python-dotenv >nul 2>&1 || pip install python-dotenv -q

echo.
echo  ============================================
echo   正在启动 Web 服务...
echo  ============================================
echo.
echo  浏览器访问: http://127.0.0.1:5000
echo  首次使用:   http://127.0.0.1:5000/register  (创建管理员账号)
echo  管理面板:   http://127.0.0.1:5000/admin
echo  用户中心:   http://127.0.0.1:5000/profile
echo.
echo  按 Ctrl+C 停止服务
echo.
echo  配置说明: 编辑 .env 文件可修改端口、默认管理员等
echo.

python -m src.docker_converter.app

pause
