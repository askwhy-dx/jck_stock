@echo off
chcp 65001 > nul
cd /d "%~dp0"
REM Maejip (accumulation) report - scheduled 16:30 Mon-Fri (Dante_Maejip_1630).
REM  [0] holiday check: KRX closed -> exit silently, no Slack
REM  [1] toss_capture.py  -> after-close investor/program/short images
REM  [2] build_maejip.js  -> Claude judges flow, writes reports for accumulation stocks only, patches #maejip-section
REM  [3] upload_dashboard.js
REM  [4] build_maejip.js --notify -> Slack: updated or failed
REM  Options: --force (ignore holiday)
set "PY=C:\Users\User\AppData\Local\Programs\Python\Python312\python.exe"
if not exist "%PY%" set "PY=py -3.12"
for /f "tokens=*" %%a in ('powershell -NoProfile -Command "Get-Date -Format yyyy-MM-dd"') do set TODAY=%%a
if not exist logs mkdir logs
set "LOGFILE=logs\maejip_%TODAY%.log"
set "FORCE_FLAG="
if "%~1"=="--force" set "FORCE_FLAG=--force"
set "STATUS=fail"
set "REASON="

node check_holiday.js %FORCE_FLAG% >> "%LOGFILE%" 2>&1
if errorlevel 1 exit /b 0

echo [1] Toss capture
%PY% -u toss_capture.py >> "%LOGFILE%" 2>&1
if errorlevel 1 set "REASON=toss capture failed - login expired?" & goto :notify

echo [2] Build maejip report
node build_maejip.js >> "%LOGFILE%" 2>&1
if errorlevel 1 set "REASON=report build failed" & goto :notify

echo [3] Upload dashboard
node upload_dashboard.js >> "%LOGFILE%" 2>&1
if errorlevel 1 set "REASON=GitHub upload failed" & goto :notify
set "STATUS=ok"
powershell -NoProfile -Command "Start-Sleep -Seconds 60"

:notify
echo [4] Slack notify %STATUS%
node build_maejip.js --notify=%STATUS% --reason="%REASON%" >> "%LOGFILE%" 2>&1
echo === maejip finished status=%STATUS% ===
if "%STATUS%"=="ok" exit /b 0
exit /b 1
