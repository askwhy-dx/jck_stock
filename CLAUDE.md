# stock — 단테 주식 분석·자동화

단테 기법(밥그릇·공구리·224/112 장기이평)으로 종목을 스캔·분석하고, `분석_대시보드.html` 을 공개 GitHub Pages 로 발행하고 Slack 으로 알린다.

- 공개 URL: https://jichang999-dotcom.github.io/tes2/financial/분석_대시보드.html (리포 `jichang999-dotcom/tes2`, **공개**)

## 읽는 순서

1. 이 파일
2. `AI단테.md` — §0 업로드 규칙 · §4 분석 프로토콜(가격 산정 §4.3/§4.4) · §6 현재 종목 현황(활성·미체결 SSOT)
3. `docs/AUTOMATION_README.md` — 파이프라인 단계·.env·트러블슈팅
4. `sop_테마발굴_보조지표.md` — 아침 회차 스캐너 필터·점수 정의

## 폴더 구조

| 폴더 | 내용 |
|---|---|
| 루트 | 실행 스크립트 전부 + 코드가 읽는 문서(`AI단테*.md` · `sop_테마발굴_보조지표.md` · `오답노트.md`) + `stocks.json` · `scorecard.json` · `분석_대시보드.html` |
| `docs/` | **`CHANGELOG.md`(완료 변경 이력)** + **`기술정리.md`(기술 문서 통합본 · 기술 변경 시 여기를 갱신, 나머지 참고 문서는 고치지 않는 원본 보존용)** + 코드가 읽지 않는 참고 문서 (AUTOMATION_README · 장기이평돌파 · 돌눌재 보강안 · 레벨업 시스템 · 학습진화 루프 · 모닝브리프) |
| `tools/` | 파이프라인이 안 쓰는 수동·1회성·폐기 스크립트 (mail_send 폐기 · quality_rescan_reminder 폐기 · delete_github_images · send_flow_sim_slack · test_trade_status_capture · backtest_inverted_hammer3 · ess_screen). 경로는 `..` 기준으로 보정됨 |
| `tools/levelup/` | 주식 레벨업 엑셀·구글시트 작업 (read_xlsx · build_unified · push_to_sheets · simplify_sheet · build_preview_html · verify + 산출물). `credentials.json` 은 현재 없음 |
| `backup/` | 모든 `.bak` 백업 (`backup/config/` · `backup/data/` 는 원위치별) |
| `config/` · `data/` · `logs/` · `reports/` · `images/` | 설정 · 스캐너 산출/캐시 · 실행 로그 · 리포트 · 차트 캡처 |

⚠️ **파이프라인 스크립트는 루트에서 옮기지 않는다.** 전부 `__dirname` · `Path(__file__).parent` · `../.env` 기준 경로를 쓰고, 파이프라인 `eval_forward.py` 가 `backtest_scanners.py` 를 import 한다. 옮기면 에러 없이 데이터를 못 찾는다.

## 핵심 파일

| 파일 | 역할 |
|---|---|
| `AI단테.md` · `AI단테_아카이브.md` · `AI단테_매매일지.md` | 마스터 / 옛 이력 / 종결 매매일지 (3-tier) |
| `stocks.json` | 활성 종목 목록. §6 · 대시보드 · 최신 analysis 와 **4종 동기화** 필수. **태그는 전부 `active`** — 후보 분류 없음(매집=체결 여부는 §6 라벨로만 구분) |
| `분석_대시보드.html` | 발행 대상. 탭 = **🎯 손익비**(버튼 id 는 `btn-swing`·`tab-swing` 그대로) · **💼 매입**(26-11-03 · 체결 보유 카드. 카드는 HTML상 `#tab-swing` 에 그대로 두고 페이지 로드 JS 가 `#rr-swing-summary` `data-auto`(🤖자동 관리 코드, auto_intake.js 가 씀)에 없는 활성 카드를 `#maeip-tab` 으로 옮긴다 — reorder_active_cards.js 슬롯 재조립 때문에 HTML 에서 직접 옮기지 말 것) · **🍚 밥그릇3** · 🏁 종료 · 📊 요약 · **🇺🇸 USA**(`#usa-tab-summary`, 26-10-27 · `../jck_stock-usa/daily.py` 가 평일 21:50 교체 · 표시 전용) (2026-10-19 중장기·🌊 눌림·💥 돌파·🧭 테마 탭 삭제 · 2026-10-23 📥 매집 탭 삭제). 스윙 탭 = `#rr-swing-summary` 🎯 손익비 요약표(순위·충족(최신 15:30·19:00 분석 — 맨 위 굵게 매수 판정 `buyVerdict`(🟢 매수·🟡 매수가능·⚪ 대기 + 양봉/음봉) + 가격·양봉(참고)·거래량·외국인·프로그램 한 줄씩 🟢/🔴/⚪, `auto_intake.gateCell`, 밥그릇3 탭 공용)·종목·테마(줄바꿈)·D+·신호일·1차·2차·3차·손절·목표·손절 폭·손익비·승률·재무·공시, `auto_intake.js` 가 08:30 + full 회차 맨 앞 [0s]·분석 직후 [2e] `--summary-only` 로 다시 그림 — `--summary-only` 는 편입 없이 손절·목표 이탈 퇴출도 함). 🤖자동 활성 카드는 **화면에서 숨김**(요약표 안 `<style>`) — 19:00 분석이 카드의 진입 조건을 SSOT 로 읽고 reorder·가격 배너도 카드를 쓰므로 DOM 에서 지우면 안 된다. 요약 탭은 자동 패치 |
| `run_daily_dante.bat` | 파이프라인 진입점 (recommend / full 분기) |
| `claude_dante_analyze.js` | 분석 + HTML 패치 (`patchFlowSections` = 요약 탭 · 눌림·돌파·테마 탭은 삭제돼 마커가 없으면 건너뜀 — body 끝에 붙이지 않음) |
| `collect_theme_flow.py` | 눌림 스캐너 + KRX 전 종목 OHLCV 캐시(`data/krx_ohlcv_cache/`) |
| `collect_breakout_flow.py` | 돌파 스캐너 |
| `collect_theme_rotation.py` | 🧭 테마 (탭·Slack 줄은 2026-10-19 삭제, 계산만 계속) — **반도체 소부장·전공정·후공정만** — 테마 = 종목 분류표 `config/semi_process_map.csv`의 group(소부장·전공정·후공정·제외, 네이버 4테마 180종 수기 분류. 미분류 신규 종목은 [WARN] 로그 → 표에 추가) 눌림 대기 테마 안의 길목 종목 편입 + 추적 풀(`data/theme_pick_pool.json`): ⏳대기 → 🟢길목 도달(저가 ≤ 10일선) → 🎓졸업(편입 종가 +10%) / ❌이탈(종가 < 20일선 × (1-`REMOVE_BUF_PCT`)) / ⌛만료(20거래일). 화면은 추적 풀만. 테스트 `test_theme_pool.py` |
| `build_maejip.js` · `run_maejip.bat` | **미사용(2026-10-23 사용자 지시로 📥 매집 탭·스케줄 `Dante_Maejip_1630`·Slack 삭제)**. 파일만 남음 — 다시 쓰려면 대시보드에 탭·`#maejip-section` 마커부터 복원(`backup/분석_대시보드.html.bak_nomaejip_20261023`) |
| `screen_rr_breakout.py` | 08:30 [B1g] 🎯 손익비 돌파 (2026-10-19) — 최근 15거래일 안(10-19 10→15) max(112,224)선 돌파 신호(양봉·거래량 2배·시총 1,000억) 중 **손절 폭 2~5%**(손절 = 선 −1%)·당일 양봉·신호 몸통 50%+ · 과거 유사(신호 봉 4구간 일치) 30건+ 기대값 플러스 → 기대값 순으로 **올해 악재 공시(DART, 밥그릇3 와 같은 판정) 종목 제외**하며 **최대 5종** + 재무 배지(3개년 매출·순이익 연속 증가, 표시만) → `data/rr_breakout_{날짜}.json`. KRX 캐시 전체를 읽어 ~45초 |
| `screen_bowl3_quality.py` | 08:30 [B1d] 🍚 밥그릇3 = **3구간 눌림**(2026-10-21 개정, 돌파·눌림 pool 의존 폐지) — KRX 캐시에서 1구간(500일 고점→저점 −40%·저점이 112·224 아래) · 2구간(박스 ≥ 하락 기간 0.5배·224 아래 70%) 뒤 장기선 max(112,224) 동시 돌파(양봉·거래량 1.5배·시총 1,000억) → 20거래일 안 첫 눌림(저가 장기선 −1~+5%) **양봉** → **토스 Open API 외국인+기관 5일 순매수 > 0** → 올해 악재 공시 제외. **재무 3년 성장은 표시만**(`fin_ok`). 눌림일부터 **60거래일** 노출(26-11-03 20→60) · score = 수급 강도(5일 순매수 ÷ 20일 평균 거래량 %). 판정 순서·상수는 백테스트(시장 대비 20일 초과 중앙 +1.1%, 수급까지 111건 · 10-21 박스 기간 완화)와 같다 — 근거는 docstring. ⚠️ 토스 클라이언트 = `../jck_stock-usa/toss.py`(토큰 1개 — stock-usa 스크립트와 동시 실행 금지 · 허용 IP 61.36.60.229). 10-21 이전 산출물과 건수 비교 금지 |
| `build_bowl3_tab.js` | 08:30 [B3b] 🍚 밥그릇3 탭(`#bowl3-tab-summary`) — `bowl3_quality_{날짜}.json` 통과 종목 점수 순 최대 5종, 가격은 `auto_intake.scenario(bars,'112+224')` = 손익비와 같은 손절(돌파선 −1%)·1차·목표. **손절 고정**: 처음 들어온 날의 1·2·3차·손절·목표를 `data/bowl3_state.json` 에 저장해 유지, **매 회차(08:30·16:30·19:00) 최신 종가**(`auto_intake.lastClose`) < 손절 또는 > 목표면 삭제 + Slack 「🛑 손절·목표 이탈 — 밥그릇3에서 삭제」(목표는 26-10-28~ · 편입 시 이미 목표 초과면 건너뜀) + 20거래일 재진입 금지 · **최신 종가 < 장기선(max MA112·224, 최신 종가 포함 재계산) × 0.99 면 삭제·편입 제외**(26-11-05 · Slack 같은 🛑 알림 · 재진입 금지 없음 — 선 위로 돌아오면 다시 들어옴 · 편입 시 목표 초과 판정도 `lastClose` 기준) , 스캔에서 빠지거나 5자리 밖으로 밀리면 조용히 정리. 경과 열 D+N(들어온 뒤 거래일). 표시 전용(스윙 편입 없음). **`data/bowl3_watch.json`**(5종 가격 계획·거래량 범위)도 쓴다 → 15:30·19:00 `toss_capture.load_bowl3_watch` 가 캡처 대상에 더하고(stocks.json·§6 불변), 분석기 `loadStocksMeta` 가 tag `bowl3` 로 분석(진입 규칙 = 관찰 목록 거래량, category `bowl3` → verify·카드 패치·Slack 추천/위험·요약 탭 진입표에서 제외) → 충족 점수가 밥그릇3 탭·Slack 표에 들어감 |
| `toss_api_snapshot.py` | 15:30·19:00 full [1] (2026-10-21, `toss_capture.py` 웹 캡처 대체) — 토스 Open API 로 `prices_{날짜}.json`(같은 스키마)·`data/api_chart/{종목}_{날짜}.txt`(일봉)·`{종목}_수급_{날짜}.txt`·`api_snapshot_{날짜}.json`. 분석기가 txt 를 PNG 대신 읽고 당일 외국인·프로그램·양봉·거래량은 기계 주입. ⚠️ 토큰 1개(stock-usa 동시 실행 금지) · 체결강도 판정 폐지(장 마감 뒤라 불필요 · 사용자 10-21, 항상 not_required) · 등락은 KRX 전일 종가 기준(토스 일봉 = NXT 포함). `toss_capture.py` 는 수동 로그인·수동 캡처용으로만 남음(16:30 매집 회차 10-23 삭제) |
| `screen_danta_popular.py` · `run_danta.bat` · `build_danta_tab.js` | ⚡ **단타인기** (2026-10-28 개편 · 탭 버튼·Slack 제목 이름 · 종목 앞에 [코스피]/[코스닥]) — 작업 `Dante_Danta_0910`(평일 09:10, 따라잡기 끔) → 네이버 증권 `m.stock.naver.com/api/stocks/priceTop/{KOSPI,KOSDAQ}`(일반 주식만, ETF·ETN 제외) 합쳐 거래대금 20위 안에서 `searchTop/all`(인기 검색) 순위가 거래대금 순위보다 높은 종목 → `data/danta_pop_{날짜}.json` · Slack 1장 · 단타 탭 `#danta-tab-summary` · verify_sync · 업로드. 매매 = 3거래일 보유(진입·손절·목표 산식 없음). **백테스트 불가**(인기 순위 과거 이력 없음 — 쌓이는 JSON 으로 나중에 성과 측정). 거래량 조건(전일 100%·70%)은 10-28 검토 후 **미채택**(09:10엔 거의 0종 — 근거 기술정리 §5.3d). 표시 전용(자동 편입 아님). **삭제(10-28 사용자 지시)**: 🌙 오버나잇(15:10)·09:15 매수·돌파임박(08:30 [B1h]·[B3c]) — 탭·스케줄 `Dante_Danta_1510`·`Dante_Danta_0915`·08:30 Slack 「⚡ 단타」 줄. `screen_danta_close.py`·`screen_danta_prebreakout.py` 는 파일만 남음(미사용, 백업 `backup/*.bak_popular_20261028`) |
| `auto_intake.js` · `exposure.js` | 08:30 [B3] — **🎯 손익비 섹션만**(2026-10-19~, 눌림·돌파·밥그릇3·테마는 Slack 표시 전용) §6 라인업·stocks.json·스윙 카드에 **자동 편입**, 라인업 **최대 5종**(빈자리만). 퇴출에 ④ 종가 < 손절 추가 — **2026-10-26부터 매 회차**(08:30 + 16:30·19:00 full 맨 앞 [0s] `--summary-only`) `lastClose` = KRX 캐시, 오늘 봉이 없고 15:31 이후면 네이버 15:30 분봉(KRX 공식 종가 · 네이버 일봉·closePrice 는 시간외·NXT 로 계속 바뀌어 쓰면 안 됨). **목표 이탈(종가 > 목표)도 26-10-28부터 매 회차 같은 `lastClose` 로 퇴출**(종전 08:30 T3-U 만). 손절·목표 이탈 퇴출은 Slack 「🛑 손절·목표 이탈」 별도 알림. 카드 제목 끝 **D+N**(편입 후 경과 거래일, 매 08:30 갱신 · `auto_intake_state.json` `dplus`) — 19:00 Slack 종목명 뒤에도 표시. 활성 카드가 0장일 때는 HTML 주석 `🎯 스윙 활성 카드 삽입 위치`(지우지 말 것) 뒤에 넣는다. 종전 설명: 📋 노출 종목을 §6 라인업·stocks.json·스윙 카드에 **자동 편입**(시나리오 §4.3/§4.4 기계 근사 · 라벨 「미체결 🤖자동」) + 자동 퇴출(MA20·60 동시 하회 / 20거래일 미체결 / 목표 초과). 🤖자동 라벨을 체결 등으로 바꾸면 자동 관리에서 빠진다. 재구축·수동 지정은 `node auto_intake.js --rebuild=코드,코드`(라인업에 없는 종목은 「✋ 수동 지정」으로 새로 편입 · 추세 이탈 등으로 건너뛰면 기존 항목 유지) · 점검 `--dry-run` · 테스트 `node test_auto_intake.js`. `exposure.js` = 노출 목록(Slack과 공용) |
| `check_krx_ready.py` | 아침 회차 [B-1] — 직전 영업일 KRX 공개 확인(exit 1 = 미공개 → 08:30 재시도) |
| `slack_send.js` | Slack. 아침 회차 = 「📋 노출 종목」 1장 — **🎯 손익비는 table 블록(순위·종목·테마·승률·재무, 2026-10-19)**, 나머지(밥그릇3·임원매수)는 종목명·점수 줄(SOP §5.4). 08:30 회차 = 노출 카드(🎯 손익비 표) + 두 번째 메시지 **🍚 밥그릇3 표**(순위·종목·테마·점수). 16:30·19:00 회차 = **1장**: 🎯 손익비 매수(Block Kit `table` 블록: 종목·충족, 대시보드 요약표 순서) + **🍚 밥그릇3 매수**(같은 메시지 안 줄 목록 — table 은 메시지당 1개 · 대상 = 대시보드 탭과 같은 `bowl3_watch.json` 5종) · **🚨 위험 종목 섹션 삭제**(10-23 사용자 지시, 그 자리에 밥그릇3) — **매수 대상만 표시**(10-23: `auto_intake.buyVerdict` — 가격 pass + 거래량·외국인·프로그램 3개 중 🟢 3개 매수 · 🟡 2개 매수가능, ⚪ 대기는 뺀다 · **양봉은 판정 제외, 양봉/음봉 참고 표시** · 5/5 `all_required_met` 판정 자체는 그대로) · table 블록이 거부되면 코드 블록 표로 자동 재발송(2026-10-19). 종전: ✅ 활성은 **진입조건 충족 개수별 묶음**(묶음 제목 = header 블록 큰 글씨 + 🟢전부/🟡절반 이상/🔴절반 미만 · 종목마다 `*종목명*  코스피 · 테마` + `❌ 미충족 항목`(fail·unknown) 2줄) + 🌟 추천 + 🚨 위험 종목(존치). 테마 = `data/naver_theme_map.json` 중 구성종목 최소 1개. 규칙 SSOT = `AI단테.md` §4.6.16 |
| `verify_sync.js` | 발행 전 라인업 SSOT 게이트 |
| `upload_dashboard.js` | GitHub 업로드 (HTML + MD 3종) |
| `config/holiday_kr.json` | KRX 휴장일 |
| `config/semi_process_map.csv` | 🧭 테마 탭 반도체 종목 분류표 (code·name·group·note·check). group = 소부장(전공정 소재·부품) · 전공정(장비·인프라) · 후공정(패키징·테스트·기판·소켓, 소재 포함) · 제외. 엑셀로 `group` 만 고치면 다음 실행부터 반영. `check`=확인필요 11종 미검수. 로그에 「분류표 미분류」 경고가 뜨면 그 종목을 행으로 추가 |

## 스케줄 (Windows 작업 스케줄러)

| 시각 | 작업 | 모드 |
|---|---|---|
| 08:30 월~금 (`Dante_Auto_0815` — 작업 이름만 0815, 트리거는 08:30 1개) | 눌림·돌파·밥그릇3·테마 스캔 → 추천 패치 → 🤖 노출 자동 편입 → 업로드 → Slack(노출 종목 1장) · KRX 전일 확정봉 기준(~08시 공개). 08:30은 KRX 미공개여도 진행(`check_krx_ready.py`의 skip은 08:25 전 실행에만 작동) | recommend |
| 08:30 토 (`Dante_Auto_Sat`, 2026-10-17~) | 평일 아침 회차와 같음(디커플링 브리프 + 노출 종목 Slack · 업로드 · 🤖자동 편입 포함). `run_daily_dante.bat --force` 로 주말 휴장 체크를 우회 → 중복 가드도 꺼지므로 **따라잡기 실행(StartWhenAvailable) 꺼 둠**. 일반 권한(LeastPrivilege) 등록. 금요일 KRX 확정봉이 토요일 08:30에 공개되는지는 미확인 — 미공개면 목요일 데이터 기준 | recommend |
| 09:10 월~금 (`Dante_Danta_0910`, 26-10-28) | `run_danta.bat` — ⚡ 단타인기(거래대금×인기) → 단타 탭 → verify → 업로드 → Slack · 따라잡기 끔 | danta |
| **16:30** 월~금 (`Dante_Auto_1530` — 이름만 1530, 트리거 16:30 · 2026-10-23 15:30→16:30 이동 · 따라잡기 실행 없음 · LeastPrivilege) | 19:00과 같은 full 회차(맨 앞 [0s]에서 손절 점검 — 2026-10-26부터 매 회차 최신 종가 < 손절이면 무조건 삭제) · **15:30엔 토스 당일 외국인 행이 없고(장중 잠정치가 마감 무렵 사라졌다 확정치로 다시 올라옴) 프로그램도 확정 전이라 16:30으로 옮김**(10-23 16:54 수동 실행에서 9종 모두 외국인 확인) · 따라잡기를 켜면 늦게 돈 회차가 eve 슬롯을 먹어 19:00을 막으므로 꺼 둠 | full |
| 19:00 월~금 (`Dante_Auto_1900`) | **[0s] 손절 점검**(최신 종가 < 손절 → 무조건 삭제, 10-26~) → 토스 캡처(현재가·수급 이미지) → 종목 분석(6지 판정) → 업로드 → Slack · **장중 토스 캡처 회차는 없다**(10:30·15:10 작업은 2026-10-07부터 미등록) | full |
| 21:50 월~금 (`Dante_USA_2150`, 26-10-27) | `../jck_stock-usa/run_daily_usa.bat` — 미국 손익비·밥그릇3 → 🇺🇸 USA 탭 교체 → 업로드 → Slack(같은 채널). 이 폴더 HTML·`upload_dashboard.js` 를 쓴다 | usa |

스케줄에 등록된 bat 은 `run_daily_dante.bat`(평일 08:30·16:30·19:00 3회 + 토 08:30) · `run_danta.bat`(09:10) · `run_weekly_reflect.bat`(`Dante_Reflect_Monthly`, 매월 1일). `run_maejip.bat` 은 10-23 스케줄 삭제(미사용). 원본 XML 백업 `backup/scheduler/*.bak_20261023.xml`.

## 수동 실행 = Claude 에게 요청

사용자는 bat 을 직접 눌러 실행하지 않는다. 스케줄에 없는 실행은 이 폴더에서 Claude 에게 요청하고, Claude 가 아래 명령으로 돌린다. 수동 실행용 bat 은 두지 않는다(새로 만들지 말 것). `PY` = `C:/Users/User/AppData/Local/Programs/Python/Python312/python.exe`.

| 요청 | 명령 (stock 폴더에서) | 주의 |
|---|---|---|
| 파이프라인 한 회차 수동 실행 | PowerShell: `cmd /c "C:\Users\User\Desktop\test\jck_stock\run_daily_dante.bat" --force` | **bat 은 전체 경로로** — 상대경로는 Git Bash·PowerShell(Set-Location 후에도) 모두 「인식되지 않음」으로 시작도 못 한 이력(2026-10-14). 모드는 실행 시각으로 정해진다(08·09시만 recommend). 강제하려면 `$env:HERMES_RUN_MODE='recommend'`(종목스캔) / `'full'`(토스 캡처·종목분석 = 19:00 보고서). **업로드·Slack 까지 실제로 나간다** |
| 발행 없이 점검만 | `cmd /c run_daily_dante.bat --force --dry-run` | 분석·업로드·Slack 생략 |
| 토스 로그인(세션 만료 시) | `PY toss_capture.py --login` | 브라우저가 뜨고 **사용자가 직접 로그인**해야 함 |
| 토스 캡처만 | `PY -u toss_capture.py` | |
| 디커플링 브리프 | `PY -u decouple_scan.py` (Slack 발송은 `--slack`) | |
| 네이버 테마 사전만 재수집 | `PY -c "import collect_theme_rotation as m; m.load_theme_map(True)"` | **장중에** 돌릴 것 — 장 전엔 구성종목이 빈 목록으로 온다(전부 0종이면 저장 안 하는 가드 있음) |
| 테마 탭 데이터만 재계산 | `PY -u collect_theme_rotation.py` (`--dry-run` 은 저장 안 함) | 셸에서 직접 돌리면 `PYTHONIOENCODING=utf-8` 필수(없으면 cp949 로 로그 이모지에서 죽고 **저장 안 됨**) |
| 테마 탭만 대시보드에 반영 | `node -e "const fs=require('fs'),{renderThemeSection}=require('./claude_dante_analyze.js');const tr=JSON.parse(fs.readFileSync('data/theme_rotation_YYYY-MM-DD.json','utf-8'));const f='분석_대시보드.html';fs.writeFileSync(f,fs.readFileSync(f,'utf-8').replace(/<section id=\"theme-cards\"[\s\S]*?<\/section>/,()=>renderThemeSection(tr).trim()),'utf-8')"` → 발행은 `node upload_dashboard.js` | `patchFlowSections` 는 눌림·돌파·요약 탭까지 다시 그리므로 쓰지 말고 테마 섹션만 교체 |
| 월간 회고 수동 | `cmd /c run_weekly_reflect.bat` | Slack 발송됨 |
| 스케줄러 재등록·삭제 | 관리자 권한 `schtasks /create /xml ... /f` (원본 XML: `test/archive/20260914_scheduler_retime/`) | |

recommend 순서: B-1 KRX 공개 확인 → B0 디커플링 → B1 눌림 → B1b 돌파 → B1c forward 평가 → **B1g 손익비 → B1d 밥그릇3**(10-21 순서 변경 · B1h ⚡단타는 10-28 삭제) → **B1e 테마** → **B1f 임원매수**(`screen_insider_buys.py` → `data/insider_buys_{날짜}.json`, Slack 노출 종목 맨 아래 「👔 임원매수 코스피/코스닥」 최대 5종 = 종목명(테마). 조건 = 직전 30일 간부진 장내매수 + 5일선 112/224 상향돌파(**7거래일 내**, 10-16 60→7) 후 고점 대비 −5% 이상 눌림·장기선 −3~+5% + 3개년 당기순이익 흑자(B안). 테스트 `test_insider_buys.py`. **표시 전용 — exposure.js·자동 편입에 넣지 말 것**) → B2 분석·패치 → **B3 노출 자동 편입(🎯 손익비만)** → 2c verify → 업로드 → Slack.

## 작업 규칙 (깨지면 조용히 틀리는 것들)

- **업로드는 `node upload_dashboard.js` 로만.** 실행 전 `commitMsg` 를 이번 변경으로 **한 줄** 교체(백슬래시 이스케이프 금지) → `node --check`. `git init` 금지. **이미지 업로드 영구 금지.**
- 발행·새로고침은 사용자가 시켰을 때만. 에이전트 기본 범위는 「수정」까지.
- `verify_sync.js` 수동 실행은 **`--no-slack`** (안 붙이면 실제 경보 발송). `slack_send.js` 는 `require()` 금지(실발송).
- 가격(진입·손절·목표)은 §4.3/§4.4 로직으로만 — 재량 금지. 카드·가격 수정 전 메모리 `hub_dante_dashboard_card_rules` 확인.
- **백업은 `backup/` 에 넣는다** (원래 위치가 `config/`·`data/` 면 `backup/config/`·`backup/data/`). 이름은 `원본명.bak_{사유}_{YYYYMMDD}`. 예외: 루트 `stocks.json.bak` 은 `toss_capture.py` 가 매 회차 덮어쓰는 현재용 백업이라 제자리.
- `.bat` 에 새로 쓰는 줄은 **ASCII 전용 + CRLF**. 한글이 필요하면 `.py` 로.
- Python 은 bat 의 `%PY%`(Python312 고정) 사용. bare `python` 은 Hermes venv 로 가로채져 스캐너가 0종으로 조용히 죽은 이력.
- KRX 캐시의 휴장일 파일은 `rows` 0개 — 거래일 판정은 파일 존재가 아니라 행수로.
- 대시보드는 공개다. 회사·개인 데이터 넣지 말 것.

## 변경 이력

완료된 수정 내역은 이 파일에 쓰지 않고 `docs/CHANGELOG.md` 에 날짜별로 보관한다 (새 항목은 맨 위). 이 파일은 **현재 상태와 규칙만** 유지한다.
