@echo off
chcp 65001 >nul
cd /d "%~dp0"
python clear_cache.py
echo.
pause
