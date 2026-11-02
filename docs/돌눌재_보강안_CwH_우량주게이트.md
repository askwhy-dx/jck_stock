# 돌·눌·재 보강안 — 컵위드핸들(CwH) + 펀더멘탈 우량주 게이트 구현안

> 작성 2026-07-14 · 트리거 = 월가캡틴 '돌·눌·재' 원칙 검토(§4.8 눌림 스캐너 보강)
> **핵심 원칙: 둘 다 hard가 아니라 soft(배지·가점·정렬)로 도입** — pass/fail을 절대 뒤집지 않아 기존 돌·눌·재와 충돌 없음(아래 §0 실증).
>
> **상태 (2026-07-15 갱신)**
> - **도입안 B (펀더멘탈 우량주 ①~⑤ soft 게이트) = ✅ 구현 완료** (§2 · 아래 §6 구현 기록)
> - **도입안 A (CwH 배지) = ⏸ 미구현** — 사용자 결정은 받았으나(§5) **§4.1 백테스트 선행 조건 미충족**. `backtest_pullback_strategy.py`로 CwH 동반 vs 미동반 승률·기대값 검증 통과 후에만 편입.

---

## 0. 검증 근거 (2026-07-14 · 읽기전용 시뮬)

실제 돌·눌·재 통과 12종(recommend_pool 영속추적)에 `screen_quality_value.py` 실측:

| 게이트 | 통과 | 결론 |
|---|---|---|
| ⑥ 가격위치(바겐세일) | **3/12 (25%)** | 🚨 돌·눌·재와 정면 충돌 → **⑥ 반드시 제외** |
| 제안 ①~⑤(펀더멘탈만) ≥4/5 | **8/12 (67%)** | ✅ soft 게이트로 커버리지 고갈 없음 |
| ①~⑤ 완전 5/5 | 2/12 | hard는 신호 고갈 → soft 권장 |

- **충돌 원인**: 돌·눌·재 대상은 이미 224 돌파해 **52주 상단권**으로 올라온 주. 우량주 ⑥은 "52주 하위 40%·낙폭 35%↑ 바겐세일"을 찾음 = 방향 정반대.
- **역설 사례**: 코웨이 = 펀더멘탈 5/5 완벽인데 ⑥ 때문에 기존 6조건 B등급. 돌·눌·재 축에선 ⭐최우선이어야 함 → **①~⑤ 전용 게이트가 별도로 필요**함을 실증.

---

## 1. 도입안 A — 컵위드핸들(CwH) soft 배지

### 1.1 파일 · 수정 지점
| 파일 | 수정 |
|---|---|
| `collect_theme_flow.py` | ① `THRESHOLDS`에 `cwh_*` 키 추가 ② 신규 `detect_cup_with_handle(df, thr)` ③ `evaluate_pullback_signal()` 내에서 `detect_double_bottom` 옆에 호출 → `setup["cup_handle"]` 병합 (pass/fail 불변) |
| `slack_send.js` / `claude_dante_analyze.js` | 눌림 카드 배지에 `🏆 CwH` 노출 (쌍바닥 `🪑`와 동일 렌더 경로) |
| `backtest_pullback_strategy.py` | CwH 동반 셋업 승률 별도 집계(편입 전 검증) |

### 1.2 `detect_cup_with_handle` 알고리즘 (OHLCV만 · 결정적)
`detect_double_bottom`의 swing 검출 기계를 재사용하되 **W형이 아닌 U형 단일바닥 + 핸들**을 판정:

1. **컵 구간**: 최근 `cwh_window`(초안 60영업일) 내 좌측 rim(고점 L)·바닥(저점 B)·우측 rim(고점 R) 탐색.
2. **컵 조건**:
   - 깊이 `cwh_depth_min~max` = (rim−bottom)/rim ∈ **[0.15, 0.40]** (얕으면 눌림, 깊으면 붕괴)
   - 좌우 rim 수평성 |R/L−1| ≤ `cwh_rim_tol` (초안 **0.05**)
   - 바닥 둥근 정도 = 바닥 부근 `cwh_round_bars`(초안 5) 저점들의 표준편차/가격 ≤ `cwh_round_tol` (V자 급반등 배제)
3. **핸들 조건**: 우측 rim 이후 얕은 눌림
   - 핸들 깊이 ≤ 컵 깊이 × `cwh_handle_max_ratio` (초안 **1/3**)
   - 핸들이 상단부(컵 상반부)에서 형성 · 저거래량(핸들 평균 < 컵 평균 × `cwh_handle_vol_max`, 초안 0.8)
4. **돌파 트리거(재돌파)**: `today_close > handle_high` = 기존 2단계 반등확인과 **동일하게 종가 기준** → 별도 로직 불필요, `cup_handle.breakout=True`만 표기.
5. 반환: `{"cup_handle":bool, cup_depth_pct, rim_diff_pct, handle_depth_pct, handle_vol_ratio, cup_left/bottom/right, handle_high}`

### 1.3 통합(soft) · 가점
- `evaluate_pullback_signal` 마지막에 `setup.update({"cup_handle": detect_cup_with_handle(...)})` — **pass 여부 미변경**.
- 점수 가점: 쌍바닥 선례(모양만은 우위 없음 검증)와 동일하게 **거래량 확인 동반 시에만** `cwh_bonus`(초안 +5). 형태만이면 배지만.
- 배지: `🏆 CwH`(돌파 확인) / `🍵 CwH대기`(핸들 형성 중).

### 1.4 THRESHOLDS 초안
```python
"cwh_window": 60, "cwh_depth_min": 0.15, "cwh_depth_max": 0.40,
"cwh_rim_tol": 0.05, "cwh_round_bars": 5, "cwh_round_tol": 0.06,
"cwh_handle_max_ratio": 0.34, "cwh_handle_vol_max": 0.8, "cwh_bonus": 5,
```
> ⚠️ 전부 **백테스트로 튜닝 후 확정**. 편입 전 `backtest_pullback_strategy.py`로 CwH 동반 vs 미동반 승률·기대값 비교(쌍바닥 검증 절차 준용).

---

## 2. 도입안 B — 펀더멘탈 우량주 ①~⑤ soft 게이트

### 2.1 파일 · 수정 지점
| 파일 | 수정 |
|---|---|
| `screen_quality_value.py` | `judge()`에 **①~⑤만 집계하는 `grade_fundamental(code)`** 헬퍼 노출(⑥ 가격위치 제외·재무 DART 재사용). 기존 6조건 스캔은 불변 |
| `collect_theme_flow.py` | 스캔 말미, **stage=='확인'(pass) 종목에 한해** `grade_fundamental` 호출 → `setup["quality_fund"]={passed,grade,details}` 부여 + `⭐우량 우선정렬` |
| `slack_send.js` / `claude_dante_analyze.js` | 카드 분류행에 `🅰️/🅱️ 펀더 N/5` 배지 (삼성바이오 카드 `재무 🅱️ B등급 5/6`과 동일 표기) |

### 2.2 크로스필터 로직(soft)
```
for setup in confirmed_setups:          # stage=='확인'만 (0~10종 → DART 콜 소수)
    q = grade_fundamental(setup.code)   # ①매출↑ ②순익≥50억&흑자 ③부채/자본≤1 ④부채/자산<0.8 ⑤자산↑
    setup.quality_fund = q              # passed:0~5, grade
# 정렬: (quality_fund.passed desc, dante_pullback_score desc)  → ⭐ 상단 노출
# ⚠️ 탈락 없음: 진입 판정은 사용자 4지 선다(§4.6.13) 유지
```
- **⑥ 가격위치 절대 미포함** (§0 실증: 포함 시 75% 부당 탈락).
- 등급: `passed 5=🅰️우량 · 4=🅱️ · ≤3=배지 없음`(soft badge 컷 = **≥4**).

### 2.3 DART 성능
- '확인' 통과분(보통 0~10종)에만 호출 → `fnlttSinglAcntAll` 1콜=3개년 · `data/dart_cache` 재사용 → rate limit 무관(`screen_bowl3_quality.py` 동일 패턴).

---

## 3. 충돌 회피 체크리스트 (검증 완료)

| 잠재 충돌 | 판정 | 조치 |
|---|---|---|
| CwH가 V자 눌림 배제 | soft라 pass 불변 | 🏆 배지·가점만 |
| 우량주 ⑥ vs 52주 상단 돌파 | 🚨 실증 충돌(75% 탈락) | **⑥ 제외·①~⑤만** |
| 커버리지 고갈 | soft ≥4/5면 67% 생존 | hard 금지·배지/정렬 |
| 4지 선다 사용자 재량 | 자동 배제 금지 | 노출 우선순위까지만 |
| DART rate limit | 확인분 소수만 | 후단 심사 |
| 밥그릇3와 중복 | 돌파축은 bowl3 담당 | **눌림축에 우량주 추가**가 신규 가치 |

---

## 4. 롤아웃 순서 (권장)
1. **시뮬 먼저**: `detect_cup_with_handle` 프로토타입 + `backtest_pullback_strategy.py`로 CwH 동반 승률 검증(쌍바닥처럼). 통과 시에만 편입.
2. **우량주 ①~⑤ soft 게이트** 먼저 반영(구현 단순·즉효·리스크 낮음).
3. CwH 배지 반영(백테스트 통과 후).
4. 각 단계 [[feedback_summer_event_undocumented_change_policy]] 준용 = 수정+문서반영(본 문서·sop_테마발굴_보조지표.md §4)+SOP 갱신.

## 5. 사용자 결정 (2026-07-15 확정)

| 항목 | 결정 | 반영 |
|---|---|---|
| soft badge 컷 | **우량 ≥4/5** (≥5/5 엄격안 불채택) | `FUND_BADGE_CUT = 4` ✅ |
| CwH 가점 · 형태-only 배지 | **가점 +5 유지 · 형태-only도 배지 노출** | ⏸ CwH 백테스트 대기 |
| 우선정렬 1순위 | **펀더멘탈 등급** (기존 dante_pullback_score는 2순위로 강등) | `derive_lifecycle` display 정렬 ✅ |

---

## 6. 구현 기록 (2026-07-15 · 도입안 B만)

### 6.1 변경 파일
| 파일 | 변경 |
|---|---|
| `screen_quality_value.py` | `grade_fundamental(code, dart, bsns_year, end_dd)` + `fund_grade_of()` + `FUND_GRADE`/`FUND_BADGE_CUT` 신설. `score_stock()`을 재사용해 ①~⑤만 집계(⑥은 `6_` prefix로 필터링해 제외). **기존 6조건 스캔 경로 불변** |
| `collect_theme_flow.py` | ① `apply_quality_fund_gate(candidates, end_dd)` 신설 — 공시 필터 직후 호출, 배지 부여, **탈락 없음** ② `update_recommend_pool`의 풀 엔트리에 `quality_fund` 영속 필드 추가 ③ `derive_lifecycle`의 `display.sort` 키를 `(-펀더 passed, -score)`로 교체 |
| `claude_dante_analyze.js` | `renderRecommendationsSection` 카드 헤더에 `🅰️ 우량 펀더 5/5` / `🅱️ 펀더 4/5` 배지(`qb`) 추가 |
| `slack_send.js` | 눌림 노출 카드에 동일 배지. `top3`(LLM 산출)에는 `quality_fund`가 없으므로 **`theme_flow.candidates`에서 code로 조회**(`qfByCode`)해 두 렌더 경로 모두 커버 |

### 6.2 ⚠️ 비자명 함정 (구현 중 발견)
1. **풀 엔트리 화이트리스트**: `update_recommend_pool`이 필드를 명시 나열해 담는 구조라, `quality_fund`를 추가하지 않으면 **조용히 버려져 배지가 영원히 안 뜬다**. `double_bottom`/`db_vol_div`와 같은 위치에 추가함.
2. **정렬 지점이 2곳**: `candidates`에 정렬을 걸어도 **노출 순서는 `derive_lifecycle`의 `display.sort`가 최종 결정**(풀 기반 재산출)이라 덮어써진다. 실제 반영 지점은 `derive_lifecycle`.
3. **게이트 도입 前 편입분**: `quality_fund`가 없어 `passed=0`으로 취급 → 정렬 후순위. **탈락이 아니라 순서만 밀리는 것**(soft 원칙 유지). 재스캔 통과 시 부여됨.
4. **DART 재호출 안 함**: 풀 편입 시점 1회 산출 후 유지(재무는 분기 단위 변동).

### 6.3 검증 (2026-07-15)
- **§0 역설 사례 재현 ✅**: `코웨이(021240) = 펀더 5/5 🅰️` — 기존 6조건에선 ⑥ 가격위치 탈락으로 B등급이던 바로 그 종목. ⑥ 제외 설계가 의도대로 동작함을 실증. `기아 5/5 🅰️`(대시보드 기존 A등급 표기와 일치).
- **정렬 1순위 = 펀더멘탈 실증 ✅**: 합성 3종(GS건설 score 95·펀더 2/5 / 기아 88·5/5 / 코웨이 81·5/5) 투입 → **점수 1위 GS건설이 최하단으로 밀리고** 기아·코웨이가 상단. 입력 3종 → 출력 3종(**탈락 0 = soft 확인**).
- **배지 렌더 ✅**: 5/5→`🅰️ 우량 펀더 5/5` · 4/5→`🅱️ 펀더 4/5` · 2/5→미노출(컷 ≥4).
- **회귀 없음 ✅**: `collect_theme_flow.py --dry-run` 결과가 07-15 08:15 정규 회차와 동일(노출 0 / 졸업①2 / 지속관찰 5 / 이탈 5 / 풀 12).
- ⚠️ **한계**: 07-15 기준 확인(pass) 0종이라 **게이트가 실전 파이프라인 경로에서 발화한 적은 아직 없음**(합성 입력으로만 검증). 최초 확인 종목 등장 회차에 로그 `[우량게이트]` 라인으로 실동작 확인 필요.
