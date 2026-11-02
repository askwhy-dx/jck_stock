@echo off
chcp 65001 > nul
cd /d "%~dp0"

REM ============================================================
REM  run_weekly_reflect.bat - Dante 오답노트 (weekly)
REM
REM  Scheduled: 1st of month 12:00 KST (Dante_Reflect_Monthly) — changed 2026-06-19 from weekly Sat 08:00
REM    [1] build_lessons.js          - §6.9 매매일지 -> lessons.json (종결거래 + 보유궤적 + +10일 청산평가)
REM    [2] reflect_agent.js           - 종결거래 -> proposals.json (전략 보완 제안 + 빈도 수치)
REM    [3] build_mistake_notes.js     - 오답노트.md 누적 + 분석_대시보드.html 종료탭 카드 주입
REM    [4] slack_send_proposals.js    - 승인 대기 보완 제안만 Slack 발송 (non-fatal)
REM
REM  대상 = "실제 거래한 종결 종목"만 (후보·관망 채점 노이즈 제거, 2026-06-15 전환).
REM  Note: KRX cache on Saturday is current through THURSDAY (Friday data
REM        arrives at Monday 08:15 collection). 종결거래 회고는 영향 없음.
REM
REM  Output: data/proposals.json (status=pending_review - 사람 승인 필요)
REM          오답노트.md (주차별 누적) · 분석_대시보드.html (종료 탭 카드)
REM  Log:    logs/reflect_YYYY-MM-DD.log
REM ============================================================

if not exist logs mkdir logs
for /f "tokens=*" %%a in ('powershell -NoProfile -Command "Get-Date -Format yyyy-MM-dd"') do set TODAY=%%a
for /f "tokens=*" %%a in ('powershell -NoProfile -Command "Get-Date -Format \"yyyy-MM-dd HH:mm:ss\""') do set RUNTIME=%%a
set "LOGFILE=logs\reflect_%TODAY%.log"

echo === Weekly reflection start [%RUNTIME%] ===
>> "%LOGFILE%" echo ==================================================
>> "%LOGFILE%" echo === Reflect run: %RUNTIME% ===

echo [1/4] Build lessons (종결거래 + 보유궤적 + +10일 청산평가)
node build_lessons.js 2>&1 | powershell -NoProfile -Command "$input | Tee-Object -FilePath '%LOGFILE%' -Append"

echo [2/4] Reflection agent (proposals.json + 빈도 수치)
node reflect_agent.js 2>&1 | powershell -NoProfile -Command "$input | Tee-Object -FilePath '%LOGFILE%' -Append"

echo [3/4] Build mistake notes (오답노트.md + 종료탭 HTML 카드)
node build_mistake_notes.js 2>&1 | powershell -NoProfile -Command "$input | Tee-Object -FilePath '%LOGFILE%' -Append"

REM [4] 보완 제안만 Slack 발송 (발신 전용 · 승인은 터미널). 실패해도 파일은 남으므로 non-fatal.
echo [4/4] Slack send proposals (slack_send_proposals.js)
node slack_send_proposals.js 2>&1 | powershell -NoProfile -Command "$input | Tee-Object -FilePath '%LOGFILE%' -Append"
if errorlevel 1 (
    echo !! Slack proposals send failed - proposals.json kept locally
    >> "%LOGFILE%" echo !! Slack proposals send failed
)

echo.
echo Proposals: data\proposals.json  (review + approve in terminal)
echo Detail log: %LOGFILE%
echo === Weekly reflection finished ===

exit /b 0
