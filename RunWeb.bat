@echo off
chcp 65001 >nul
title DockerConverter Web UI

echo.
echo ============================================
echo   DockerConverter Web UI  v2.2
echo ============================================
echo.
echo  [Web UI]  Browser access
echo  [CLI]     Use RunCLI.bat
echo.

:: Use py launcher (always available when Python is installed on Windows)
py -V >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found. Please install Python 3.8+ and ensure py launcher is available.
    pause
    exit /b 1
)

:: Install dependencies
py -m pip show flask >nul 2>&1 || py -m pip install flask -q
py -m pip show flask-login >nul 2>&1 || py -m pip install flask-login -q
py -m pip show flask-sqlalchemy >nul 2>&1 || py -m pip install flask-sqlalchemy -q
py -m pip show bcrypt >nul 2>&1 || py -m pip install bcrypt -q
py -m pip install python-dotenv -q

echo.
echo ============================================
echo   Starting Web service...
echo ============================================
echo.
echo  Browser:    http://127.0.0.1:5030
echo  First run:  http://127.0.0.1:5030/register (create admin account)
echo  Admin:      http://127.0.0.1:5030/admin
echo  Profile:    http://127.0.0.1:5030/profile
echo.
echo  Press Ctrl+C to stop
echo.
echo  Config: edit .env to change port, default admin, etc.
echo.

py -m src.docker_converter.app

pause
