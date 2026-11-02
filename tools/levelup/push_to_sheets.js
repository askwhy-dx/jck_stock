// Push the unified workbook to Google Sheets via service account.
//
// Usage:
//   node push_to_sheets.js
//
// Requires:
//   - credentials.json (service account JSON)
//   - sheet-report-update@ai-report-bot-490808.iam.gserviceaccount.com
//     must have Editor access to the spreadsheet.
//
// Reads sheet definitions from build_unified.js by importing it as a module.
// (build_unified.js currently runs side-effects; we extract its data structures.)

const path = require('path');
const fs = require('fs');
const { google } = require('googleapis');

const SPREADSHEET_ID = '1DhDbm9jPTT5d7QNtFCp6jEPBOfdtQk3cs4SiwHr4R6Q';
const CREDS_PATH = path.join(__dirname, '..', '..', 'credentials.json');

// =============================================================
// Re-build the data shape (rows, with formulas as strings starting with "=")
// We mirror the structure from build_unified.js but emit Sheets-native data.
// =============================================================
const TRADE_ROWS = 50;
const SNAP_ROWS = 36;

function fcell(formula) { return `=${formula}`; }

// Sheet 0: 사용방법
const sheet_usage = [
  ['📖 주식 레벨업 통합 시스템 사용 방법', ''],
  ['', ''],
  ['🏗️', '전체 구조 (시트 순서)'],
  ['', '00_사용방법       ─ 본 가이드\n01_거래일지       ─ 매 거래마다 한 행씩 입력\n02_월별스냅샷     ─ 매월 말 잔고/입금 입력\n03_KPI_자동집계   ─ 거래/스냅샷 기반 자동 산출 (수식)\n04_로드맵_L0_L5   ─ 레벨별 미션·졸업·강등 기준 (참고)\n05_대시보드       ─ 모든 레벨 진급/유지 한눈에\n06_L1→L2 ~ 10_L5유지 ─ 레벨별 진급/유지 체크리스트\n     (E열 본인값은 KPI 시트에서 자동 인입)'],
  ['', ''],
  ['🎯', '운영 목표'],
  ['', '레벨별 미션을 달성하면서 시드(주식 자산)를 안전하게 키우는 것이 목적입니다.\n• 손절·기록·원칙 준수가 진급의 1차 조건\n• 수익률은 절대 기준(Floor) + 상대 기준(KOSPI ±α)을 AND 조건으로 평가\n• 시드 5배 확장마다 체급 적응기(3~6개월) 필수'],
  ['', ''],
  ['📝', '01_거래일지 입력 방법'],
  ['', '노란색 열(입력)만 채우면 됩니다:\n  • 매수일 / 매도일 (YYYY-MM-DD)\n  • 종목명, 매수가, 매도가, 수량\n  • 손절가 설정 (매수 시 계획)\n  • 손절가 도달? (Y/N), 손절 준수? (Y/N), 원칙 위반? (Y/N)\n\n회색 열(매수금액·매도금액·손익·수익률)은 자동 계산됩니다.\n최대 50개 거래까지 수식이 미리 깔려 있습니다.'],
  ['', ''],
  ['📸', '02_월별스냅샷 입력 방법'],
  ['', '매월 말 한 번만 입력:\n  • 연월 (YYYY-MM), 기말 잔고(만원), 당월 입금액(만원)\n\n자동 계산:\n  • 누적 원금+입금, 당월 수익률, 누적 최고점, MDD(%)'],
  ['', ''],
  ['📊', '03_KPI_자동집계 활용'],
  ['', '거래일지·월별스냅샷을 근거로 자동 산출됩니다.\n⭐ 표시 항목이 체크리스트(06~10)에서 직접 인입되는 핵심 지표:\n  • 손절 준수율, 원칙 위반율, MDD, 연 수익률, 자립 지수, 손익비\n별도로 다시 입력할 필요 없습니다.'],
  ['', ''],
  ['🔁', '레벨 이동 방식 (게임화)'],
  ['', 'L0 (모의투자, 3개월) → L1 (10~50만, 3개월, 기록 습관)\n  → L2 (50~200만, 6개월, 손절 체화)\n  → L3 (200~1000만, 9개월, 포트폴리오)\n  → L4 (1000~5000만, 12개월, 리스크 설계)\n  → L5 (5000만+, 상시 유지)\n\n각 레벨 시트의 "최종 판정" 행에서 진급/유지/강등을 자동 판정합니다.'],
  ['', ''],
  ['🎯', '🎯 목표 KPI 기준 한눈에 (Level 2→3 기준)'],
  ['', '  • 손절 준수율: ≥ 90%\n  • 원칙 위반율: ≤ 10%\n  • MDD: -15% 이내\n  • 연 수익률 Floor: ≥ -10%\n  • 분기 Floor 경보: ≤ 3회\n  • 누적 수익: 양수\n  • 손익비: ≥ 1.5 (L3 이상 필수)\n  • 자립 지수: 30% 이상 (Level 4부터)'],
  ['', ''],
  ['⚠️', '주의 사항'],
  ['', '• Y/N 항목은 대문자로만 입력 (Y, N)\n• 잔고는 모두 만원 단위 (200만원 → 200)\n• MDD·Floor 등 음수는 - 부호 포함 (-10)\n• 수익률 공식: (기말 - 기초 - 입금) / 기초 × 100\n• 모든 진급 조건은 AND, 강등 트리거는 OR\n• 본 시스템은 자기 검증 프레임워크입니다. 투자 권유 아님'],
];

// Sheet 1: 거래일지
const sheet_trade = [
  ['No.', '매수일', '매도일', '종목명', '매수가', '매도가', '수량', '매수금액', '매도금액', '손익(원)', '수익률(%)', '손절가 설정', '손절가 도달?\n(Y/N)', '손절 준수?\n(Y/N)', '원칙 위반?\n(Y/N)']
];
sheet_trade.push([1, '2026-01-05', '2026-01-18', 'DS단석', 19968, 20655, 18,
  fcell('IF(AND(E2<>"",G2<>""),E2*G2,"")'),
  fcell('IF(AND(F2<>"",G2<>""),F2*G2,"")'),
  fcell('IF(AND(H2<>"",I2<>""),I2-H2,"")'),
  fcell('IF(AND(H2<>"",I2<>"",H2<>0),(I2-H2)/H2*100,"")'),
  17000, 'N', 'Y', 'N']);
for (let i = 2; i <= TRADE_ROWS; i++) {
  const r = i + 1;
  sheet_trade.push([
    i, '', '', '', '', '', '',
    fcell(`IF(AND(E${r}<>"",G${r}<>""),E${r}*G${r},"")`),
    fcell(`IF(AND(F${r}<>"",G${r}<>""),F${r}*G${r},"")`),
    fcell(`IF(AND(H${r}<>"",I${r}<>""),I${r}-H${r},"")`),
    fcell(`IF(AND(H${r}<>"",I${r}<>"",H${r}<>0),(I${r}-H${r})/H${r}*100,"")`),
    '', '', '', ''
  ]);
}

// Sheet 2: 월별스냅샷
const SAMPLE_SNAP = [
  ['2025-12', 200, 200],
  ['2026-01', 200, 0],
  ['2026-02', 200, 0],
  ['2026-03', 200, 0],
  ['2026-04', 210, 0],
];
const sheet_snap = [
  ['연월', '기말 잔고\n(만원)', '당월 입금액\n(만원)', '누적 원금+입금\n(만원)', '당월 수익률\n(%)', '누적 최고점\n(만원)', 'MDD\n(고점 대비 %)']
];
for (let i = 1; i <= SNAP_ROWS; i++) {
  const r = i + 1;
  const ym  = (i - 1 < SAMPLE_SNAP.length) ? SAMPLE_SNAP[i - 1][0] : '';
  const bal = (i - 1 < SAMPLE_SNAP.length) ? SAMPLE_SNAP[i - 1][1] : '';
  const dep = (i - 1 < SAMPLE_SNAP.length) ? SAMPLE_SNAP[i - 1][2] : '';
  sheet_snap.push([
    ym, bal, dep,
    (i === 1) ? fcell(`IF(B${r}<>"",B${r},"")`) : fcell(`IF(AND(B${r}<>"",B${r-1}<>""),D${r-1}+C${r},"")`),
    (i === 1) ? '' : fcell(`IF(AND(B${r}<>"",B${r-1}<>"",B${r-1}<>0),(B${r}-B${r-1}-C${r})/B${r-1}*100,"")`),
    (i === 1) ? fcell(`IF(B${r}<>"",B${r},"")`) : fcell(`IF(B${r}<>"",MAX(F${r-1},B${r}),"")`),
    fcell(`IF(AND(B${r}<>"",F${r}<>"",F${r}<>0),(B${r}-F${r})/F${r}*100,"")`),
  ]);
}

// Sheet 3: KPI
const J = "'01_거래일지'!";
const S = "'02_월별스냅샷'!";
const trCount = `COUNTA(${J}D2:D${TRADE_ROWS+1})`;
const winCount = `COUNTIFS(${J}J2:J${TRADE_ROWS+1},">0")`;
const loseCount = `COUNTIFS(${J}J2:J${TRADE_ROWS+1},"<0")`;
const avgRet = `IFERROR(AVERAGE(${J}K2:K${TRADE_ROWS+1}),0)`;
const avgWin = `IFERROR(AVERAGEIF(${J}K2:K${TRADE_ROWS+1},">0"),0)`;
const avgLoss = `IFERROR(AVERAGEIF(${J}K2:K${TRADE_ROWS+1},"<0"),0)`;
const lastIdx = `COUNTA(${S}A2:A${SNAP_ROWS+1})`;
const lastBal = `IFERROR(LOOKUP(2,1/(${S}B2:B${SNAP_ROWS+1}<>""),${S}B2:B${SNAP_ROWS+1}),0)`;
const totalDep = `SUM(${S}C2:C${SNAP_ROWS+1})`;
const depMonths = `COUNTIF(${S}C2:C${SNAP_ROWS+1},">0")`;
const monthsCount = `COUNTA(${S}A2:A${SNAP_ROWS+1})`;
const minMDD = `IFERROR(MIN(${S}G2:G${SNAP_ROWS+1}),0)`;
const floorAlerts = `COUNTIFS(${S}G2:G${SNAP_ROWS+1},"<=-7")`;
const bal12ago = `IFERROR(INDEX(${S}B2:B${SNAP_ROWS+1},MAX(1,${lastIdx}-11)),0)`;
const dep12sum = `IFERROR(SUM(OFFSET(${S}C2,MAX(0,${lastIdx}-11),0,MIN(12,${lastIdx}),1)),0)`;
const yearRet = `IFERROR(IF(${bal12ago}=0,0,(${lastBal}-${bal12ago}-${dep12sum})/${bal12ago}*100),0)`;

const sheet_kpi = [
  ['구분', 'KPI 항목', '자동 계산 값', '단위', '산출 공식 설명', '체크리스트 연결'],
  ['거래 통계', '총 거래 횟수', fcell(trCount), '건', '거래일지 종목명 열의 비어있지 않은 셀 개수', '실거래 횟수'],
  ['', '승률', fcell(`IFERROR((${winCount})/MAX(1,(${winCount})+(${loseCount}))*100,0)`), '%', '손익>0 거래 / 손익 기록된 거래 × 100', '참고 지표'],
  ['', '평균 수익률', fcell(avgRet), '%', '거래일지 수익률(%) 열의 평균', '참고 지표'],
  ['', '평균 수익 (익절 거래)', fcell(avgWin), '%', '수익률>0인 거래의 평균 수익률', '손익비 계산 기초'],
  ['', '평균 손실 (손절 거래)', fcell(avgLoss), '%', '수익률<0인 거래의 평균 손실률', '손익비 계산 기초'],
  ['', '손익비 (Profit Factor)', fcell(`IFERROR(C5/ABS(C6),0)`), '배', '평균 수익률 ÷ 평균 손실률 절댓값 (1.5 이상 목표)', '손익비 평균'],
  ['원칙 준수', '손절가 도달 거래 수', fcell(`COUNTIF(${J}M2:M${TRADE_ROWS+1},"Y")`), '건', '손절가에 실제 도달한 거래 건수', '─'],
  ['', '손절 실제 실행 수', fcell(`COUNTIFS(${J}M2:M${TRADE_ROWS+1},"Y",${J}N2:N${TRADE_ROWS+1},"Y")`), '건', '손절가 도달 AND 실제 매도 실행한 건수', '─'],
  ['', '손절 준수율', fcell(`IFERROR(C9/MAX(1,C8)*100,0)`), '%', '손절 실행 / 손절가 도달 × 100', '손절 준수율 ⭐'],
  ['', '원칙 위반 거래 수', fcell(`COUNTIF(${J}O2:O${TRADE_ROWS+1},"Y")`), '건', '원칙 위반(Y) 체크된 거래 건수', '─'],
  ['', '원칙 위반율', fcell(`IFERROR(C11/MAX(1,${trCount})*100,0)`), '%', '원칙 위반 / 전체 거래 × 100', '원칙 위반율 ⭐'],
  ['리스크', '현재 MDD (최악)', fcell(minMDD), '%', '월별 스냅샷 MDD 열의 최소값 (가장 큰 낙폭)', 'MDD ⭐'],
  ['', '최근 연 수익률 (12개월)', fcell(yearRet), '%', '(최근잔고 - 12개월전잔고 - 12개월간 입금총액) / 12개월전 잔고', '연 Floor ⭐'],
  ['', '분기 Floor 경보 횟수\n(분기 -7% 초과)', fcell(floorAlerts), '회', '월별 MDD 중 -7% 이하인 월 수 (분기 환산 시 참고)', '분기 Floor 경보'],
  ['시드 구성', '현재 시드', fcell(lastBal), '만원', '월별 스냅샷의 가장 마지막 기말 잔고', '현재 레벨 판정'],
  ['', '누적 입금액', fcell(totalDep), '만원', '월별 스냅샷 입금액 합계', '─'],
  ['', '누적 수익', fcell(`C16-C17`), '만원', '현재 시드 - 누적 입금액 = 순수 수익분', '─'],
  ['', '자립 지수', fcell(`IFERROR(IF(C18>0,C18/C16*100,0),0)`), '%', '누적 수익 ÷ 현재 시드 × 100 (음수는 0으로 처리)', '자립 지수 ⭐'],
  ['', '월 평균 입금액', fcell(`IFERROR(C17/MAX(1,${depMonths}),0)`), '만원', '누적 입금 ÷ 입금 발생 월수', '월 입금 상한 체크'],
  ['추가 지표', '운영 기간 (월)', fcell(monthsCount), '개월', '월별 스냅샷에 입력된 월 수', '운영 기간 ⭐'],
  ['', '매매 일지 작성률', fcell(`IF(${trCount}=0,0,100)`), '%', '거래일지에 한 행이라도 있으면 100% (별도 누락 체크 시 수동 조정)', '매매 일지 작성률 ⭐'],
  ['', '분할매수 완결성', 100, '%', '진입한 종목을 100% 종결했는지 (수동 입력)', '분할매수 완결성'],
  ['', '체급 적응기 통과', 'Y', '', '레벨 전환 후 적응기 기간 통과 여부 (수동: Y/N)', '체급 적응기'],
];

// Sheet 4: 로드맵
const sheet_roadmap = [
  ['📊 시드 기반 투자 로드맵 (Level 0 ~ Level 5) — 완성본'],
  ['레벨','명칭','시드 규모','최소 기간','체급 적응기','동시 보유','종목당 투입 상한','현금 최소 보유','MDD 한도','연 수익률 Floor (절대)','연 수익률 상대 기준','월 입금 상한','자립 지수 목표','핵심 테마','주요 미션','졸업 / 유지 조건','강등 트리거','비고'],
  ['Level 0','⚪ 모의투자','가상 계좌\n(초기 1,000만)','3개월','해당 없음','가상','가상','—','—','—','—','—','—','실전 전 학습\n(안 다치고 배우기)',
    '1. 증권사 모의투자 앱 또는 엑셀 가상계좌 개설\n2. 3줄 매수 원칙 + 매매 일지 양식 구축\n3. 20회 이상 가상 거래 완결\n4. 주 1회 시장 리뷰 작성',
    '• 기간 3개월 이상\n• 가상 거래 20회 이상\n• 원칙 위반율 30% 이하\n• 손절 실행 경험 최소 3회','해당 없음','실전 진입 전 필수 관문'],
  ['Level 1','🟢 튜토리얼','10 ~ 50만 원','3개월','없음','1종목','시드 100%','0~10%','제한 없음','불문','불문','월 5~10만 원','불문 (측정 시작)','기록·원칙의 습관화',
    '1. 생활비 분리 투자 전용 계좌 + 10만 입금\n2. 사업 내용 한 문장 설명 가능한 종목만\n3. 3줄 매수 원칙 사전 기록\n4. 매매 일지 100% 작성 (6필드)\n5. 공시·재무제표·뉴스만 참고 (추천 금지)\n6. 주 1회 시장 리뷰 한 문단 요약\n7. 입금 시 \'원칙 유지 자가 점검\' 기록',
    '• 기간 3개월 이상\n• 매매 일지 100% 작성\n• 원칙 위반율 20% 이하\n• 실거래 10회 이상\n• 수익률 불문',
    '• 원칙 위반율 30% 초과 지속\n• 매매 일지 미작성 20% 초과','수익률 평가 없음, 기록 완결성이 통과 기준'],
  ['Level 2','🔵 초보 던전','50 ~ 200만 원','6개월','3개월','최대 2종목','시드 50~60%','10~20%','-15% 이내','-10% 이상','누적 수익 양수','월 10~30만 원','측정·기록','원칙을 실제로 지키는가\n(손절 체화)',
    '1. 2~3분할 매수, 첫 진입 30% 이하\n2. 손절 기계화 (예외 불문)\n3. 한 종목 시드의 50% 이하\n4. 월 1회 복기 리포트\n5. 외부 추천 진입 0건\n6. 손익비 1.5 이상 사전 검증\n7. 입금 후 1주일 신규 진입 금지\n8. 원금/수익/입금 3분리 회계 시작',
    '[AND] 기간 6개월+, 체급 적응기 통과, 손절 준수율 90%+, 누적 수익 양수, MDD -15% 이내, Floor -10%+, 원칙 위반율 10% 이하, 분할매수 완결성 100%',
    '손절 준수율 80% 미만, MDD -15% 초과, Floor 하회 → L1 복귀','첫 실전 체급'],
  ['Level 3','🟣 중급 필드','200 ~ 1,000만 원','9개월','4개월','3~5종목','시드 25~30%','15~25%','-12% 이내','-8% 이상','KOSPI ±3%p 이내/초과','월 30~100만 원','측정·공개','포트폴리오 운영 + 세후 관점',
    '1. 3~5종목, 섹터 3개 이상\n2. 한 종목 최대 25%, 한 섹터 최대 40%\n3. 분기별 리밸런싱 (±5%p 초과 시)\n4. 분할 익절 3단계 체계화\n5. 기대값(EV) 계산 후 진입\n6. 실측 평단 엑셀 관리\n7. 매매 비용(수수료+세금) 매 거래 기록\n8. 연간 입금 < 연간 수익금 조건\n9. 원금/수익/입금/세금 4분리 회계',
    '[AND] 기간 9개월+, 체급 적응기 통과, 손절 95%+, 손익비 1.5+ (1.0 미달 0건), MDD -12% 이내, Floor -8%+, KOSPI ±3%p, 리밸런싱 4회+, 분할 익절 70%+, 수익>입금',
    '손절 85% 미만, MDD -15% 이상, Floor 하회, 수익<입금 2년 연속 → L2 복귀','단일 종목 → 포트폴리오 전환점'],
  ['Level 4','🔴 고급 레이드','1,000 ~ 5,000만 원','12개월','6개월','3~4종목','시드 20~25%','20~30% (상시)','-10% 이내','-5% 이상 (세후)','KOSPI + α (예: +3%p)','월 자율 (최대 200만)','30% 이상 필수','손실을 설계 + 수급 독립',
    '1. 1거래 최대 손실 시드의 2% 이내\n2. 헤지 수단 1개 이상 실전 적용\n3. -10%/-20%/-30% 하락장 시나리오 문서화\n4. 스윙 + 장기 분리, 장기 30% 이상 배분\n5. 세후 수익 관리 (양도세·배당세 반영)\n6. 분기별 기법별·종목별 백테스트\n7. 현금 20% 이상 상시 확보\n8. 연 2회 \'수익 자립 감사\'',
    '[AND] 기간 12개월+, 체급 적응기 통과, 손절 98%+, 손익비 2.0+, MDD -10% 이내, Floor -5% 세후, 하락장 -10% 경험+대응, 변동성 KOSPI 이하, 자립 지수 30%+, 월 입금 0원 시뮬 통과',
    '손절 90% 미만, MDD -12% 이상, Floor 하회 (세후), 자립 30% 미달 → L3 복귀','월급 1~2개월치 단일 손실 감수 체급'],
  ['Level 5','🟡 만렙 레이드','5,000만 원 이상','상시 (졸업 없음)','6개월','5~7종목','시드 15~20%','20% 상시','-10% 이내 (연)','-5% 이상 (세후)','KOSPI + α','자율 (IPS 명시)','60% 이상','자산배분 + IPS + 지속성',
    '1. IPS (투자정책서) 작성 + 연 1회 갱신\n2. 자산 배분: 주식/채권/현금/대체자산\n3. 생활비 6개월치 + 비상자금 분리\n4. ISA/연금저축/IRP 절세계좌 전략적 활용\n5. 5~7종목, 섹터 4개 이상, 한 종목 20% 상한\n6. 연간 복기 보고서 (본인용 IR 수준)\n7. 연 3권 이상 투자서 + 전략 설명 가능\n8. MDD -15% 초과 시 L4 자발 강등\n9. 목표 인출 시나리오 작성 (출구 전략)',
    '[상시] 연 KOSPI+α, Floor -5%+ (세후), MDD -10% 이내, IPS 95%+, 손절 100%, 연간 복기 보고서, 현금 20% 상시, 외부 입금 의존 30% 이하, 자립 60%+',
    'MDD -15% 초과, 손절 예외 발생, IPS 85% 미만, 자립 60% 미달 지속 → L4 자발 강등','졸업 없는 \'지속\'의 체급'],
  ['🔁 전 레벨 공통 불변 룰 (R1 ~ R10)'],
  ['R1 손절 기계화 / R2 매매 일지 100% / R3 손익비 1.5+ / R4 추천 종목 금지 / R5 생활비 분리 / R6 총 손실 -30% 한도 / R7 L0 모의 3개월 / R8 3분리 회계 / R9 체급 적응기 / R10 Floor 절대+상대 AND'],
];

// Checklist builder (formulas as = strings)
function buildChecklist(opts) {
  const { title, seedRange, theme, items, passLabel } = opts;
  const rows = [];
  rows.push([title]);
  rows.push([`💰 시드 구간: ${seedRange}`]);
  rows.push([`🎯 핵심 테마: ${theme}`]);
  rows.push(['No.', '구분', '항목', '기준', '본인 값', '단위', '판정', '결과', '비고']);
  items.forEach((it) => {
    const r = rows.length + 1;
    const [no, sec, item, criteria, val, unit, judge, note] = it;
    const isPromo = sec === '진급 조건' || sec === '유지 조건';
    let gFormula;
    if (typeof judge === 'string' && judge.startsWith('>=')) {
      gFormula = `IF(E${r}>=${judge.slice(2)},1,0)`;
    } else if (typeof judge === 'string' && judge.startsWith('<=')) {
      gFormula = `IF(E${r}<=${judge.slice(2)},1,0)`;
    } else if (judge === 'Y' || judge === 'N') {
      gFormula = `IF(UPPER(TRIM(E${r}&""))="Y",1,0)`;
    } else if (judge === '>0') {
      gFormula = `IF(E${r}>0,1,0)`;
    } else {
      gFormula = `IF(E${r}=1,1,0)`;
    }
    const hFormula = isPromo
      ? `IF(G${r}=1,"✅ 충족","❌ 미충족")`
      : `IF(G${r}=1,"⚠️ 발동","⚪ 해당 없음")`;
    rows.push([no, sec, item, criteria, val, unit, fcell(gFormula), fcell(hFormula), note]);
  });
  const promoRows = items.filter(it => it[1] === '진급 조건' || it[1] === '유지 조건').length;
  const demoteRows = items.filter(it => it[1] === '강등 트리거').length;
  const headerRowOffset = 5;
  const pStart = headerRowOffset, pEnd = pStart + promoRows - 1;
  const dStart = pEnd + 1, dEnd = dStart + demoteRows - 1;
  const promoLabel = (items[0][1] === '유지 조건') ? '✅ 유지 조건 통과 수' : '✅ 진급 조건 통과 수';
  rows.push(['', promoLabel, '', '', fcell(`SUM(G${pStart}:G${pEnd})`), '', `/ ${promoRows}`, '', '']);
  rows.push(['', '🚨 강등 트리거 발동 수', '', '', fcell(`SUM(G${dStart}:G${dEnd})`), '', `/ ${demoteRows} 개 중`, '', '']);
  const sumRow = rows.length - 1, trgRow = rows.length;
  rows.push(['', '🎯 최종 판정', '',
    fcell(`IF(AND(E${sumRow}>=${promoRows},E${trgRow}=0),"${passLabel}", IF(E${trgRow}>=1,"🔻 강등/유지","🟡 유지 (재수)"))`),
    '', '', '', '', '']);
  return rows;
}

const K = "'03_KPI_자동집계'!";

const sheet_l1l2 = buildChecklist({
  title: '🟢 Level 1 → Level 2 진급 체크리스트',
  seedRange: '10~50만 원  →  50~200만 원',
  theme: '기록·원칙의 습관화  →  원칙 실행·손절 체화',
  items: [
    [1, '진급 조건', '운영 기간', '≥ 3개월', fcell(`${K}C21`), '개월', '>=3', '최소 운영 기간 충족'],
    [2, '진급 조건', '매매 일지 작성률', '100%', fcell(`${K}C22`), '%', '>=100', '3개월간 모든 거래 일지 작성'],
    [3, '진급 조건', '원칙 위반율', '≤ 20%', fcell(`${K}C12`), '%', '<=20', '감정/뇌동 매매 비율'],
    [4, '진급 조건', '실거래 횟수', '≥ 10회', fcell(`${K}C2`), '회', '>=10', '경험치 확보'],
    [5, '진급 조건', '3줄 매수 원칙 사전 기록률', '100%', 100, '%', '>=100', '매수 전 이유/익절/손절 기록 (수동)'],
    [6, '강등 트리거', '원칙 위반율 30% 초과 지속', '< 30%', fcell(`${K}C12`), '%', '>=30', '지속 3개월 이상'],
    [7, '강등 트리거', '매매 일지 미작성률', '< 20%', fcell(`100-${K}C22`), '%', '>=20', '일지 없는 거래 비율'],
    [8, '강등 트리거', '총 손실 한도 초과 (시드 -30%)', '> -30%', fcell(`${K}C13`), '%', '<=-30', '🚨 2단계 강등 트리거'],
  ],
  passLabel: '🎉 L2 진급 가능',
});

const sheet_l2l3 = buildChecklist({
  title: '🔵 Level 2 → Level 3 진급 체크리스트',
  seedRange: '50~200만 원  →  200~1,000만 원',
  theme: '원칙 실행·손절 체화  →  포트폴리오 운영·세후 관점',
  items: [
    [1, '진급 조건', '운영 기간', '≥ 6개월', fcell(`${K}C21`), '개월', '>=6', '사이클 최소 1회 경험'],
    [2, '진급 조건', '체급 적응기 통과 (3개월)', 'Y', fcell(`${K}C24`), '', 'Y', 'L1 → L2 전환 적응기'],
    [3, '진급 조건', '손절 준수율', '≥ 90%', fcell(`${K}C10`), '%', '>=90', '손절가 도달 시 실제 매도 비율'],
    [4, '진급 조건', '누적 수익', '양수', fcell(`${K}C18`), '만원', '>0', '12개월 누적 수익'],
    [5, '진급 조건', 'MDD (최대 낙폭)', '≥ -15%', fcell(`${K}C13`), '%', '>=-15', '고점 대비 최대 낙폭 (음수)'],
    [6, '진급 조건', '연 Floor (절대)', '≥ -10%', fcell(`${K}C14`), '%', '>=-10', '연간 수익률 (음수 입력)'],
    [7, '진급 조건', '분기 Floor 경보 횟수 (12분기 기준)', '≤ 3회', fcell(`${K}C15`), '회', '<=3', '분기 -7% 초과 발동 누적'],
    [8, '진급 조건', '원칙 위반율', '≤ 10%', fcell(`${K}C12`), '%', '<=10', 'Level 1보다 타이트'],
    [9, '진급 조건', '분할매수 완결성', '100%', fcell(`${K}C23`), '%', '>=100', '진입 종목 100% 종결'],
    [10, '강등 트리거', '손절 준수율 위반', '< 80%', fcell(`${K}C10`), '%', '<=80', '기준보다 -10%p 이상 낮음'],
    [11, '강등 트리거', 'MDD 한도 초과', '< -15%', fcell(`${K}C13`), '%', '<=-15', ''],
    [12, '강등 트리거', '연 Floor 하회', '< -10%', fcell(`${K}C14`), '%', '<=-10', ''],
    [13, '강등 트리거', '총 손실 -30% 초과', '> -30%', fcell(`${K}C13`), '%', '<=-30', '🚨 2단계 강등'],
  ],
  passLabel: '🎉 L3 진급 가능',
});

const sheet_l3l4 = buildChecklist({
  title: '🟣 Level 3 → Level 4 진급 체크리스트',
  seedRange: '200~1,000만 원  →  1,000~5,000만 원',
  theme: '포트폴리오 운영·세후  →  리스크 설계·수급 독립',
  items: [
    [1, '진급 조건', '운영 기간', '≥ 9개월', fcell(`${K}C21`), '개월', '>=9', ''],
    [2, '진급 조건', '체급 적응기 통과 (4개월)', 'Y', fcell(`${K}C24`), '', 'Y', 'L2 → L3 전환 적응기'],
    [3, '진급 조건', '손절 준수율', '≥ 95%', fcell(`${K}C10`), '%', '>=95', ''],
    [4, '진급 조건', '손익비 평균', '≥ 1.5', fcell(`${K}C7`), '배', '>=1.5', '전체 거래 평균 손익비'],
    [5, '진급 조건', '손익비 1.0 미만 진입 건수', '0건', 0, '건', '<=0', '사전 계산 누락 금지 (수동)'],
    [6, '진급 조건', 'MDD', '≥ -12%', fcell(`${K}C13`), '%', '>=-12', ''],
    [7, '진급 조건', '연 Floor', '≥ -8%', fcell(`${K}C14`), '%', '>=-8', ''],
    [8, '진급 조건', '분기 Floor 경보 횟수', '≤ 3회', fcell(`${K}C15`), '회', '<=3', '분기 -6% 초과 발동'],
    [9, '진급 조건', '연 수익률 vs KOSPI 차이 (수동)', '±3%p 이내 또는 초과', 2, '%p', '>=-3', 'KOSPI 대비 (본인 - KOSPI)'],
    [10, '진급 조건', '리밸런싱 실행 횟수 (수동)', '≥ 4회/년', 4, '회', '>=4', '분기별 조정'],
    [11, '진급 조건', '분할 익절 비율 (수동)', '≥ 70%', 75, '%', '>=70', '수익 거래 중 분할 익절 비율'],
    [12, '진급 조건', '수익 자생률 (수익 > 입금)', 'Y', fcell(`IF(${K}C18>${K}C17,"Y","N")`), '', 'Y', '연 수익금 > 연 입금액'],
    [13, '강등 트리거', '손절 준수율 위반', '< 85%', fcell(`${K}C10`), '%', '<=85', ''],
    [14, '강등 트리거', 'MDD 한도 초과', '< -15%', fcell(`${K}C13`), '%', '<=-15', ''],
    [15, '강등 트리거', '연 Floor 하회', '< -8%', fcell(`${K}C14`), '%', '<=-8', ''],
    [16, '강등 트리거', '수익 < 입금 2년 연속 (수동)', 'Y / N', 'N', '', 'Y', '자립 실패 신호'],
    [17, '강등 트리거', '총 손실 -30% 초과', '> -30%', fcell(`${K}C13`), '%', '<=-30', '🚨 2단계 강등'],
  ],
  passLabel: '🎉 L4 진급 가능',
});

const sheet_l4l5 = buildChecklist({
  title: '🔴 Level 4 → Level 5 진급 체크리스트',
  seedRange: '1,000~5,000만 원  →  5,000만 원+',
  theme: '리스크 설계·수급 독립  →  자산 배분·IPS·지속성',
  items: [
    [1, '진급 조건', '운영 기간', '≥ 12개월', fcell(`${K}C21`), '개월', '>=12', ''],
    [2, '진급 조건', '체급 적응기 통과 (6개월)', 'Y', fcell(`${K}C24`), '', 'Y', 'L3 → L4 전환 적응기'],
    [3, '진급 조건', '손절 준수율', '≥ 98%', fcell(`${K}C10`), '%', '>=98', ''],
    [4, '진급 조건', '손익비 평균', '≥ 2.0', fcell(`${K}C7`), '배', '>=2.0', ''],
    [5, '진급 조건', 'MDD', '≥ -10%', fcell(`${K}C13`), '%', '>=-10', ''],
    [6, '진급 조건', '연 Floor (세후, 수동)', '≥ -5%', -2, '%', '>=-5', '양도세·배당세 반영'],
    [7, '진급 조건', '분기 Floor 경보 횟수', '≤ 2회', fcell(`${K}C15`), '회', '<=2', '분기 -4% 초과 발동'],
    [8, '진급 조건', '연 수익률 vs KOSPI (+α, 수동)', '≥ +3%p', 4, '%p', '>=3', 'KOSPI 초과 α 달성'],
    [9, '진급 조건', '하락장(-10%) 경험 + 대응 (수동)', 'Y', 'Y', '', 'Y', '시나리오대로 대응 성공'],
    [10, '진급 조건', '연간 변동성 vs KOSPI (수동)', '≤ KOSPI 수준', 15, '%', '<=20', 'KOSPI 표준편차 약 18~20%'],
    [11, '진급 조건', '자립 지수', '≥ 30%', fcell(`${K}C19`), '%', '>=30', '수익 ÷ (수익+입금)'],
    [12, '진급 조건', '월 입금 0원 시뮬레이션 통과 (수동)', 'Y', 'Y', '', 'Y', '입금 없이 시드 유지 가능'],
    [13, '강등 트리거', '손절 준수율 위반', '< 90%', fcell(`${K}C10`), '%', '<=90', ''],
    [14, '강등 트리거', 'MDD 한도 초과', '< -12%', fcell(`${K}C13`), '%', '<=-12', ''],
    [15, '강등 트리거', '연 Floor (세후) 하회 (수동)', '< -5%', -2, '%', '<=-5', ''],
    [16, '강등 트리거', '자립 지수 미달', '< 30%', fcell(`${K}C19`), '%', '<=30', '입금 과잉 의존 판정'],
    [17, '강등 트리거', '총 손실 -30% 초과', '> -30%', fcell(`${K}C13`), '%', '<=-30', '🚨 2단계 강등'],
  ],
  passLabel: '🎉 L5 진급 가능',
});

const sheet_l5keep = buildChecklist({
  title: '🟡 Level 5 상시 유지 체크리스트',
  seedRange: '5,000만 원+ (졸업 없음)',
  theme: '자산 배분 · IPS · 지속성',
  items: [
    [1, '유지 조건', '연 수익률 vs KOSPI (+α, 수동)', '≥ +3%p', 5, '%p', '>=3', ''],
    [2, '유지 조건', '연 Floor (세후, 수동)', '≥ -5%', -2, '%', '>=-5', ''],
    [3, '유지 조건', 'MDD (연간)', '≥ -10%', fcell(`${K}C13`), '%', '>=-10', ''],
    [4, '유지 조건', '분기 Floor 경보 횟수', '≤ 2회/년', fcell(`${K}C15`), '회', '<=2', ''],
    [5, '유지 조건', 'IPS 준수율 (수동)', '≥ 95%', 97, '%', '>=95', '투자정책서 명시 규칙 준수'],
    [6, '유지 조건', '손절 준수율', '100%', fcell(`${K}C10`), '%', '>=100', '예외 없음'],
    [7, '유지 조건', '연간 복기 보고서 작성 (수동)', 'Y', 'Y', '', 'Y', '연 1회 이상'],
    [8, '유지 조건', '현금 비중 상시 (수동)', '≥ 20%', 22, '%', '>=20', ''],
    [9, '유지 조건', '외부 입금 의존도', '≤ 30%', fcell(`IFERROR(${K}C17/MAX(1,${K}C16)*100,0)`), '%', '<=30', '누적 입금 ÷ 시드'],
    [10, '유지 조건', '자립 지수', '≥ 60%', fcell(`${K}C19`), '%', '>=60', ''],
    [11, '강등 트리거', 'MDD 한도 초과', '< -15%', fcell(`${K}C13`), '%', '<=-15', '자발 강등 기준'],
    [12, '강등 트리거', '손절 예외 발생 (수동)', '1회라도', 0, '회', '>=1', '손절 예외 = 즉시 강등'],
    [13, '강등 트리거', 'IPS 준수율 위반 (수동)', '< 85%', 97, '%', '<=85', ''],
    [14, '강등 트리거', '자립 지수 60% 미달 지속 (수동)', 'Y / N', 'N', '', 'Y', '구조적 의존 판정'],
    [15, '강등 트리거', '총 손실 -30% 초과', '> -30%', fcell(`${K}C13`), '%', '<=-30', '🚨 2단계 강등'],
  ],
  passLabel: '🎉 L5 유지 우수 (모든 조건 충족)',
});

// Dashboard
function summaryRefs(sheetName, rows) {
  const lr = rows.length;
  return {
    pass:    `'${sheetName}'!E${lr-2}`,
    promoLb: `'${sheetName}'!G${lr-2}`,
    trigger: `'${sheetName}'!E${lr-1}`,
    trigLb:  `'${sheetName}'!G${lr-1}`,
    judge:   `'${sheetName}'!D${lr}`,
  };
}
const checklistMeta = [
  ['L1→L2', '06_L1→L2', sheet_l1l2],
  ['L2→L3', '07_L2→L3', sheet_l2l3],
  ['L3→L4', '08_L3→L4', sheet_l3l4],
  ['L4→L5', '09_L4→L5', sheet_l4l5],
  ['L5 유지', '10_L5_유지', sheet_l5keep],
];
const sheet_dash = [
  ['📊 투자 로드맵 진급/강등 판정 대시보드'],
  ['각 레벨 시트의 노란색 \'본인 값\' 열은 03_KPI_자동집계에서 자동 인입됩니다. 거래일지/월별스냅샷만 채우면 자동 갱신.'],
  ['레벨 전환', '진급/유지 조건 통과', '강등 트리거 발동', '최종 판정', '시트 바로가기'],
];
checklistMeta.forEach(([label, name, rows]) => {
  const refs = summaryRefs(name, rows);
  sheet_dash.push([
    label,
    fcell(`${refs.pass}&" "&${refs.promoLb}`),
    fcell(`${refs.trigger}&" "&${refs.trigLb}`),
    fcell(`${refs.judge}`),
    `📂 ${name}`,
  ]);
});
sheet_dash.push(['']);
sheet_dash.push(['📖 사용 방법']);
sheet_dash.push(['1. 01_거래일지에 매 거래마다 한 행씩 입력합니다.\n2. 02_월별스냅샷에 매월 말 잔고/입금을 입력합니다.\n3. 03_KPI_자동집계가 자동으로 핵심 지표를 산출합니다.\n4. 06~10 레벨 체크리스트의 노란색 본인값 열은 KPI 시트에서 자동으로 인입됩니다.\n5. 본 대시보드에서 모든 레벨의 진급/유지 현황을 한눈에 확인하세요.']);

const ALL_SHEETS = [
  ['00_사용방법', sheet_usage],
  ['01_거래일지', sheet_trade],
  ['02_월별스냅샷', sheet_snap],
  ['03_KPI_자동집계', sheet_kpi],
  ['04_로드맵_L0_L5', sheet_roadmap],
  ['05_대시보드', sheet_dash],
  ['06_L1→L2', sheet_l1l2],
  ['07_L2→L3', sheet_l2l3],
  ['08_L3→L4', sheet_l3l4],
  ['09_L4→L5', sheet_l4l5],
  ['10_L5_유지', sheet_l5keep],
];

// =============================================================
// Push to Google Sheets
// =============================================================
async function main() {
  const auth = new google.auth.GoogleAuth({
    keyFile: CREDS_PATH,
    scopes: ['https://www.googleapis.com/auth/spreadsheets'],
  });
  const sheets = google.sheets({ version: 'v4', auth });

  // Read current spreadsheet to know existing sheets
  const meta = await sheets.spreadsheets.get({ spreadsheetId: SPREADSHEET_ID });
  const existingSheets = meta.data.sheets.map(s => ({ title: s.properties.title, id: s.properties.sheetId }));
  console.log('Existing sheets:', existingSheets.map(s => s.title));

  // Plan:
  //   1) Add a temporary sheet "_TMP_PLACEHOLDER_" so we can safely delete others.
  //   2) Delete all existing sheets EXCEPT the placeholder.
  //   3) Add our 11 sheets in order.
  //   4) Delete the placeholder.

  const TMP = '_TMP_' + Date.now();

  // Step 1: add temp
  await sheets.spreadsheets.batchUpdate({
    spreadsheetId: SPREADSHEET_ID,
    requestBody: { requests: [{ addSheet: { properties: { title: TMP } } }] },
  });
  console.log('Temp sheet added:', TMP);

  // Refresh meta
  let m2 = (await sheets.spreadsheets.get({ spreadsheetId: SPREADSHEET_ID })).data.sheets;
  const tmpId = m2.find(s => s.properties.title === TMP).properties.sheetId;

  // Step 2: delete originals
  const deleteReqs = m2
    .filter(s => s.properties.title !== TMP)
    .map(s => ({ deleteSheet: { sheetId: s.properties.sheetId } }));
  if (deleteReqs.length) {
    await sheets.spreadsheets.batchUpdate({
      spreadsheetId: SPREADSHEET_ID,
      requestBody: { requests: deleteReqs },
    });
    console.log(`Deleted ${deleteReqs.length} original sheets`);
  }

  // Step 3: add our 11 sheets
  const addReqs = ALL_SHEETS.map(([name, _], idx) => ({
    addSheet: { properties: { title: name, index: idx } },
  }));
  await sheets.spreadsheets.batchUpdate({
    spreadsheetId: SPREADSHEET_ID,
    requestBody: { requests: addReqs },
  });
  console.log('Added 11 sheets');

  // Step 4: write values to each sheet
  const data = ALL_SHEETS.map(([name, rows]) => ({
    range: `'${name}'!A1`,
    values: rows,
  }));
  await sheets.spreadsheets.values.batchUpdate({
    spreadsheetId: SPREADSHEET_ID,
    requestBody: {
      valueInputOption: 'USER_ENTERED', // formulas evaluate
      data,
    },
  });
  console.log('Wrote values to all sheets');

  // Step 5: formatting & frozen rows + delete placeholder
  m2 = (await sheets.spreadsheets.get({ spreadsheetId: SPREADSHEET_ID })).data.sheets;
  const sheetIdMap = Object.fromEntries(m2.map(s => [s.properties.title, s.properties.sheetId]));

  const fmtReqs = [];
  // Freeze first row on each data sheet, set bold header
  ALL_SHEETS.forEach(([name, rows]) => {
    const sid = sheetIdMap[name];
    if (sid === undefined) return;
    // freeze first row for sheets where header is row 1 (most)
    fmtReqs.push({
      updateSheetProperties: {
        properties: { sheetId: sid, gridProperties: { frozenRowCount: name.match(/^(00|04|05)/) ? 0 : 1 } },
        fields: 'gridProperties.frozenRowCount',
      },
    });
    // Bold first row, light gray bg
    if (!name.match(/^(00|04)/)) {
      fmtReqs.push({
        repeatCell: {
          range: { sheetId: sid, startRowIndex: 0, endRowIndex: 1 },
          cell: { userEnteredFormat: {
            backgroundColor: { red: 0.85, green: 0.92, blue: 0.85 },
            textFormat: { bold: true },
            horizontalAlignment: 'CENTER',
            verticalAlignment: 'MIDDLE',
            wrapStrategy: 'WRAP',
          }},
          fields: 'userEnteredFormat(backgroundColor,textFormat,horizontalAlignment,verticalAlignment,wrapStrategy)',
        },
      });
    }
    // Auto resize columns
    fmtReqs.push({
      autoResizeDimensions: {
        dimensions: { sheetId: sid, dimension: 'COLUMNS', startIndex: 0, endIndex: rows[0]?.length || 1 },
      },
    });
  });
  // Highlight 본인 값 (E열) in checklist sheets - light yellow
  ['06_L1→L2', '07_L2→L3', '08_L3→L4', '09_L4→L5', '10_L5_유지'].forEach((name) => {
    const sid = sheetIdMap[name];
    if (sid === undefined) return;
    fmtReqs.push({
      repeatCell: {
        range: { sheetId: sid, startRowIndex: 4, startColumnIndex: 4, endColumnIndex: 5 },
        cell: { userEnteredFormat: { backgroundColor: { red: 1, green: 0.96, blue: 0.78 } } },
        fields: 'userEnteredFormat.backgroundColor',
      },
    });
  });
  // Delete placeholder
  fmtReqs.push({ deleteSheet: { sheetId: tmpId } });

  await sheets.spreadsheets.batchUpdate({
    spreadsheetId: SPREADSHEET_ID,
    requestBody: { requests: fmtReqs },
  });
  console.log('Formatting + placeholder cleanup done');

  console.log('\n✅ DONE — view: https://docs.google.com/spreadsheets/d/' + SPREADSHEET_ID);
}

main().catch(e => {
  console.error('❌ FAILED:', e.message);
  if (e.errors) console.error(JSON.stringify(e.errors, null, 2));
  process.exit(1);
});
