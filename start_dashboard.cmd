@echo off
cd /d "%~dp0"
python tools\bot_dashboard.py --port 8765
pause
