@echo off
cd /d "%~dp0lichess-bot"
powershell -NoProfile -Command "$p = Get-CimInstance Win32_Process | Where-Object { $_.Name -like 'python*' -and $_.CommandLine -like '*lichess-bot.py*' }; if ($p) { exit 2 }"
if errorlevel 2 (
  echo Lichess bot is already running. Not starting a second instance.
  pause
  exit /b 1
)
python lichess-bot.py -l bot.log
pause
