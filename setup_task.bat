@echo off
schtasks /Create /TN "LoL Auto Video Creator" /TR "C:\+mywork\+myproject_lol_video_create\run_scheduled.bat" /SC DAILY /ST 09:00 /RL HIGHEST /F
if errorlevel 1 (
    echo ERROR: Run as Administrator
) else (
    echo OK: Task created. Runs daily at 09:00.
)
pause
