@echo off
REM run_danta.bat - 2026-10-28 danta: Naver trading value top 20 x popular search rank (weekday 09:10)
REM   Steps: holiday check -> screen_danta_popular.py (Slack) -> build_danta_tab.js -> verify_sync -> upload_dashboard.js
REM   2026-10-21~27 overnight 15:10 / open 09:15 / pre-breakout removed 2026-10-28.
cd /d "%~dp0"
set "PY=C:\Users\User\AppData\Local\Programs\Python\Python312\python.exe"
if not exist "%PY%" set "PY=py -3.12"
if not exist logs mkdir logs
for /f "tokens=*" %%a in ('powershell -NoProfile -Command "Get-Date -Format yyyy-MM-dd"') do set TODAY=%%a
set "LOGFILE=logs\danta_%TODAY%.log"
set "FORCE_FLAG="
if "%~1"=="--force" set "FORCE_FLAG=--force"
node check_holiday.js %FORCE_FLAG% >> "%LOGFILE%" 2>&1
if errorlevel 1 exit /b 0
echo [1] screen_danta_popular.py >> "%LOGFILE%"
%PY% -u screen_danta_popular.py >> "%LOGFILE%" 2>&1
if errorlevel 1 exit /b 1
echo [2] build_danta_tab.js >> "%LOGFILE%"
node build_danta_tab.js >> "%LOGFILE%" 2>&1
echo [3] verify_sync.js >> "%LOGFILE%"
node verify_sync.js --no-slack >> "%LOGFILE%" 2>&1
if errorlevel 1 (
    echo verify_sync failed - upload skipped >> "%LOGFILE%"
    exit /b 1
)
echo [4] upload_dashboard.js >> "%LOGFILE%"
node upload_dashboard.js >> "%LOGFILE%" 2>&1
exit /b %errorlevel%
