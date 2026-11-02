@echo off
chcp 65001 > nul
cd /d "%~dp0"

echo === run_daily_dante.bat 시작 ===
echo 작업 디렉토리: %CD%
echo.

REM ============================================================
REM  run_daily_dante.bat - Dante automation pipeline entry
REM
REM  Scheduled runs (KST):
REM    08:30  recommend: scanners + recommend patch + exposure auto-intake (KRX prior-day bars)
REM    15:30  full: Toss capture + stock analysis (2026-10-19)
REM    19:00  full: Toss capture + stock analysis (10:30/15:10 tasks not registered since 2026-10-07)
REM
REM  08:15 회차 파이프라인:
REM    [0]  check_holiday.js
REM    [B1] collect_theme_flow.py   - KRX OpenAPI 밥그릇 스캔 + 기존 보유/후보 추세 점검 (theme_flow JSON: candidates + holdings_trend)
REM    [B2] claude_dante_analyze.js --recommend-only - 추천 카드 + 추세 점검 섹션 패치
REM    [2c] verify_sync.js          - 라인업 SSOT 게이트 (불일치 시 발행 차단)
REM    [2d] reorder_active_cards.js - 최신 analysis entry_conditions 전부 충족 우선 재정렬
REM    [3]  upload_dashboard.js
REM    [4]  timeout 60
REM    [5]  slack_send.js --recommend-only
REM    [6]  write_summary.js
REM
REM  19:00 full-run pipeline:
REM    [0] check_holiday.js
REM    [1] toss_capture.py          - 9 차트 PNG + 가격 JSON
REM    [1b] recall_similar.js       - 유사 패턴 회상 (현재 종목 vs 종결거래 풀) -> recall_latest.json
REM    [1c] claude_dante_analyze.js --prices-only - 캡처 시세를 카드 4배너 현재가에 주입 (Claude 실패 무관 · non-fatal)
REM    [2] claude_dante_analyze.js  - 4지 판정 + HTML 패치(4배너 현재가 포함) + 판정 원장 append
REM    [2c] verify_sync.js          - 라인업 SSOT 게이트 (stocks.json<->§6<->analysis 불일치 시 발행 차단)
REM    [2b] score_predictions.js    - 원장(predictions.jsonl) 채점 -> scorecard.json
REM    [2d] reorder_active_cards.js - 최신 analysis entry_conditions 전부 충족 우선 재정렬
REM    [3] upload_dashboard.js
REM    [4] timeout 60
REM    [5] slack_send.js
REM    [6] write_summary.js
REM
REM  Logs:
REM    - per-run detail: logs/dante_auto_YYYY-MM-DD_HHMM.log
REM    - daily summary:  logs/dante_summary_YYYY-MM-DD.log (3 runs appended)
REM
REM  Options:
REM    run_daily_dante.bat              normal run
REM    run_daily_dante.bat --dry-run    skip analyze/upload/mail
REM    run_daily_dante.bat --force      ignore holiday calendar
REM ============================================================

if not exist logs mkdir logs

for /f "tokens=*" %%a in ('powershell -NoProfile -Command "Get-Date -Format yyyy-MM-dd"') do set TODAY=%%a
for /f "tokens=*" %%a in ('powershell -NoProfile -Command "Get-Date -Format HHmm"') do set HHMM=%%a
for /f "tokens=*" %%a in ('powershell -NoProfile -Command "Get-Date -Format \"yyyy-MM-dd HH:mm:ss\""') do set RUNTIME=%%a
set "LOGFILE=logs\dante_auto_%TODAY%_%HHMM%.log"

REM --- Python interpreter pin ---
REM  bare `python` was being hijacked by the Hermes Agent venv (no pandas/numpy),
REM  which silently killed the breakout/bowl scanners -> 0 pullback candidates.
REM  Pin to the system Python312 (pandas 2.3.3). Fallback to the py launcher.
set "PY=C:\Users\User\AppData\Local\Programs\Python\Python312\python.exe"
if not exist "%PY%" set "PY=py -3.12"

set "DRY_FLAG="
set "FORCE_FLAG="
if "%~1"=="--dry-run" set "DRY_FLAG=--dry-run"
if "%~1"=="--force" set "FORCE_FLAG=--force"
if "%~2"=="--dry-run" set "DRY_FLAG=--dry-run"
if "%~2"=="--force" set "FORCE_FLAG=--force"

set "PIPE_STATUS=success"
set "PIPE_REASON="
set "NOTIFY_STATUS=skipped"

REM 회차 분기: 08:00~08:59 = 밥그릇 스캐너 회차 (장 시작 전 추천 갱신)
set "RUN_MODE=full"
if "%HHMM:~0,2%"=="08" set "RUN_MODE=recommend"
REM 2026-09-14: recommend run moved 08:15 -> 09:30. 09:00-09:59 is also the recommend window.
if "%HHMM:~0,2%"=="09" set "RUN_MODE=recommend"

REM [수동 트리거] 헤르메스(유빐)가 텔레그램으로 회차 강제 지정. 스케줄러는 미설정 -> 영향 없음
REM   HERMES_RUN_MODE=recommend(종목스캔) / full(종목분석)
if defined HERMES_RUN_MODE set "RUN_MODE=%HERMES_RUN_MODE%"

REM Guard slots: scheduled runs are 08:30 (morning) and 19:00 (eve); am/pm slots remain for manual runs.
REM   RUN_MODE는 recommend/full 2종뿐이라 full 마커 하나를 3개가 공유 -> 첫 풀런이
REM   나머지를 막는 버그가 있었음(2026-06-08). 시간대 슬롯으로 분리해 같은 슬롯
REM   재발사(08:12/08:15 중복, 놓친 회차 아침 따라잡기)만 차단한다.
set "HH=%HHMM:~0,2%"
set "SLOT=eve"
if %HH% LSS 17 set "SLOT=pm"
if %HH% LSS 13 set "SLOT=am"
if "%HH%"=="08" set "SLOT=morning"
REM 2026-09-14: 09 must be morning too, otherwise the 09:30 run takes the am slot and blocks 10:30.
if "%HH%"=="09" set "SLOT=morning"

REM Analysis phase classification (2026-06-19): before 09:00 = pre-analysis / 09:00-20:00 = regular
REM 2026-09-14: the morning recommend slot (08:xx/09:xx) keeps the pre label.
set "PHASE=regular"
if %HH% LSS 9 set "PHASE=pre"
if "%SLOT%"=="morning" set "PHASE=pre"

echo === Dante automation pipeline start [%RUNTIME%] ===
>> "%LOGFILE%" echo ==================================================
>> "%LOGFILE%" echo === Run: %RUNTIME% (DRY=%DRY_FLAG% FORCE=%FORCE_FLAG%) ===
>> "%LOGFILE%" echo ==================================================

REM === 중복 실행 가드: 같은 날 같은 회차(RUN_MODE) 1회만 ===
REM   StartWhenAvailable 따라잡기로 전날 19:00 등 놓친 회차가 다음날 아침
REM   08시대(recommend)로 둔갑해 08:15 정상 회차와 중복 실행되는 것을 차단.
REM   마커는 시작 시점에 기록(3분 간격 동시 실행도 차단). --force/--dry-run은 예외.
set "RUNMARK=logs\.ran_%TODAY%_%SLOT%.marker"
set "GUARD_ON=1"
if "%FORCE_FLAG%"=="--force" set "GUARD_ON="
if "%DRY_FLAG%"=="--dry-run" set "GUARD_ON="
if defined GUARD_ON (
    if exist "%RUNMARK%" (
        echo [GUARD] %TODAY% %SLOT% 슬롯 이미 실행됨 - 중복 실행 건너뜀
        >> "%LOGFILE%" echo [GUARD] duplicate %SLOT% slot run on %TODAY% skipped ^(marker exists^)
        exit /b 0
    )
    > "%RUNMARK%" echo %RUNTIME% [%LOGFILE%]
)

REM [0] Holiday check
echo [0/5] Holiday check
REM Preserve the producer exit code before logging its output.
node check_holiday.js %FORCE_FLAG% > "%TEMP%\dante_holiday_%TODAY%_%HHMM%.txt" 2>&1
set "HOLIDAY_RC=%errorlevel%"
powershell -NoProfile -Command "Get-Content -LiteralPath '%TEMP%\dante_holiday_%TODAY%_%HHMM%.txt' -Encoding UTF8 | Tee-Object -FilePath '%LOGFILE%' -Append"
if not "%HOLIDAY_RC%"=="0" (
    echo Holiday - skipping pipeline
    >> "%LOGFILE%" echo Holiday skip
    set "PIPE_STATUS=skip"
    set "PIPE_REASON=KRX holiday or weekend"
    set "NOTIFY_STATUS=skipped"
    goto :summary
)

REM [0b] Dependency preflight: 루트 공용 node_modules 자가 점검/복구
REM   stock\ 에는 package.json이 없어 모든 node 스크립트가 루트 ..\node_modules 에서
REM   dotenv 등을 해결한다. 루트 node_modules 삭제/누락 시 toss 캡처/분석은 통과해도
REM   upload_dashboard.js / slack_send.js 가 'Cannot find module dotenv'로 죽어
REM   대시보드 미갱신 + Slack 미발송 = 겉보기엔 "스케줄 미실행" (2026-06-09 사고).
REM   해결: dotenv 부재 감지 -> 루트 npm install 자동 복구 -> 재확인. 실패 시에만 중단.
echo [0b] Dependency preflight ^(node_modules guard^)
if not exist "..\node_modules\dotenv" (
    echo [WARN] root node_modules missing - auto npm install
    >> "%LOGFILE%" echo [0b] root node_modules\dotenv missing - running npm install in root
    pushd ..
    call npm install > "%TEMP%\dante_npm_install.txt" 2>&1
    popd
    powershell -NoProfile -Command "Get-Content -LiteralPath '%TEMP%\dante_npm_install.txt' -Encoding UTF8 | Select-Object -Last 20 | Tee-Object -FilePath '%LOGFILE%' -Append"
)
if not exist "..\node_modules\dotenv" (
    echo !! Dependency recovery failed - pipeline abort
    >> "%LOGFILE%" echo !! root node_modules\dotenv still missing after npm install - abort
    set "PIPE_STATUS=fail"
    set "PIPE_REASON=Root node_modules missing - npm install recovery failed (Step 0b)"
    set "NOTIFY_STATUS=skipped"
    goto :summary
)
>> "%LOGFILE%" echo [0b] dependency OK ^(dotenv present^)

if "%RUN_MODE%"=="recommend" goto :recommend_branch

REM ===== Full run (19:00) =====
REM [0s] 2026-10-26: stop-loss exit first (latest KRX close; swing + bowl3). Runs even if later steps abort. Non-fatal.
node auto_intake.js --summary-only %DRY_FLAG% 2>&1 | powershell -NoProfile -Command "$input | Tee-Object -FilePath '%LOGFILE%' -Append"
node build_bowl3_tab.js %DRY_FLAG% 2>&1 | powershell -NoProfile -Command "$input | Tee-Object -FilePath '%LOGFILE%' -Append"
REM [1] 2026-10-21: Toss Open API snapshot replaces web capture (prices JSON + api_chart txt)
echo [1/5] Toss Open API snapshot (toss_api_snapshot.py)
REM Preserve the producer exit code before logging its output.
%PY% -u toss_api_snapshot.py > "%TEMP%\dante_snapshot_%TODAY%_%HHMM%.txt" 2>&1
set "SNAPSHOT_RC=%errorlevel%"
powershell -NoProfile -Command "Get-Content -LiteralPath '%TEMP%\dante_snapshot_%TODAY%_%HHMM%.txt' -Encoding UTF8 | Tee-Object -FilePath '%LOGFILE%' -Append"
if not "%SNAPSHOT_RC%"=="0" (
    echo !! Toss API snapshot failed - pipeline abort
    >> "%LOGFILE%" echo !! Toss API snapshot failed
    set "PIPE_STATUS=fail"
    set "PIPE_REASON=Toss API snapshot failed (Step 1)"
    set "NOTIFY_STATUS=skipped"
    goto :summary
)

REM [1b] Refresh similar-pattern recall (current lineup vs closed-trade pool; non-fatal)
echo [1b] Similar-pattern recall (recall_similar.js)
node recall_similar.js 2>&1 | powershell -NoProfile -Command "$input | Tee-Object -FilePath '%LOGFILE%' -Append"

REM [1c] Price-only banner patch: inject captured prices into card 4-banner BEFORE Claude.
REM      Keeps "현재가" banners fresh even if the Claude analyze step later aborts (rate limit etc). Non-fatal.
if "%DRY_FLAG%"=="--dry-run" (
    echo [1c] dry-run: price banner patch skipped
) else (
    echo [1c] Price banner patch ^(claude_dante_analyze.js --prices-only^)
    node claude_dante_analyze.js --prices-only 2>&1 | powershell -NoProfile -Command "$input | Tee-Object -FilePath '%LOGFILE%' -Append"
)

REM [2] Claude Code analyze + HTML patch
echo [2/5] Claude Code analyze (claude_dante_analyze.js)
REM Preserve the producer exit code before logging its output.
node claude_dante_analyze.js %DRY_FLAG% > "%TEMP%\dante_analyze_%TODAY%_%HHMM%.txt" 2>&1
set "ANALYZE_RC=%errorlevel%"
powershell -NoProfile -Command "Get-Content -LiteralPath '%TEMP%\dante_analyze_%TODAY%_%HHMM%.txt' -Encoding UTF8 | Tee-Object -FilePath '%LOGFILE%' -Append"
if not "%ANALYZE_RC%"=="0" (
    echo !! Claude Code analyze failed - pipeline abort
    >> "%LOGFILE%" echo !! Claude Code analyze failed
    set "PIPE_STATUS=fail"
    set "PIPE_REASON=Claude Code analyze failed (Step 2)"
    set "NOTIFY_STATUS=skipped"
    goto :summary
)
REM [2e] 2026-10-19: refresh swing summary (gate score) and bowl3 tab after analysis. Non-fatal.
node auto_intake.js --summary-only %DRY_FLAG% 2>&1 | powershell -NoProfile -Command "$input | Tee-Object -FilePath '%LOGFILE%' -Append"
node build_bowl3_tab.js %DRY_FLAG% 2>&1 | powershell -NoProfile -Command "$input | Tee-Object -FilePath '%LOGFILE%' -Append"
goto :post_analyze

REM ===== 08:15 회차 (Recommend only) =====
:recommend_branch
REM [B-1] 2026-10-01: KRX prev-day data ready? Scheduled runs only.
REM   Before 08:25 (the 08:15 run): if not ready, delete the morning marker and stop,
REM   so the 08:30 trigger retries. If 08:15 succeeded the marker stays and 08:30 is skipped by GUARD.
REM   At 08:30+ it only logs and continues.
if defined GUARD_ON (
    %PY% -u check_krx_ready.py >> "%LOGFILE%" 2>&1
    if errorlevel 1 if "%HHMM%" LSS "0825" (
        echo [B-1] KRX data not ready - retry at 08:30
        del "%RUNMARK%"
        set "PIPE_STATUS=skip"
        set "PIPE_REASON=KRX prev-day data not ready - retry 08:30"
        goto :summary
    )
)
REM [B0] Decoupling pre-reflection brief (moved from 07:30 to 08:15, 2026-06-19). Sends own Slack. non-fatal.
echo [B0] Decoupling brief (decouple_scan.py --slack)
%PY% -u decouple_scan.py --slack 2>&1 | powershell -NoProfile -Command "$input | Tee-Object -FilePath '%LOGFILE%' -Append"
echo [B1/5] Bowl scanner (collect_theme_flow.py)
REM Preserve the producer exit code before logging its output.
%PY% -u collect_theme_flow.py > "%TEMP%\dante_scanner_%TODAY%_%HHMM%.txt" 2>&1
set "SCANNER_RC=%errorlevel%"
powershell -NoProfile -Command "Get-Content -LiteralPath '%TEMP%\dante_scanner_%TODAY%_%HHMM%.txt' -Encoding UTF8 | Tee-Object -FilePath '%LOGFILE%' -Append"
if not "%SCANNER_RC%"=="0" (
    echo !! Bowl scanner failed - pipeline abort
    >> "%LOGFILE%" echo !! collect_theme_flow.py failed
    set "PIPE_STATUS=fail"
    set "PIPE_REASON=Bowl scanner failed (Step B1)"
    set "NOTIFY_STATUS=skipped"
    goto :summary
)

REM [B1b] Breakout scanner (collect_breakout_flow.py) - 12 dolpa tab.
REM   Non-fatal: a breakout failure must not abort the nullim pipeline.
REM   Reads latest offline cache (filled by collect_theme_flow above) + today KST date.
echo [B1b] Breakout scanner (collect_breakout_flow.py)
%PY% -u collect_breakout_flow.py 2>&1 | powershell -NoProfile -Command "$input | Tee-Object -FilePath '%LOGFILE%' -Append"

REM [B1c] Forward-test eval (eval_forward.py) - 12 yoyak tab scanner-eval source (Dante split-buy method).
REM   Non-fatal: feeds forward_scorecard.json consumed by claude_dante_analyze.js renderScannerEval.
echo [B1c] Forward-test eval (eval_forward.py)
%PY% -u eval_forward.py 2>&1 | powershell -NoProfile -Command "$input | Tee-Object -FilePath '%LOGFILE%' -Append"

REM [B1g] RR breakout (screen_rr_breakout.py) - 2026-10-19. 2026-10-21: runs before bowl3 (order rr -> bowl3 -> danta). Top 5 by expected value, stop width 2-5 pct.
REM   Non-fatal. Only this section is auto-intaken by auto_intake.js [B3] (other sections are Slack display only).
echo [B1g] RR breakout (screen_rr_breakout.py)
%PY% -u screen_rr_breakout.py 2>&1 | powershell -NoProfile -Command "$input | Tee-Object -FilePath '%LOGFILE%' -Append"

REM [B1d] Bowl3 quality search (screen_bowl3_quality.py) - "bap-geu-reut 3-beon-jari searching" card.
REM   Non-fatal: reads breakout_flow_{date}.json (from B1b) + DART financials/disclosures.
REM   Filters bowl 3-stage (dolpa/watching) by 3yr sales&net-income growth + clean current-year filings.
echo [B1d] Bowl3 quality search (screen_bowl3_quality.py)
%PY% -u screen_bowl3_quality.py 2>&1 | powershell -NoProfile -Command "$input | Tee-Object -FilePath '%LOGFILE%' -Append"

REM [B1e] Theme rotation (collect_theme_rotation.py) - theme tab source (weekly theme flow + pullback picks).
REM   Non-fatal: reads krx_ohlcv_cache (from B1) + naver_theme_map.json (refetched every 7 days).
echo [B1e] Theme rotation (collect_theme_rotation.py)
%PY% -u collect_theme_rotation.py 2>&1 | powershell -NoProfile -Command "$input | Tee-Object -FilePath '%LOGFILE%' -Append"

REM [B1f] Insider buys (screen_insider_buys.py) - exec non-CEO, stake under 5 pct market buys 30d + net income up 2y.
REM   Non-fatal: Slack morning exposure card line only (not in exposure.js / auto_intake).
echo [B1f] Insider buys (screen_insider_buys.py)
%PY% -u screen_insider_buys.py 2>&1 | powershell -NoProfile -Command "$input | Tee-Object -FilePath '%LOGFILE%' -Append"

echo [B2/5] Claude Code analyze --recommend-only
REM Preserve the producer exit code before logging its output.
node claude_dante_analyze.js --recommend-only %DRY_FLAG% > "%TEMP%\dante_recommend_%TODAY%_%HHMM%.txt" 2>&1
set "RECOMMEND_RC=%errorlevel%"
powershell -NoProfile -Command "Get-Content -LiteralPath '%TEMP%\dante_recommend_%TODAY%_%HHMM%.txt' -Encoding UTF8 | Tee-Object -FilePath '%LOGFILE%' -Append"
if not "%RECOMMEND_RC%"=="0" (
    echo !! Claude Code recommend-only failed - pipeline abort
    >> "%LOGFILE%" echo !! claude_dante_analyze.js --recommend-only failed
    set "PIPE_STATUS=fail"
    set "PIPE_REASON=Recommend-only analyze failed (Step B2)"
    set "NOTIFY_STATUS=skipped"
    goto :summary
)

REM [B3] Exposure auto-intake (auto_intake.js) - exposed names -> active lineup (section6 + stocks.json + card).
REM   Also exits auto-managed names (close < MA20 and MA60 / 20 trading days unfilled / close > target).
REM   Non-fatal: on failure it writes nothing; verify_sync [2c] still gates publishing.
echo [B3] Exposure auto-intake (auto_intake.js)
node auto_intake.js %DRY_FLAG% 2>&1 | powershell -NoProfile -Command "$input | Tee-Object -FilePath '%LOGFILE%' -Append"

REM [B3b] 2026-10-19: bowl3 tab (build_bowl3_tab.js) - top 5 by score, same stop rule as RR. Display only. Non-fatal.
echo [B3b] Bowl3 tab (build_bowl3_tab.js)
node build_bowl3_tab.js %DRY_FLAG% 2>&1 | powershell -NoProfile -Command "$input | Tee-Object -FilePath '%LOGFILE%' -Append"

:post_analyze

REM [2c] Lineup SSOT integrity gate (stocks.json vs section6 vs analysis.json)
REM   On lineup mismatch, block GitHub upload + Slack send (fail-closed). Prevents
REM   the stale-stocks.json Slack misexposure (2026-06-04 active, 06-10 candidate).
REM   On mismatch/parse-failure verify_sync.js posts its own Slack alert then exit 1
REM   -> we stop publishing here and jump to summary.
REM   Capture node's true exit code via temp redirect (same pattern as [3] upload,
REM   outside the paren block to avoid the parse-time paren trap). ASCII only: cmd
REM   under chcp 65001 mis-parses Korean in REM lines (see reference_bat_crlf_requirement).
set "VERIFY_FLAG="
if "%DRY_FLAG%"=="--dry-run" set "VERIFY_FLAG=--no-slack"
echo [2c] Lineup SSOT verify ^(verify_sync.js^)
node verify_sync.js %VERIFY_FLAG% > "%TEMP%\dante_verify_out.txt" 2>&1
set "VERIFY_RC=%errorlevel%"
powershell -NoProfile -Command "Get-Content -LiteralPath '%TEMP%\dante_verify_out.txt' -Encoding UTF8 | Tee-Object -FilePath '%LOGFILE%' -Append"
if not "%VERIFY_RC%"=="0" (
    echo !! Lineup SSOT mismatch - upload/slack blocked
    >> "%LOGFILE%" echo !! Lineup SSOT mismatch - publish blocked
    set "PIPE_STATUS=fail"
    set "PIPE_REASON=Lineup SSOT mismatch (Step 2c)"
    set "NOTIFY_STATUS=skipped"
    goto :summary
)

REM [2b] Score predictions ledger (mature judgments scored; non-fatal)
if "%DRY_FLAG%"=="--dry-run" (
    echo [2b] dry-run: prediction scoring skipped
) else (
    echo [2b] Score predictions ^(score_predictions.js^)
    node score_predictions.js 2>&1 | powershell -NoProfile -Command "$input | Tee-Object -FilePath '%LOGFILE%' -Append"
)

REM [2d] Active card ordering: filled cards first by buy date.
REM   Unfilled sort SSOT is the latest data\dante_analysis_*.json stocks[].entry_conditions:
REM   all required conditions first, then pass count, tier T1/T2/T3, and entry proximity.
REM   Also refreshes the proximity badge next to badge-swing in each card header.
REM   Runs AFTER the price and analysis patches so Slack and dashboard read the same decision.
REM   Fail-closed: missing/invalid analysis JSON or missing active-stock decision exits 1
REM   and leaves the HTML untouched. Stale phase-strip text is never used as a fallback.
REM   Non-fatal for the pipeline: upload proceeds with the previous ordering rather than
REM   a half-rewritten file.
if "%DRY_FLAG%"=="--dry-run" (
    echo [2d] dry-run: active card reorder skipped
) else (
    echo [2d] Active card reorder ^(reorder_active_cards.js^)
    node reorder_active_cards.js 2>&1 | powershell -NoProfile -Command "$input | Tee-Object -FilePath '%LOGFILE%' -Append"
)

REM [3] GitHub upload
REM   ⚠️ 근본버그(2026-06-08 확정): 과거 if 블록 안 echo에 unquoted 괄호가 있었음 ->
REM      `>> "%LOGFILE%" echo !! GitHub upload failed (analysis kept locally)`.
REM      cmd 괄호블록 파서가 echo 텍스트의 `( )` 를 블록 경계로 오인 -> if 조건과 무관하게
REM      `set PIPE_STATUS=fail`/`set PIPE_REASON=...` 가 매 회차 무조건 실행 + echo는 엉뚱히
REM      리다이렉트돼 로그에 마커도 안 남음. 결과: upload_dashboard.js가 exit 0(대시보드 정상
REM      발행)인데도 모든 회차가 허위 FAIL + "GitHub upload failed (Step 3)" 기록.
REM   해결: (1) if 블록 안 echo의 괄호 제거(아래 line). (2) 업로드를 괄호블록 밖으로 빼
REM      node의 참 종료코드를 %UPLOAD_RC%에 직접 포착(괄호 밖이라 parse-time 함정 없음),
REM      powershell은 로그/콘솔 표시 전용. SET 값 안 따옴표 괄호(예: "...(Step 3)")는 안전.
if "%DRY_FLAG%"=="--dry-run" goto :upload_dry
echo [3/5] GitHub financial/ upload (upload_dashboard.js)
node upload_dashboard.js > "%TEMP%\dante_upload_out.txt" 2>&1
set "UPLOAD_RC=%errorlevel%"
powershell -NoProfile -Command "Get-Content -LiteralPath '%TEMP%\dante_upload_out.txt' -Encoding UTF8 | Tee-Object -FilePath '%LOGFILE%' -Append"
if not "%UPLOAD_RC%"=="0" (
    echo !! GitHub upload failed - notify still proceeds
    >> "%LOGFILE%" echo !! GitHub upload failed - analysis kept locally
    set "PIPE_STATUS=fail"
    set "PIPE_REASON=GitHub upload failed (Step 3)"
)
goto :after_upload
:upload_dry
echo [3/5] dry-run: GitHub upload skipped
>> "%LOGFILE%" echo [3/5] dry-run skip
:after_upload

REM  2026-08-05: timeout /t 60 -> powershell Start-Sleep 60 교체.
REM  stdin 이 리디렉션된 비대화형 실행에서 timeout 이 Input redirection is not supported 로 즉시 실패해
REM  60초 대기가 통째로 건너뛰어졌다. 그 탓에 GitHub Pages 전파 40~60초 전에 슬랙 링크가 나갔다.
REM  powershell Start-Sleep 은 스케줄러/터미널 양쪽에서 동작한다.
REM [4] Wait 60s for GitHub Pages cache
if "%DRY_FLAG%"=="--dry-run" (
    echo [4/5] dry-run: 60s wait skipped
) else (
    echo [4/5] GitHub Pages cache wait 60s
    >> "%LOGFILE%" echo [4/5] sleep 60
    powershell -NoProfile -Command "Start-Sleep -Seconds 60"
)

REM [5] Slack send
set "SLACK_FLAG="
if "%RUN_MODE%"=="recommend" set "SLACK_FLAG=--recommend-only"
set "DANTE_PHASE=%PHASE%"
echo [5/5] Slack Webhook send (slack_send.js %SLACK_FLAG%)
REM Preserve the producer exit code before logging its output.
node slack_send.js %SLACK_FLAG% %DRY_FLAG% > "%TEMP%\dante_slack_%TODAY%_%HHMM%.txt" 2>&1
set "SLACK_RC=%errorlevel%"
powershell -NoProfile -Command "Get-Content -LiteralPath '%TEMP%\dante_slack_%TODAY%_%HHMM%.txt' -Encoding UTF8 | Tee-Object -FilePath '%LOGFILE%' -Append"
if not "%SLACK_RC%"=="0" (
    echo !! Slack send failed
    >> "%LOGFILE%" echo !! Slack send failed
    set "NOTIFY_STATUS=failed"
    if "%PIPE_STATUS%"=="success" set "PIPE_STATUS=fail"
    if "%PIPE_REASON%"=="" set "PIPE_REASON=Slack send failed (Step 5)"
) else (
    if "%DRY_FLAG%"=="--dry-run" (
        set "NOTIFY_STATUS=skipped"
    ) else (
        set "NOTIFY_STATUS=sent"
    )
)

echo === Pipeline complete ===
>> "%LOGFILE%" echo === Pipeline complete ===

:summary
REM [6] Append daily summary
echo [6/6] Append daily summary (write_summary.js)
node write_summary.js --status=%PIPE_STATUS% --start="%RUNTIME%" --notify=%NOTIFY_STATUS% --reason="%PIPE_REASON%" 2>&1 | powershell -NoProfile -Command "$input | Tee-Object -FilePath '%LOGFILE%' -Append"

echo.
echo Detail log: %LOGFILE%
echo Daily summary: logs\dante_summary_%TODAY%.log
echo.
echo === Pipeline finished (status=%PIPE_STATUS%) ===
echo.

REM 자동화 파이프라인: 완료 후 창 자동 종료 (pause 제거)
echo.

if "%PIPE_STATUS%"=="success" (
    exit /b 0
) else if "%PIPE_STATUS%"=="skip" (
    exit /b 0
) else (
    exit /b 1
)

REM Return pipeline failure to manual callers and Task Scheduler.
if "%PIPE_STATUS%"=="fail" exit /b 1
exit /b 0
