@echo off
title DockerConverter CLI

echo.
echo ============================================
echo   DockerConverter CLI  v2.2
echo ============================================
echo.

python  DockerConverter.py samples\docker_commands.txt samples\docker-compose.yml

echo.
cmd
