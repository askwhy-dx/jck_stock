// Fetch ONLY 01_거래일지 + 02_월별수익 from the Google Sheet,
// compute all KPIs / checklist results / dashboard locally in JS,
// and render a self-contained dark-theme HTML preview.
//
// Usage:
//   node build_preview_html.js
//
// Output:
//   C:\Users\User\Desktop\test\레벨_자동계산_미리보기.html
//
// All level math (roadmap / checklists / KPI) is defined in this file —
// the spreadsheet is pure input storage now.

const fs = require('fs');
const path = require('path');
const { google } = require('googleapis');

const SPREADSHEET_ID = '1DhDbm9jPTT5d7QNtFCp6jEPBOfdtQk3cs4SiwHr4R6Q';
const CREDS_PATH = path.join(__dirname, '..', '..', 'credentials.json');
const OUT_PATH   = path.join(__dirname, '레벨_자동계산_미리보기.html');

const TRADE_SHEET = '01_거래일지';
const SNAP_SHEET  = '02_월별수익';

// =============================================================
// User-declared progress state
// =============================================================
const CURRENT_LEVEL = 'L2';
const PASSED_LEVELS = ['L0', 'L1'];
const LEVEL_COLORS = { L0:'#6e7681', L1:'#3fb950', L2:'#58a6ff', L3:'#a371f7', L4:'#f85149', L5:'#d29922' };
// Active checklist = the one you're working to pass (CURRENT_LEVEL → next)
const nextLvl = 'L' + (parseInt(CURRENT_LEVEL.slice(1)) + 1);
const ACTIVE_KEY = `${CURRENT_LEVEL}→${nextLvl}`;

function isCompletedChecklist(key) {
  const m = key.match(/L(\d+)→L(\d+)/);
  if (!m) return false;
  return PASSED_LEVELS.includes('L' + m[1]);
}

// =============================================================
// Level roadmap (hardcoded — was 04_로드맵 sheet)
// L1 최소 기간: 3개월 (사용자 결정). L2=6, L3=9, L4=12.
// =============================================================
const ROADMAP = [
  { key:'L0', name:'⚪ 모의투자', seed:'가상 계좌\n(초기 1,000만)', period:'3개월', adapt:'해당 없음',
    holdings:'가상', perStock:'가상', cash:'—', mddCap:'—', floorAbs:'—', floorRel:'—',
    monthlyDep:'—', indep:'—', theme:'실전 전 학습\n(안 다치고 배우기)',
    mission:'1. 증권사 모의투자 앱 또는 엑셀 가상계좌 개설\n2. 3줄 매수 원칙 + 매매 일지 양식 구축\n3. 20회 이상 가상 거래 완결\n4. 주 1회 시장 리뷰 작성',
    graduate:'• 기간 3개월 이상\n• 가상 거래 20회 이상\n• 원칙 위반율 30% 이하\n• 손절 실행 경험 최소 3회',
    demote:'해당 없음', note:'실전 진입 전 필수 관문' },
  { key:'L1', name:'🟢 튜토리얼', seed:'10 ~ 50만 원', period:'3개월', adapt:'없음',
    holdings:'1종목', perStock:'시드 100%', cash:'0~10%', mddCap:'제한 없음',
    floorAbs:'불문', floorRel:'불문', monthlyDep:'월 5~10만 원', indep:'불문 (측정 시작)',
    theme:'기록·원칙의 습관화',
    mission:'1. 생활비 분리 투자 전용 계좌 + 10만 입금\n2. 사업 내용 한 문장 설명 가능한 종목만\n3. 3줄 매수 원칙 사전 기록\n4. 매매 일지 100% 작성 (6필드)\n5. 공시·재무제표·뉴스만 참고 (추천 금지)\n6. 주 1회 시장 리뷰 한 문단 요약\n7. 입금 시 \'원칙 유지 자가 점검\' 기록',
    graduate:'• 기간 3개월 이상\n• 매매 일지 100% 작성\n• 원칙 위반율 20% 이하\n• 실거래 10회 이상\n• 수익률 불문',
    demote:'• 원칙 위반율 30% 초과 지속\n• 매매 일지 미작성률 20% 초과',
    note:'수익률 평가 없음, 기록 완결성이 통과 기준' },
  { key:'L2', name:'🔵 초보 던전', seed:'50 ~ 200만 원', period:'6개월', adapt:'3개월',
    holdings:'최대 2종목', perStock:'시드 50~60%', cash:'10~20%', mddCap:'-15% 이내',
    floorAbs:'-10% 이상', floorRel:'누적 수익 양수', monthlyDep:'월 10~30만 원', indep:'측정·기록',
    theme:'원칙을 실제로 지키는가\n(손절 체화)',
    mission:'1. 2~3분할 매수, 첫 진입 30% 이하\n2. 손절 기계화 (예외 불문)\n3. 한 종목 시드의 50% 이하\n4. 월 1회 복기 리포트\n5. 외부 추천 진입 0건\n6. 손익비 1.5 이상 사전 검증\n7. 입금 후 1주일 신규 진입 금지\n8. 원금/수익/입금 3분리 회계 시작',
    graduate:'[AND] 기간 6개월+, 체급 적응기 통과, 손절 준수율 90%+, 누적 수익 양수, MDD -15% 이내, Floor -10%+, 원칙 위반율 10% 이하, 분할매수 완결성 100%',
    demote:'손절 준수율 80% 미만, MDD -15% 초과, Floor 하회 → L1 복귀',
    note:'첫 실전 체급' },
  { key:'L3', name:'🟣 중급 필드', seed:'200 ~ 1,000만 원', period:'9개월', adapt:'4개월',
    holdings:'3~5종목', perStock:'시드 25~30%', cash:'15~25%', mddCap:'-12% 이내',
    floorAbs:'-8% 이상', floorRel:'KOSPI ±3%p 이내/초과', monthlyDep:'월 30~100만 원', indep:'측정·공개',
    theme:'포트폴리오 운영 + 세후 관점',
    mission:'1. 3~5종목, 섹터 3개 이상\n2. 한 종목 최대 25%, 한 섹터 최대 40%\n3. 분기별 리밸런싱 (±5%p 초과 시)\n4. 분할 익절 3단계 체계화\n5. 기대값(EV) 계산 후 진입\n6. 실측 평단 엑셀 관리\n7. 매매 비용(수수료+세금) 매 거래 기록\n8. 연간 입금 < 연간 수익금 조건\n9. 원금/수익/입금/세금 4분리 회계',
    graduate:'[AND] 기간 9개월+, 체급 적응기 통과, 손절 95%+, 손익비 1.5+ (1.0 미달 0건), MDD -12% 이내, Floor -8%+, KOSPI ±3%p, 리밸런싱 4회+, 분할 익절 70%+, 수익>입금',
    demote:'손절 85% 미만, MDD -15% 이상, Floor 하회, 수익<입금 2년 연속 → L2 복귀',
    note:'단일 종목 → 포트폴리오 전환점' },
  { key:'L4', name:'🔴 고급 레이드', seed:'1,000 ~ 5,000만 원', period:'12개월', adapt:'6개월',
    holdings:'3~4종목', perStock:'시드 20~25%', cash:'20~30% (상시)', mddCap:'-10% 이내',
    floorAbs:'-5% 이상 (세후)', floorRel:'KOSPI + α (예: +3%p)', monthlyDep:'월 자율 (최대 200만)', indep:'30% 이상 필수',
    theme:'손실을 설계 + 수급 독립',
    mission:'1. 1거래 최대 손실 시드의 2% 이내\n2. 헤지 수단 1개 이상 실전 적용\n3. -10%/-20%/-30% 하락장 시나리오 문서화\n4. 스윙 + 장기 분리, 장기 30% 이상 배분\n5. 세후 수익 관리 (양도세·배당세 반영)\n6. 분기별 기법별·종목별 백테스트\n7. 현금 20% 이상 상시 확보\n8. 연 2회 \'수익 자립 감사\'',
    graduate:'[AND] 기간 12개월+, 체급 적응기 통과, 손절 98%+, 손익비 2.0+, MDD -10% 이내, Floor -5% 세후, 하락장 -10% 경험+대응, 변동성 KOSPI 이하, 자립 지수 30%+, 월 입금 0원 시뮬 통과',
    demote:'손절 90% 미만, MDD -12% 이상, Floor 하회 (세후), 자립 30% 미달 → L3 복귀',
    note:'월급 1~2개월치 단일 손실 감수 체급' },
  { key:'L5', name:'🟡 만렙 레이드', seed:'5,000만 원 이상', period:'상시 (졸업 없음)', adapt:'6개월',
    holdings:'5~7종목', perStock:'시드 15~20%', cash:'20% 상시', mddCap:'-10% 이내 (연)',
    floorAbs:'-5% 이상 (세후)', floorRel:'KOSPI + α', monthlyDep:'자율 (IPS 명시)', indep:'60% 이상',
    theme:'자산배분 + IPS + 지속성',
    mission:'1. IPS (투자정책서) 작성 + 연 1회 갱신\n2. 자산 배분: 주식/채권/현금/대체자산\n3. 생활비 6개월치 + 비상자금 분리\n4. ISA/연금저축/IRP 절세계좌 전략적 활용\n5. 5~7종목, 섹터 4개 이상, 한 종목 20% 상한\n6. 연간 복기 보고서 (본인용 IR 수준)\n7. 연 3권 이상 투자서 + 전략 설명 가능\n8. MDD -15% 초과 시 L4 자발 강등\n9. 목표 인출 시나리오 작성 (출구 전략)',
    graduate:'[상시] 연 KOSPI+α, Floor -5%+ (세후), MDD -10% 이내, IPS 95%+, 손절 100%, 연간 복기 보고서, 현금 20% 상시, 외부 입금 의존 30% 이하, 자립 60%+',
    demote:'MDD -15% 초과, 손절 예외 발생, IPS 85% 미만, 자립 60% 미달 지속 → L4 자발 강등',
    note:'졸업 없는 \'지속\'의 체급' },
];

// =============================================================
// Checklist definitions (hardcoded — was 06~10 sheets)
// kpi key references into the computed KPI object, or 'manual' for static
// op: '>=' | '<=' | '>' | '<' | '==' | 'Y' ; cmp threshold
// =============================================================
const CHECKLISTS = {
  'L1→L2': {
    title:'🟢 Level 1 → Level 2 진급 체크리스트',
    seedRange:'10~50만 원  →  50~200만 원',
    theme:'기록·원칙의 습관화  →  원칙 실행·손절 체화',
    passLabel:'🎉 L2 진급 가능',
    items: [
      { no:1, sec:'진급 조건', name:'운영 기간',              criteria:'≥ 3개월',     src:'monthsCount',   unit:'개월', op:'>=', t:3,   note:'최소 운영 기간 충족' },
      { no:2, sec:'진급 조건', name:'매매 일지 작성률',       criteria:'100%',         src:'journalRate',   unit:'%',    op:'>=', t:100, note:'3개월간 모든 거래 일지 작성' },
      { no:3, sec:'진급 조건', name:'원칙 위반율',            criteria:'≤ 20%',        src:'violationRate', unit:'%',    op:'<=', t:20,  note:'감정/뇌동 매매 비율' },
      { no:4, sec:'진급 조건', name:'실거래 횟수',            criteria:'≥ 10회',       src:'trCount',       unit:'회',   op:'>=', t:10,  note:'경험치 확보' },
      { no:5, sec:'진급 조건', name:'3줄 매수 원칙 사전 기록률', criteria:'100%',      manual:100,          unit:'%',    op:'>=', t:100, note:'매수 전 이유/익절/손절 기록 (수동)' },
      { no:6, sec:'강등 트리거', name:'원칙 위반율 30% 초과 지속', criteria:'< 30%',    src:'violationRate', unit:'%',    op:'>=', t:30,  note:'지속 3개월 이상' },
      { no:7, sec:'강등 트리거', name:'매매 일지 미작성률',   criteria:'< 20%',        src:'journalMiss',   unit:'%',    op:'>=', t:20,  note:'일지 없는 거래 비율' },
      { no:8, sec:'강등 트리거', name:'총 손실 한도 초과 (시드 -30%)', criteria:'> -30%', src:'minMDD',      unit:'%',    op:'<=', t:-30, note:'🚨 2단계 강등 트리거' },
    ],
  },
  'L2→L3': {
    title:'🔵 Level 2 → Level 3 진급 체크리스트',
    seedRange:'50~200만 원  →  200~1,000만 원',
    theme:'원칙 실행·손절 체화  →  포트폴리오 운영·세후 관점',
    passLabel:'🎉 L3 진급 가능',
    items: [
      { no:1,  sec:'진급 조건', name:'운영 기간',         criteria:'≥ 6개월',    src:'monthsCount',   unit:'개월', op:'>=', t:6,    note:'사이클 최소 1회 경험' },
      { no:2,  sec:'진급 조건', name:'체급 적응기 통과 (3개월)', criteria:'Y',  manual:'Y',          unit:'',     op:'Y',  note:'L1 → L2 전환 적응기' },
      { no:3,  sec:'진급 조건', name:'손절 준수율',       criteria:'≥ 90%',      src:'stopLossRate',  unit:'%',    op:'>=', t:90,   note:'손절가 도달 시 실제 매도 비율' },
      { no:4,  sec:'진급 조건', name:'누적 수익',         criteria:'양수',       src:'cumProfit',     unit:'만원', op:'>',  t:0,    note:'12개월 누적 수익' },
      { no:5,  sec:'진급 조건', name:'MDD (최대 낙폭)',   criteria:'≥ -15%',     src:'minMDD',        unit:'%',    op:'>=', t:-15,  note:'고점 대비 최대 낙폭 (음수)' },
      { no:6,  sec:'진급 조건', name:'연 Floor (절대)',   criteria:'≥ -10%',     src:'yearRet',       unit:'%',    op:'>=', t:-10,  note:'연간 수익률 (음수 입력)' },
      { no:7,  sec:'진급 조건', name:'분기 Floor 경보 횟수 (12분기 기준)', criteria:'≤ 3회', src:'floorAlerts', unit:'회', op:'<=', t:3, note:'분기 -7% 초과 발동 누적' },
      { no:8,  sec:'진급 조건', name:'원칙 위반율',       criteria:'≤ 10%',      src:'violationRate', unit:'%',    op:'<=', t:10,   note:'Level 1보다 타이트' },
      { no:9,  sec:'진급 조건', name:'분할매수 완결성',   criteria:'100%',       manual:100,          unit:'%',    op:'>=', t:100,  note:'진입 종목 100% 종결' },
      { no:10, sec:'강등 트리거', name:'손절 준수율 위반', criteria:'< 80%',     src:'stopLossRate',  unit:'%',    op:'<=', t:80,   note:'기준보다 -10%p 이상 낮음' },
      { no:11, sec:'강등 트리거', name:'MDD 한도 초과',   criteria:'< -15%',     src:'minMDD',        unit:'%',    op:'<=', t:-15,  note:'' },
      { no:12, sec:'강등 트리거', name:'연 Floor 하회',   criteria:'< -10%',     src:'yearRet',       unit:'%',    op:'<=', t:-10,  note:'' },
      { no:13, sec:'강등 트리거', name:'총 손실 -30% 초과', criteria:'> -30%',   src:'minMDD',        unit:'%',    op:'<=', t:-30,  note:'🚨 2단계 강등' },
    ],
  },
  'L3→L4': {
    title:'🟣 Level 3 → Level 4 진급 체크리스트',
    seedRange:'200~1,000만 원  →  1,000~5,000만 원',
    theme:'포트폴리오 운영·세후  →  리스크 설계·수급 독립',
    passLabel:'🎉 L4 진급 가능',
    items: [
      { no:1,  sec:'진급 조건', name:'운영 기간',         criteria:'≥ 9개월',    src:'monthsCount',   unit:'개월', op:'>=', t:9,   note:'' },
      { no:2,  sec:'진급 조건', name:'체급 적응기 통과 (4개월)', criteria:'Y',  manual:'Y', unit:'', op:'Y', note:'L2 → L3 전환 적응기' },
      { no:3,  sec:'진급 조건', name:'손절 준수율',       criteria:'≥ 95%',      src:'stopLossRate',  unit:'%',    op:'>=', t:95,  note:'' },
      { no:4,  sec:'진급 조건', name:'손익비 평균',       criteria:'≥ 1.5',      src:'profitFactor',  unit:'배',   op:'>=', t:1.5, note:'전체 거래 평균 손익비' },
      { no:5,  sec:'진급 조건', name:'손익비 1.0 미만 진입 건수', criteria:'0건', manual:0, unit:'건', op:'<=', t:0, note:'사전 계산 누락 금지 (수동)' },
      { no:6,  sec:'진급 조건', name:'MDD',               criteria:'≥ -12%',     src:'minMDD',        unit:'%',    op:'>=', t:-12, note:'' },
      { no:7,  sec:'진급 조건', name:'연 Floor',          criteria:'≥ -8%',      src:'yearRet',       unit:'%',    op:'>=', t:-8,  note:'' },
      { no:8,  sec:'진급 조건', name:'분기 Floor 경보 횟수', criteria:'≤ 3회',   src:'floorAlerts',   unit:'회',   op:'<=', t:3,   note:'분기 -6% 초과 발동' },
      { no:9,  sec:'진급 조건', name:'연 수익률 vs KOSPI 차이 (수동)', criteria:'±3%p 이내 또는 초과', manual:2, unit:'%p', op:'>=', t:-3, note:'KOSPI 대비 (본인 - KOSPI)' },
      { no:10, sec:'진급 조건', name:'리밸런싱 실행 횟수 (수동)', criteria:'≥ 4회/년', manual:4, unit:'회', op:'>=', t:4, note:'분기별 조정' },
      { no:11, sec:'진급 조건', name:'분할 익절 비율 (수동)',     criteria:'≥ 70%', manual:75, unit:'%', op:'>=', t:70, note:'수익 거래 중 분할 익절 비율' },
      { no:12, sec:'진급 조건', name:'수익 자생률 (수익 > 입금)', criteria:'Y', src:'selfSustain', unit:'', op:'Y', note:'연 수익금 > 연 입금액' },
      { no:13, sec:'강등 트리거', name:'손절 준수율 위반', criteria:'< 85%',     src:'stopLossRate',  unit:'%',    op:'<=', t:85,  note:'' },
      { no:14, sec:'강등 트리거', name:'MDD 한도 초과',   criteria:'< -15%',     src:'minMDD',        unit:'%',    op:'<=', t:-15, note:'' },
      { no:15, sec:'강등 트리거', name:'연 Floor 하회',   criteria:'< -8%',      src:'yearRet',       unit:'%',    op:'<=', t:-8,  note:'' },
      { no:16, sec:'강등 트리거', name:'수익 < 입금 2년 연속 (수동)', criteria:'Y / N', manual:'N', unit:'', op:'Y', note:'자립 실패 신호' },
      { no:17, sec:'강등 트리거', name:'총 손실 -30% 초과', criteria:'> -30%',   src:'minMDD',        unit:'%',    op:'<=', t:-30, note:'🚨 2단계 강등' },
    ],
  },
  'L4→L5': {
    title:'🔴 Level 4 → Level 5 진급 체크리스트',
    seedRange:'1,000~5,000만 원  →  5,000만 원+',
    theme:'리스크 설계·수급 독립  →  자산 배분·IPS·지속성',
    passLabel:'🎉 L5 진급 가능',
    items: [
      { no:1,  sec:'진급 조건', name:'운영 기간',         criteria:'≥ 12개월',   src:'monthsCount',   unit:'개월', op:'>=', t:12,  note:'' },
      { no:2,  sec:'진급 조건', name:'체급 적응기 통과 (6개월)', criteria:'Y',  manual:'Y', unit:'', op:'Y', note:'L3 → L4 전환 적응기' },
      { no:3,  sec:'진급 조건', name:'손절 준수율',       criteria:'≥ 98%',      src:'stopLossRate',  unit:'%',    op:'>=', t:98,  note:'' },
      { no:4,  sec:'진급 조건', name:'손익비 평균',       criteria:'≥ 2.0',      src:'profitFactor',  unit:'배',   op:'>=', t:2.0, note:'' },
      { no:5,  sec:'진급 조건', name:'MDD',               criteria:'≥ -10%',     src:'minMDD',        unit:'%',    op:'>=', t:-10, note:'' },
      { no:6,  sec:'진급 조건', name:'연 Floor (세후, 수동)', criteria:'≥ -5%',  manual:-2, unit:'%', op:'>=', t:-5, note:'양도세·배당세 반영' },
      { no:7,  sec:'진급 조건', name:'분기 Floor 경보 횟수', criteria:'≤ 2회',   src:'floorAlerts',   unit:'회',   op:'<=', t:2,   note:'분기 -4% 초과 발동' },
      { no:8,  sec:'진급 조건', name:'연 수익률 vs KOSPI (+α, 수동)', criteria:'≥ +3%p', manual:4, unit:'%p', op:'>=', t:3, note:'KOSPI 초과 α 달성' },
      { no:9,  sec:'진급 조건', name:'하락장(-10%) 경험 + 대응 (수동)', criteria:'Y', manual:'Y', unit:'', op:'Y', note:'시나리오대로 대응 성공' },
      { no:10, sec:'진급 조건', name:'연간 변동성 vs KOSPI (수동)', criteria:'≤ KOSPI 수준', manual:15, unit:'%', op:'<=', t:20, note:'KOSPI 표준편차 약 18~20%' },
      { no:11, sec:'진급 조건', name:'자립 지수',         criteria:'≥ 30%',      src:'indep',         unit:'%',    op:'>=', t:30,  note:'수익 ÷ (수익+입금)' },
      { no:12, sec:'진급 조건', name:'월 입금 0원 시뮬레이션 통과 (수동)', criteria:'Y', manual:'Y', unit:'', op:'Y', note:'입금 없이 시드 유지 가능' },
      { no:13, sec:'강등 트리거', name:'손절 준수율 위반', criteria:'< 90%',     src:'stopLossRate',  unit:'%',    op:'<=', t:90,  note:'' },
      { no:14, sec:'강등 트리거', name:'MDD 한도 초과',   criteria:'< -12%',     src:'minMDD',        unit:'%',    op:'<=', t:-12, note:'' },
      { no:15, sec:'강등 트리거', name:'연 Floor (세후) 하회 (수동)', criteria:'< -5%', manual:-2, unit:'%', op:'<=', t:-5, note:'' },
      { no:16, sec:'강등 트리거', name:'자립 지수 미달', criteria:'< 30%',      src:'indep',         unit:'%',    op:'<=', t:30,  note:'입금 과잉 의존 판정' },
      { no:17, sec:'강등 트리거', name:'총 손실 -30% 초과', criteria:'> -30%',   src:'minMDD',        unit:'%',    op:'<=', t:-30, note:'🚨 2단계 강등' },
    ],
  },
  'L5_유지': {
    title:'🟡 Level 5 상시 유지 체크리스트',
    seedRange:'5,000만 원+ (졸업 없음)',
    theme:'자산 배분 · IPS · 지속성',
    passLabel:'🎉 L5 유지 우수 (모든 조건 충족)',
    items: [
      { no:1,  sec:'유지 조건', name:'연 수익률 vs KOSPI (+α, 수동)', criteria:'≥ +3%p', manual:5, unit:'%p', op:'>=', t:3,  note:'' },
      { no:2,  sec:'유지 조건', name:'연 Floor (세후, 수동)', criteria:'≥ -5%', manual:-2, unit:'%', op:'>=', t:-5, note:'' },
      { no:3,  sec:'유지 조건', name:'MDD (연간)',       criteria:'≥ -10%',     src:'minMDD',        unit:'%',    op:'>=', t:-10, note:'' },
      { no:4,  sec:'유지 조건', name:'분기 Floor 경보 횟수', criteria:'≤ 2회/년', src:'floorAlerts', unit:'회', op:'<=', t:2, note:'' },
      { no:5,  sec:'유지 조건', name:'IPS 준수율 (수동)', criteria:'≥ 95%',      manual:97, unit:'%', op:'>=', t:95, note:'투자정책서 명시 규칙 준수' },
      { no:6,  sec:'유지 조건', name:'손절 준수율',       criteria:'100%',       src:'stopLossRate',  unit:'%',    op:'>=', t:100, note:'예외 없음' },
      { no:7,  sec:'유지 조건', name:'연간 복기 보고서 작성 (수동)', criteria:'Y', manual:'Y', unit:'', op:'Y', note:'연 1회 이상' },
      { no:8,  sec:'유지 조건', name:'현금 비중 상시 (수동)', criteria:'≥ 20%', manual:22, unit:'%', op:'>=', t:20, note:'' },
      { no:9,  sec:'유지 조건', name:'외부 입금 의존도', criteria:'≤ 30%',      src:'depDependency', unit:'%',    op:'<=', t:30,  note:'누적 입금 ÷ 시드' },
      { no:10, sec:'유지 조건', name:'자립 지수',         criteria:'≥ 60%',      src:'indep',         unit:'%',    op:'>=', t:60,  note:'' },
      { no:11, sec:'강등 트리거', name:'MDD 한도 초과',   criteria:'< -15%',     src:'minMDD',        unit:'%',    op:'<=', t:-15, note:'자발 강등 기준' },
      { no:12, sec:'강등 트리거', name:'손절 예외 발생 (수동)', criteria:'1회라도', manual:0, unit:'회', op:'>=', t:1, note:'손절 예외 = 즉시 강등' },
      { no:13, sec:'강등 트리거', name:'IPS 준수율 위반 (수동)', criteria:'< 85%', manual:97, unit:'%', op:'<=', t:85, note:'' },
      { no:14, sec:'강등 트리거', name:'자립 지수 60% 미달 지속 (수동)', criteria:'Y / N', manual:'N', unit:'', op:'Y', note:'구조적 의존 판정' },
      { no:15, sec:'강등 트리거', name:'총 손실 -30% 초과', criteria:'> -30%',   src:'minMDD',        unit:'%',    op:'<=', t:-30, note:'🚨 2단계 강등' },
    ],
  },
};

// =============================================================
// KPI computation (JS replacement for the old 03_KPI sheet formulas)
// =============================================================
function toNum(v) {
  if (v === '' || v === null || v === undefined) return null;
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
}
function eqY(v) { return String(v ?? '').trim().toUpperCase() === 'Y'; }

function computeKPI(tradeRows, snapRows) {
  // trade header in row 0; data from row 1
  const trades = tradeRows.slice(1);
  // Column layout: 0 No, 1 매수일, 2 매도일, 3 종목명, 4 매수가, 5 매도가, 6 수량, 7 매수금액,
  //                8 매도금액, 9 손익, 10 수익률, 11 손절가, 12 손절도달, 13 손절준수, 14 원칙위반
  const hasName = trades.filter(r => r[3] !== '' && r[3] !== undefined);
  const trCount = hasName.length;

  const profitsPct = hasName.map(r => toNum(r[10])).filter(n => n !== null);
  const wins = profitsPct.filter(n => n > 0);
  const losses = profitsPct.filter(n => n < 0);
  const avgRet = profitsPct.length ? profitsPct.reduce((a,b)=>a+b,0)/profitsPct.length : 0;
  const avgWin = wins.length ? wins.reduce((a,b)=>a+b,0)/wins.length : 0;
  const avgLoss = losses.length ? losses.reduce((a,b)=>a+b,0)/losses.length : 0;
  const profitFactor = avgLoss !== 0 ? avgWin / Math.abs(avgLoss) : 0;
  const winRate = profitsPct.length ? wins.length / profitsPct.length * 100 : 0;

  const stopReach = hasName.filter(r => eqY(r[12])).length;
  const stopDone  = hasName.filter(r => eqY(r[12]) && eqY(r[13])).length;
  const stopLossRate = stopReach > 0 ? stopDone / stopReach * 100 : 0;
  const violationCount = hasName.filter(r => eqY(r[14])).length;
  const violationRate = trCount > 0 ? violationCount / trCount * 100 : 0;

  // Snapshot: 0 연월, 1 기말잔고, 2 당월입금, 3 누적원금+입금, 4 당월수익률, 5 누적최고점, 6 MDD
  const snaps = snapRows.slice(1).filter(r => r[0] !== '' && r[0] !== undefined);
  const monthsCount = snaps.length;
  const lastBal = snaps.length ? (toNum(snaps[snaps.length - 1][1]) ?? 0) : 0;
  const deposits = snaps.map(r => toNum(r[2])).filter(n => n !== null);
  const totalDep = deposits.reduce((a,b)=>a+b,0);
  const depMonths = deposits.filter(n => n > 0).length;
  const mdds = snaps.map(r => toNum(r[6])).filter(n => n !== null);
  const minMDD = mdds.length ? Math.min(...mdds) : 0;
  const floorAlerts = mdds.filter(n => n <= -7).length;

  // 12-month rolling: last 12 snaps
  const last12 = snaps.slice(Math.max(0, snaps.length - 12));
  const bal12ago = last12.length ? (toNum(last12[0][1]) ?? 0) : 0;
  const dep12sum = last12.reduce((s, r) => s + (toNum(r[2]) ?? 0), 0);
  const yearRet = bal12ago !== 0 ? (lastBal - bal12ago - dep12sum) / bal12ago * 100 : 0;

  const cumProfit = lastBal - totalDep;
  const indep = lastBal > 0 && cumProfit > 0 ? cumProfit / lastBal * 100 : 0;
  const avgMonthlyDep = depMonths > 0 ? totalDep / depMonths : 0;

  const journalRate = trCount > 0 ? 100 : 0;
  const journalMiss = 100 - journalRate;
  const depDependency = lastBal > 0 ? totalDep / lastBal * 100 : 0;
  const selfSustain = cumProfit > totalDep ? 'Y' : 'N';

  return {
    trCount, winRate, avgRet, avgWin, avgLoss, profitFactor,
    stopReach, stopDone, stopLossRate, violationCount, violationRate,
    monthsCount, lastBal, totalDep, depMonths, avgMonthlyDep,
    minMDD, floorAlerts, bal12ago, dep12sum, yearRet,
    cumProfit, indep, journalRate, journalMiss, depDependency, selfSustain,
    // Manual defaults (shown on KPI table)
    splitCompleteness: 100,
    classAdaptation: 'Y',
  };
}

// =============================================================
// Checklist evaluation
// =============================================================
function evalItem(item, kpi) {
  const actual = item.src != null ? kpi[item.src] : item.manual;
  let triggered = false;
  if (item.op === '>=') triggered = toNum(actual) !== null && toNum(actual) >= item.t;
  else if (item.op === '<=') triggered = toNum(actual) !== null && toNum(actual) <= item.t;
  else if (item.op === '>')  triggered = toNum(actual) !== null && toNum(actual) >  item.t;
  else if (item.op === '<')  triggered = toNum(actual) !== null && toNum(actual) <  item.t;
  else if (item.op === 'Y')  triggered = eqY(actual);

  const isDemote = item.sec.includes('강등');
  // For promo/유지: triggered == meeting promo threshold == 충족
  // For demote: triggered == threshold met (BAD) == 발동
  const result = isDemote
    ? (triggered ? '⚠️ 발동' : '⚪ 해당 없음')
    : (triggered ? '✅ 충족' : '❌ 미충족');
  return { ...item, actual, triggered, result };
}

function evalChecklist(key, kpi, override) {
  const def = CHECKLISTS[key];
  const items = def.items.map(it => evalItem(it, kpi));
  const promos = items.filter(i => !i.sec.includes('강등'));
  const demotes = items.filter(i =>  i.sec.includes('강등'));
  const passCount = promos.filter(i => i.triggered).length;
  const trigCount = demotes.filter(i => i.triggered).length;
  let final;
  if (override === 'pass') final = '🎉 통과 완료';
  else if (passCount >= promos.length && trigCount === 0) final = def.passLabel;
  else if (trigCount >= 1) final = '🔻 강등/유지';
  else final = '🟡 유지 (재수)';

  // If override=pass, flip visual fields for items
  const renderedItems = override === 'pass'
    ? items.map(i => ({ ...i, triggered: !i.sec.includes('강등'), result: i.sec.includes('강등') ? '⚪ 해당 없음' : '✅ 충족' }))
    : items;

  return {
    def, items: renderedItems,
    passCount: override === 'pass' ? promos.length : passCount,
    passTotal: promos.length,
    trigCount: override === 'pass' ? 0 : trigCount,
    trigTotal: demotes.length,
    final,
    override: override === 'pass',
  };
}

// =============================================================
// Fetch raw input sheets
// =============================================================
async function fetchInputs() {
  const auth = new google.auth.GoogleAuth({
    keyFile: CREDS_PATH,
    scopes: ['https://www.googleapis.com/auth/spreadsheets.readonly'],
  });
  const sheets = google.sheets({ version: 'v4', auth });
  const res = await sheets.spreadsheets.values.batchGet({
    spreadsheetId: SPREADSHEET_ID,
    ranges: [`'${TRADE_SHEET}'!A1:O60`, `'${SNAP_SHEET}'!A1:G40`],
    valueRenderOption: 'UNFORMATTED_VALUE',
  });
  return {
    trades: res.data.valueRanges[0].values || [],
    snaps:  res.data.valueRanges[1].values || [],
  };
}

// =============================================================
// Rendering helpers
// =============================================================
function esc(v) {
  if (v === null || v === undefined) return '';
  return String(v).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/\n/g,'<br>');
}
function fmt(v, dec = 1) {
  const n = toNum(v);
  if (n === null) return esc(v);
  if (Math.abs(n - Math.round(n)) < 1e-9) return n.toLocaleString();
  return n.toFixed(dec);
}
function judgeColor(text) {
  if (!text) return '';
  if (text.includes('충족') || text.includes('진급') || text.includes('통과')) return '#3fb950';
  if (text.includes('미충족') || text.includes('강등') || text.includes('🔻')) return '#f85149';
  if (text.includes('해당 없음')) return '#8b949e';
  if (text.includes('발동') || text.includes('재수') || text.includes('🟡')) return '#d29922';
  return '';
}

// =============================================================
// Renderers (section-order aware — numeric prefix assigned at render time)
// =============================================================
function renderKPI(num, kpi) {
  // Summary grid with 현재 레벨 + highlight KPIs
  const curC = LEVEL_COLORS[CURRENT_LEVEL] || '#58a6ff';
  const passedBadges = PASSED_LEVELS.map(lv => {
    const c = LEVEL_COLORS[lv] || '#8b949e';
    return `<span class="badge" style="background:${c}22; color:${c}; border:1px solid ${c};">${lv} ✅</span>`;
  }).join(' ');
  const levelCard = `<div class="summary-item" style="background:${curC}1a; border:1px solid ${curC}; grid-column:span 2;">
    <div class="label" style="color:${curC};">현재 레벨</div>
    <div class="value" style="color:${curC}; font-size:1.6em;">${CURRENT_LEVEL}</div>
    <div style="font-size:0.75em; color:#8b949e; margin-top:6px;">통과: ${passedBadges}</div>
  </div>`;

  const highlights = [
    { label:'운영 기간',       val:kpi.monthsCount,   unit:'개월', good:(n)=> n>=3,  warn:(n)=> n>=1 },
    { label:'손절 준수율',     val:kpi.stopLossRate,  unit:'%',    good:(n)=> n>=90, warn:(n)=> n>=80 },
    { label:'원칙 위반율',     val:kpi.violationRate, unit:'%',    good:(n)=> n<=10, warn:(n)=> n<=20, invert:true },
    { label:'MDD (원금 회손)',   val:kpi.minMDD,        unit:'%',    good:(n)=> n>=-10, warn:(n)=> n>=-15 },
    { label:'연 Floor (손절 %)', val:kpi.yearRet,       unit:'%',    good:(n)=> n>=-5, warn:(n)=> n>=-10 },
    { label:'자립 지수',       val:kpi.indep,         unit:'%',    good:(n)=> n>=30, warn:(n)=> n>0 },
    { label:'손익비',          val:kpi.profitFactor,  unit:'배',   good:(n)=> n>=1.5, warn:(n)=> n>=1 },
    { label:'실거래 횟수',     val:kpi.trCount,       unit:'회',   good:(n)=> n>=10, warn:(n)=> n>=1 },
  ];
  const cards = levelCard + highlights.map(h => {
    const n = toNum(h.val);
    let color = '#58a6ff';
    if (n !== null) color = h.good(n) ? '#3fb950' : (h.warn(n) ? '#d29922' : '#f85149');
    return `<div class="summary-item">
      <div class="label">${esc(h.label)}</div>
      <div class="value" style="color:${color};">${fmt(h.val)} ${esc(h.unit)}</div>
    </div>`;
  }).join('');

  // Full table
  const rows = [
    ['거래 통계','총 거래 횟수',     kpi.trCount,       '건',  '종목명 입력된 거래 수'],
    ['',          '승률',              kpi.winRate,       '%',   '수익률>0 / 손익 기록된 거래 × 100'],
    ['',          '평균 수익률',       kpi.avgRet,        '%',   '거래일지 수익률(%)의 평균'],
    ['',          '평균 수익 (익절)',  kpi.avgWin,        '%',   '수익률>0인 거래 평균'],
    ['',          '평균 손실 (손절)',  kpi.avgLoss,       '%',   '수익률<0인 거래 평균'],
    ['',          '손익비 (PF) ⭐',    kpi.profitFactor,  '배',  '평균 수익 ÷ |평균 손실|'],
    ['원칙 준수','손절가 도달 거래',  kpi.stopReach,     '건',  'M열 Y'],
    ['',          '손절 실행',         kpi.stopDone,      '건',  'M=Y AND N=Y'],
    ['',          '손절 준수율 ⭐',    kpi.stopLossRate,  '%',   '실행 / 도달 × 100'],
    ['',          '원칙 위반 거래',    kpi.violationCount,'건',  'O열 Y'],
    ['',          '원칙 위반율 ⭐',    kpi.violationRate, '%',   '위반 / 전체'],
    ['리스크',   'MDD · 원금 회손 ⭐', kpi.minMDD,       '%',   '월별 MDD 최소값 (고점 대비 최대 낙폭 = 원금 훼손 규모)'],
    ['',          '연 Floor · 손절 % ⭐', kpi.yearRet,    '%',   '(최근잔고 - 12M전잔고 - 12M입금) / 12M전잔고 — 연 수익률 하한선'],
    ['',          '분기 Floor 경보',   kpi.floorAlerts,   '회',  '월 MDD ≤ -7%인 월'],
    ['시드 구성','현재 시드',         kpi.lastBal,       '만원','마지막 기말 잔고'],
    ['',          '누적 입금액',       kpi.totalDep,      '만원','당월 입금액 합계'],
    ['',          '누적 수익',         kpi.cumProfit,     '만원','시드 - 누적 입금'],
    ['',          '자립 지수 ⭐',      kpi.indep,         '%',   '누적 수익 / 현재 시드'],
    ['',          '월 평균 입금',      kpi.avgMonthlyDep, '만원','입금 ÷ 입금 발생 월수'],
    ['추가',      '운영 기간 ⭐',      kpi.monthsCount,   '개월','월별 스냅샷 행 수'],
    ['',          '매매 일지 작성률 ⭐', kpi.journalRate, '%',  '거래 1건이라도 100%'],
    ['',          '분할매수 완결성',   kpi.splitCompleteness, '%', '수동(기본 100)'],
    ['',          '체급 적응기 통과',  kpi.classAdaptation,   '',  '수동(기본 Y)'],
  ];
  const tbody = rows.map(r => `<tr>
    <td>${esc(r[0] || '')}</td>
    <td style="text-align:left;">${esc(r[1] || '')}</td>
    <td><strong>${fmt(r[2])}</strong></td>
    <td>${esc(r[3] || '')}</td>
    <td style="text-align:left; font-size:0.85em; color:#8b949e;">${esc(r[4] || '')}</td>
  </tr>`).join('');

  return `
  <h2>${num} · 📊 KPI 자동 집계</h2>
  <div class="card">
    <div class="card-header">
      <div>
        <h3 style="margin:0;">⭐ 핵심 지표 카드</h3>
        <span style="color:#8b949e; font-size:0.85em;">⭐ 표시 항목은 체크리스트의 본인값으로 자동 인입됩니다.</span>
      </div>
    </div>
    <div class="summary-grid">${cards}</div>
  </div>
  <div class="card">
    <h3>📋 전체 KPI 표</h3>
    <table class="kpi-table">
      <thead><tr>
        <th>구분</th><th>KPI 항목</th><th>값</th><th>단위</th><th>산출 공식 설명</th>
      </tr></thead>
      <tbody>${tbody}</tbody>
    </table>
  </div>`;
}

function renderRoadmap(num) {
  const colors = ['#6e7681','#3fb950','#58a6ff','#a371f7','#f85149','#d29922'];
  const cards = ROADMAP.map((r, i) => {
    const c = colors[i] || '#8b949e';
    const passed = PASSED_LEVELS.includes(r.key);
    const isCurrent = CURRENT_LEVEL === r.key;
    const badge = passed
      ? `<span class="badge" style="background:#3fb95022; color:#3fb950; border:1px solid #3fb950; margin-left:6px;">✅ 통과</span>`
      : isCurrent
      ? `<span class="badge" style="background:${c}22; color:${c}; border:1px solid ${c}; margin-left:6px;">📍 현재</span>`
      : '';
    const bg = passed ? 'background:#0f1d12;' : (isCurrent ? `background:${c}10;` : '');
    return `
    <div class="theory-card" style="border-left:4px solid ${c}; ${bg}">
      <h4 style="color:${c};">Level ${i} · ${esc(r.name)}${badge}</h4>
      <table class="mini-table">
        <tr><td class="k">시드 규모</td><td>${esc(r.seed)}</td></tr>
        <tr><td class="k">최소 기간</td><td><strong>${esc(r.period)}</strong></td></tr>
        <tr><td class="k">동시 보유</td><td>${esc(r.holdings)}</td></tr>
        <tr><td class="k">종목당 상한</td><td>${esc(r.perStock)}</td></tr>
        <tr><td class="k">MDD 한도</td><td>${esc(r.mddCap)}</td></tr>
        <tr><td class="k">Floor (절대)</td><td>${esc(r.floorAbs)}</td></tr>
        <tr><td class="k">자립 지수 목표</td><td>${esc(r.indep)}</td></tr>
        <tr><td class="k">핵심 테마</td><td><strong>${esc(r.theme)}</strong></td></tr>
      </table>
      <div class="mission-block">
        <div class="mission-title" style="color:${c};">🎯 주요 미션</div>
        <div class="mission-body">${esc(r.mission)}</div>
      </div>
      <div class="mission-block" style="background:#1a2e1a;">
        <div class="mission-title" style="color:#3fb950;">✅ 졸업 / 유지 조건</div>
        <div class="mission-body">${esc(r.graduate)}</div>
      </div>
      <div class="mission-block" style="background:#2e1a1a;">
        <div class="mission-title" style="color:#f85149;">⚠️ 강등 트리거</div>
        <div class="mission-body">${esc(r.demote)}</div>
      </div>
    </div>`;
  }).join('');

  return `
  <h2>${num} · 🗺️ 레벨 로드맵 (L0 → L5)</h2>
  <div class="card">
    <div class="theory-grid">${cards}</div>
  </div>`;
}

function renderChecklist(num, evaluation, opts = {}) {
  const { def, items, passCount, passTotal, trigCount, trigTotal, final, override } = evaluation;
  const finalColor = judgeColor(final);
  const promoLabel = items[0]?.sec === '유지 조건' ? '✅ 유지 조건 통과 수' : '✅ 진급 조건 통과 수';
  const banner = override
    ? `<div class="highlight" style="background:#3fb95022; border-left:3px solid #3fb950; color:#3fb950; font-weight:bold;">
         ✅ 사용자 선언 통과 — 전체 충족 처리됨
       </div>`
    : (opts.activeBanner
      ? `<div class="highlight" style="background:#d2992222; border-left:3px solid #d29922; color:#d29922; font-weight:bold;">
           📍 현재 진행 중인 체크리스트 — 목표: ${esc(def.passLabel)}
         </div>`
      : '');

  const tbody = items.map(r => {
    const sec = r.sec;
    const isTrigger = sec.includes('강등');
    const sectionBadge = isTrigger
      ? `<span class="badge" style="background:#f8514922; color:#f85149; border:1px solid #f85149;">${esc(sec)}</span>`
      : `<span class="badge" style="background:#3fb95022; color:#3fb950; border:1px solid #3fb950;">${esc(sec)}</span>`;
    const resultColor = judgeColor(r.result);
    return `<tr>
      <td>${r.no}</td>
      <td>${sectionBadge}</td>
      <td style="text-align:left;">${esc(r.name)}</td>
      <td style="color:#8b949e;">${esc(r.criteria)}</td>
      <td><strong>${fmt(r.actual)}</strong></td>
      <td style="color:#8b949e;">${esc(r.unit)}</td>
      <td style="color:${resultColor}; font-weight:bold;">${esc(r.result)}</td>
      <td style="text-align:left; font-size:0.85em; color:#8b949e;">${esc(r.note)}</td>
    </tr>`;
  }).join('');

  const borderColor = opts.activeBanner ? '#d29922' : (finalColor || '#58a6ff');
  return `
  ${opts.header ? `<h2>${opts.header}</h2>` : ''}
  <div class="card" style="border-left:4px solid ${borderColor};">
    ${banner}
    <div class="card-header">
      <div>
        <h3 style="margin:0;">${esc(def.title)}${override ? ' <span style="color:#3fb950;">✅</span>' : ''}</h3>
        <div style="font-size:0.85em; color:#8b949e; margin-top:4px;">💰 ${esc(def.seedRange)}<br>🎯 ${esc(def.theme)}</div>
      </div>
      <div class="weight-info" style="flex-wrap:wrap;">
        <span class="weight-chip">${esc(promoLabel)}: <strong>${passCount} / ${passTotal}</strong></span>
        <span class="weight-chip">🚨 강등 트리거 발동: <strong>${trigCount} / ${trigTotal}</strong></span>
        <span class="weight-chip" style="background:${finalColor}33; color:${finalColor}; border:1px solid ${finalColor};">최종: <strong>${esc(final)}</strong></span>
      </div>
    </div>
    <table class="check-table">
      <thead><tr>
        <th>No.</th><th>구분</th><th>항목</th><th>기준</th><th>본인 값</th><th>단위</th><th>결과</th><th>비고</th>
      </tr></thead>
      <tbody>${tbody}</tbody>
    </table>
  </div>`;
}

// =============================================================
// HTML shell
// =============================================================
function buildHTML(kpi, evals) {
  const now = new Date().toLocaleString('ko-KR', { timeZone: 'Asia/Seoul' });

  // Section order (대시보드 제거): KPI → Active checklist → Roadmap → rest
  const orderedChecklistKeys = ['L1→L2', 'L3→L4', 'L4→L5', 'L5_유지'];
  const activeEval = evals[ACTIVE_KEY];
  const otherChecklists = orderedChecklistKeys.map(k => ({ key:k, ev: evals[k] }));

  const sections = [];
  let n = 1;
  const nn = () => String(n++).padStart(2, '0');
  sections.push(renderKPI(nn(), kpi));
  if (activeEval) {
    const num = nn();
    sections.push(renderChecklist(num, activeEval, { header: `${num} · 🎯 현재 진행 체크리스트 (${ACTIVE_KEY})`, activeBanner: true }));
  }
  sections.push(renderRoadmap(nn()));
  sections.push(`<h2>${nn()} · 📑 나머지 레벨 체크리스트</h2>`);
  for (const { key, ev } of otherChecklists) {
    if (!ev) continue;
    sections.push(renderChecklist('', ev, {}));
  }

  return `<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>레벨 자동계산 미리보기</title>
<style>
  * { margin:0; padding:0; box-sizing:border-box; }
  body { font-family:'Segoe UI','Malgun Gothic',sans-serif; background:#0d1117; color:#c9d1d9; line-height:1.6; padding:20px; }
  .container { max-width:1240px; margin:0 auto; }
  h1 { color:#58a6ff; font-size:1.7em; margin-bottom:8px; border-bottom:2px solid #30363d; padding-bottom:12px; }
  h2 { color:#f0883e; font-size:1.35em; margin:32px 0 12px; }
  h3 { color:#7ee787; font-size:1.1em; margin:16px 0 8px; }
  h4 { color:#f0883e; font-size:1em; margin-bottom:8px; }
  .subtitle { color:#8b949e; font-size:0.88em; margin-bottom:24px; }
  .badge { display:inline-block; padding:3px 10px; border-radius:12px; font-size:0.78em; font-weight:bold; margin-right:4px; }
  .card { background:#161b22; border:1px solid #30363d; border-radius:8px; padding:18px; margin-bottom:18px; }
  .card-header { display:flex; justify-content:space-between; align-items:flex-start; margin-bottom:14px; flex-wrap:wrap; gap:10px; }
  .weight-info { display:inline-flex; gap:6px; align-items:center; font-size:0.83em; color:#8b949e; flex-wrap:wrap; }
  .weight-chip { background:#30363d; padding:3px 10px; border-radius:4px; font-family:monospace; font-size:0.85em; }
  .summary-grid { display:grid; grid-template-columns:repeat(auto-fit, minmax(180px,1fr)); gap:10px; margin:12px 0; }
  .summary-item { background:#21262d; border-radius:6px; padding:12px; text-align:center; }
  .summary-item .label { color:#8b949e; font-size:0.78em; }
  .summary-item .value { font-size:1.2em; font-weight:bold; margin-top:4px; }
  .highlight { background:#1f6feb22; padding:10px 14px; margin:10px 0; border-radius:0 6px 6px 0; font-size:0.88em; }
  .theory-grid { display:grid; grid-template-columns:repeat(auto-fit, minmax(330px,1fr)); gap:14px; }
  .theory-card { background:#0d1117; border:1px solid #30363d; border-radius:6px; padding:14px; }
  .mini-table { width:100%; border-collapse:collapse; font-size:0.83em; margin:8px 0; }
  .mini-table td { padding:4px 6px; border-bottom:1px solid #21262d; vertical-align:top; }
  .mini-table td.k { color:#8b949e; width:35%; }
  .mission-block { background:#21262d; padding:8px 10px; border-radius:5px; margin-top:8px; font-size:0.82em; }
  .mission-title { font-weight:bold; margin-bottom:4px; }
  .mission-body { color:#c9d1d9; white-space:pre-wrap; }
  table { width:100%; border-collapse:collapse; margin:10px 0; font-size:0.85em; }
  th { background:#21262d; color:#f0883e; padding:8px 6px; text-align:center; border:1px solid #30363d; white-space:nowrap; font-size:0.85em; }
  td { padding:8px 6px; border:1px solid #30363d; text-align:center; vertical-align:top; }
  tr:nth-child(even) { background:#161b2280; }
  tr:hover { background:#1c2333; }
  .footer { text-align:center; color:#484f58; font-size:0.78em; margin-top:30px; padding-top:16px; border-top:1px solid #30363d; }
  .meta-bar { background:#161b22; border:1px solid #30363d; border-radius:8px; padding:14px 18px; margin-bottom:18px; display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:10px; }
  .meta-bar .left { font-size:0.92em; }
  .meta-bar a { color:#58a6ff; text-decoration:none; }
  .meta-bar a:hover { text-decoration:underline; }
  @media (max-width:768px) {
    body { padding:10px; }
    h1 { font-size:1.25em; }
    h2 { font-size:1.1em; }
    .card { padding:12px; }
    .summary-grid { grid-template-columns:1fr 1fr; }
    table { font-size:0.78em; }
    th, td { padding:5px 3px; }
  }
</style>
</head>
<body>
<div class="container">

<h1>📊 레벨 자동계산 미리보기</h1>
<p class="subtitle">입력은 구글 시트의 <strong style="color:#d29922;">01_거래일지 / 02_월별수익</strong> 두 장뿐. 나머지 계산 (KPI · 로드맵 · 체크리스트 · 대시보드)은 전부 이 페이지에서 실행됩니다.</p>

<div class="meta-bar">
  <div class="left">
    🔄 <strong>마지막 갱신</strong>: ${esc(now)}<br>
    📂 입력 시트: <a href="https://docs.google.com/spreadsheets/d/${SPREADSHEET_ID}" target="_blank">Google Sheet 열기</a>
  </div>
  <div class="weight-info">
    <span class="weight-chip">재빌드: <code>node build_preview_html.js</code></span>
  </div>
</div>

${sections.join('\n')}

<div class="footer">
  생성: ${esc(now)} · 계산: 클라이언트(JS) · 입력: Google Sheets API
</div>

</div>
</body>
</html>`;
}

// =============================================================
// Main
// =============================================================
async function main() {
  console.log('Fetching 01_거래일지 + 02_월별수익 ...');
  const { trades, snaps } = await fetchInputs();
  console.log('  trades:', trades.length, 'rows');
  console.log('  snaps :', snaps.length, 'rows');

  const kpi = computeKPI(trades, snaps);
  console.log('KPI: trCount=%d, monthsCount=%d, stopLossRate=%s%%, minMDD=%s%%',
    kpi.trCount, kpi.monthsCount, kpi.stopLossRate.toFixed(1), kpi.minMDD.toFixed(1));

  // Evaluate all 5 checklists (override passed transitions)
  const evals = {};
  for (const key of ['L1→L2','L2→L3','L3→L4','L4→L5','L5_유지']) {
    const override = isCompletedChecklist(key) ? 'pass' : null;
    evals[key] = evalChecklist(key, kpi, override);
  }

  const html = buildHTML(kpi, evals);
  fs.writeFileSync(OUT_PATH, html, 'utf8');
  console.log('\n✅ Wrote:', OUT_PATH);
  console.log('   Size :', (fs.statSync(OUT_PATH).size / 1024).toFixed(1), 'KB');
  console.log('   Open : file:///' + OUT_PATH.replace(/\\/g, '/'));
}

main().catch(e => {
  console.error('❌ FAILED:', e.message);
  if (e.errors) console.error(JSON.stringify(e.errors, null, 2));
  process.exit(1);
});
