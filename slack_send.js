/**
 * slack_send.js — 분석 완료 후 Slack Incoming Webhook으로 알림 발송
 *
 * 입력:
 *   - stock/data/dante_analysis_{YYYY-MM-DD}_{HHMM}.json (claude_dante_analyze.js 산출)
 *   - 또는 최신 파일 자동 선택
 *
 * 의존 (.env):
 *   SLACK_WEBHOOK_URL    Incoming Webhook URL (https://hooks.slack.com/services/...)
 *   DASHBOARD_URL        분석 대시보드 URL (선택, 기본: GitHub Pages)
 *   SLACK_USERNAME       발신자명 (선택, 기본: "단테 자동분석")
 *   SLACK_ICON_EMOJI     아이콘 이모지 (선택, 기본: :chart_with_upwards_trend:)
 *
 * 사용:
 *   node slack_send.js                               # 가장 최신 분석 결과 발송
 *   node slack_send.js --json=dante_analysis_*.json  # 특정 분석 결과
 *   node slack_send.js --dry-run                     # 본문만 콘솔 출력
 */
const fs = require('fs');
const path = require('path');
const https = require('https');
require('dotenv').config({ path: path.join(__dirname, '..', '.env') });

const argv = process.argv.slice(2);
const DRY_RUN = argv.includes('--dry-run');
const RECOMMEND_ONLY = argv.includes('--recommend-only');
const JSON_ARG = (argv.find(a => a.startsWith('--json=')) || '').slice('--json='.length);

const ROOT = __dirname;
const DATA_DIR = path.join(ROOT, 'data');
const HTML_PATH = path.join(ROOT, '분석_대시보드.html');

const WEBHOOK_URL = process.env.SLACK_WEBHOOK_URL;
const DASHBOARD_URL = process.env.DASHBOARD_URL || 'https://jichang999-dotcom.github.io/tes2/financial/분석_대시보드.html';
const USERNAME = process.env.SLACK_USERNAME || '단테 자동분석';
const ICON_EMOJI = process.env.SLACK_ICON_EMOJI || ':chart_with_upwards_trend:';

function log(msg) {
  const ts = new Date(Date.now() + 9 * 60 * 60 * 1000).toISOString().replace('T', ' ').slice(0, 19);
  console.log(`[${ts}] ${msg}`);
}

function findLatestAnalysisJson() {
  if (JSON_ARG) return path.join(DATA_DIR, JSON_ARG);
  const files = fs.readdirSync(DATA_DIR)
    .filter(f => /^dante_analysis_\d{4}-\d{2}-\d{2}_\d{4}\.json$/.test(f))
    .sort();
  if (files.length === 0) throw new Error('dante_analysis_*.json 파일 없음 — claude_dante_analyze.js 선실행 필요');
  return path.join(DATA_DIR, files[files.length - 1]);
}

/**
 * 대시보드(분석_대시보드.html) 카드 노출 순서를 종목코드 → 순번 Map으로 읽는다.
 * 순서 SSOT는 HTML이다 — reorder_active_cards.js가 [2d]에서 HTML을 재정렬한 뒤
 * [5] slack_send.js가 돌기 때문에, 여기서 읽으면 화면과 같은 순서가 나온다.
 * 정렬 규칙(§4.2 배치 3원칙)을 여기서 다시 구현하지 않는 이유 = 두 곳이 갈리면
 * 무증상으로 순서만 어긋나기 때문 (2026-08-26 사용자 지시: 대시보드와 동일 순서).
 *
 * 카드 id 우선순위는 claude_dante_analyze.js resolveCardRange와 동일:
 *   card-{code}-active → card-{code}-cand → card-{code}(bare)
 * -old · -closed · -archived · -removed-YYYYMMDD · -preserve 등 과거 카드와 display:none 카드는 제외.
 */
let _dashOrderCache;
function loadDashboardOrder() {
  if (_dashOrderCache !== undefined) return _dashOrderCache;
  try {
    const html = fs.readFileSync(HTML_PATH, 'utf-8');
    const re = /<div class="card" id="card-(\d{6})(-active|-cand)?"([^>]*)>/g;
    const PRIO = { '-active': 0, '-cand': 1, '': 2 };
    const best = new Map();   // code -> {prio, pos}
    let m;
    while ((m = re.exec(html))) {
      if (/display\s*:\s*none/i.test(m[3] || '')) continue;   // 숨김 카드는 화면에 없다
      const code = m[1];
      const prio = PRIO[m[2] || ''];
      const prev = best.get(code);
      if (!prev || prio < prev.prio) best.set(code, { prio, pos: m.index });
    }
    if (best.size === 0) { _dashOrderCache = null; return null; }
    const sorted = [...best.entries()].sort((a, b) => a[1].pos - b[1].pos);
    _dashOrderCache = new Map(sorted.map(([code], i) => [code, i]));
    return _dashOrderCache;
  } catch (e) {
    log(`⚠️ 대시보드 순서 읽기 실패 (${e.message}) — 분석 JSON 순서 유지`);
    _dashOrderCache = null;
    return null;
  }
}

/** 대시보드 카드 순서로 재정렬. 카드가 없는 종목은 뒤로 밀되 원래 순서를 지킨다. */
function sortByDashboard(arr, label) {
  const order = loadDashboardOrder();
  if (!order || !Array.isArray(arr) || arr.length === 0) return arr;
  const rank = s => {
    const code = String(s && s.code || '').padStart(6, '0');
    return order.has(code) ? order.get(code) : Infinity;
  };
  const missing = arr.filter(s => rank(s) === Infinity).map(s => `${s.name}(${s.code})`);
  if (missing.length && label) {
    log(`⚠️ ${label}: 대시보드 카드 미발견 ${missing.length}종 (${missing.join(', ')}) — 섹션 말미 배치`);
  }
  return arr
    .map((s, i) => ({ s, i, r: rank(s) }))
    .sort((a, b) => (a.r - b.r) || (a.i - b.i))
    .map(x => x.s);
}

// 진입조건 키 → 라벨 (claude_dante_analyze.js normalizeEntryConditions 와 같은 키 · 체결강도는 필수일 때만)
const ENTRY_LABELS = [
  ['price', '가격'], ['bullish_candle', '양봉'], ['volume', '거래량'],
  ['foreign_net_buy', '외국인'], ['program_net_buy', '프로그램'], ['execution_strength', '체결강도'],
];

// 종목명 뒤 「(코스피 · 테마)」 — 시장 = 최신 KRX 캐시 파일, 테마 = 네이버 테마 사전(collect_theme_rotation.py 가 7일마다 갱신)
// 테마가 여럿이면 구성종목이 가장 적은(가장 구체적인) 것 1개 · 끝 괄호 설명은 뗀다
let _tags = null;
function stockTag(code) {
  if (!_tags) {
    _tags = { market: {}, theme: {} };
    const cache = path.join(__dirname, 'data', 'krx_ohlcv_cache');
    try {
      for (const [mk, label] of [['KOSPI', '코스피'], ['KOSDAQ', '코스닥']]) {
        const f = fs.readdirSync(cache).filter(n => n.endsWith(`_${mk}.json`)).sort().pop();
        if (f) for (const r of JSON.parse(fs.readFileSync(path.join(cache, f), 'utf-8')).rows) _tags.market[r.code] = label;
      }
    } catch (e) { log(`⚠️ 시장 구분 로드 실패: ${e.message}`); }
    try {
      const themes = JSON.parse(fs.readFileSync(path.join(__dirname, 'data', 'naver_theme_map.json'), 'utf-8')).themes;
      for (const [name, codes] of Object.entries(themes).sort((a, b) => b[1].length - a[1].length)) {
        for (const c of codes) _tags.theme[c] = name.replace(/\s*\([^)]*\)$/, '');
      }
    } catch (e) { log(`⚠️ 테마 사전 로드 실패: ${e.message}`); }
  }
  const parts = [_tags.market[code], _tags.theme[code]].filter(Boolean);
  return parts.length ? `  ${parts.join(' · ')}` : '';
}

// 종목명 뒤 「 D+N」 — 🤖자동 편입 후 경과 거래일 (auto_intake.js 가 08:30 마다 state 에 기록, 2026-10-19)
let _dplus = null;
function dplusTag(code) {
  if (!_dplus) {
    try { _dplus = JSON.parse(fs.readFileSync(path.join(__dirname, 'data', 'auto_intake_state.json'), 'utf-8')).managed || {}; }
    catch (e) { _dplus = {}; }
  }
  const d = _dplus[code] && _dplus[code].dplus;
  return d != null ? ` D+${d}` : '';
}

// 한 종목 = 2줄 「*종목명*  시장 · 테마」 + 「❌ 미충족 항목」 (2026-10-14 사용자 선택 A안 · 모바일 가독성)
// 미충족 = fail·unknown 전부 · 다 충족이면 ✅ 전부 충족
function entryGateLine(s) {
  const ec = s.entry_conditions;
  const head = `*${s.name}*${dplusTag(s.code)}${stockTag(s.code)}`;
  if (!ec) return `${head}\n   판정 없음`;
  const miss = ENTRY_LABELS
    .filter(([k]) => ec[k] && ec[k].status !== 'not_required' && ec[k].status !== 'pass')
    .map(([, label]) => label);
  return `${head}\n   ${miss.length ? `❌ ${miss.join(' · ')}` : '✅ 전부 충족'}`;
}

// ====== 스윙·밥그릇3 충족표 (2026-10-19 사용자 지시: 표로) ======
// 스윙 = auto_intake 관리 종목(대시보드 요약표와 같은 기대값 순) · 밥그릇3 = bowl3_quality 점수 순 5종
// 충족 = 이 분석의 entry_conditions
// 2026-10-23 사용자 지시: 매수 대상만 표시 (🟢 매수 · 🟡 매수가능, ⚪ 대기는 뺀다) — 규칙 SSOT = auto_intake.buyVerdict
//   가격 pass + 거래량·외국인·프로그램 중 2개 이상 · 양봉은 판정 제외, 양봉/음봉 참고 표시
const { buyVerdict } = require('./auto_intake.js');
function buyCell(e) {
  const v = buyVerdict(e);
  return v && v.level !== 'wait' ? v.text : null;
}
function gateRows(analysis) {
  const ec = Object.fromEntries((analysis.stocks || []).filter(s => s.entry_conditions && s.entry_conditions.required_total)
    .map(s => [s.code, s.entry_conditions]));
  const cell = code => buyCell(ec[code]);
  let st = { managed: {} };
  try { st = JSON.parse(fs.readFileSync(path.join(DATA_DIR, 'auto_intake_state.json'), 'utf-8')); } catch (e) { log(`⚠️ auto_intake_state 로드 실패: ${e.message}`); }
  const swing = Object.entries(st.managed || {}).sort((a, b) => (b[1].rr?.ev_pct ?? -99) - (a[1].rr?.ev_pct ?? -99))
    .map(([code, m]) => [m.name, cell(code), scenLink(code)]).filter(r => r[1]);
  // 밥그릇3 = 대시보드 탭과 같은 5종(build_bowl3_tab.js 가 쓰는 bowl3_watch.json) — 2026-10-23 수정:
  //   종전 bowl3_quality 점수 순 5종은 손절 퇴출·재진입 금지 종목이 섞이고 탭 종목이 빠졌다
  let bowl3 = [];
  try {
    bowl3 = (JSON.parse(fs.readFileSync(path.join(DATA_DIR, 'bowl3_watch.json'), 'utf-8')).stocks || [])
      .map(r => [r.name, cell(r.code), scenLink(r.code)]).filter(r => r[1]);
  } catch (e) { log(`⚠️ bowl3_watch 로드 실패: ${e.message}`); }
  return { swing, bowl3 };
}
// 시나리오 링크 (scenario_run.py [1s] 가 Drive 에 올린 그림 · data/scenario_state.json · 없으면 '-')
let _scen = null;
const scenLink = code => {
  if (!_scen) { try { _scen = JSON.parse(fs.readFileSync(path.join(DATA_DIR, 'scenario_state.json'), 'utf-8')); } catch { _scen = {}; } }
  return _scen[code]?.link ? { url: _scen[code].link, text: '시나리오' } : '-';
};
const GATE_HEAD = ['종목', '충족', '시나리오'];
// Block Kit table 블록 (메시지당 1개만 허용 → 스윙·밥그릇3 은 메시지를 나눈다)
const tableBlock = (rows, head = GATE_HEAD) => ({
  type: 'table',
  column_settings: head.map(h => ({ align: h === '종목' || h === '테마' || h === '재무' ? 'left' : 'center' })),
  rows: [head, ...rows].map(r => r.map(t => t && t.url
    ? { type: 'rich_text', elements: [{ type: 'rich_text_section', elements: [{ type: 'link', url: t.url, text: t.text }] }] }
    : { type: 'raw_text', text: String(t) })),
});
// table 블록이 거부될 때의 대체 — 고정폭 코드 블록 (한글·이모지는 2칸으로 맞춤)
const dispW = t => [...t].reduce((w, ch) => w + (/[\u1100-\u115F\u2E80-\uA4CF\uAC00-\uD7A3\uF900-\uFAFF\uFE30-\uFE4F\uFF00-\uFF60\u{1F300}-\u{1FAFF}]/u.test(ch) ? 2 : 1), 0);
const pad = (t, w) => t + ' '.repeat(Math.max(0, w - dispW(t)));
function textTable(rows, head = GATE_HEAD) {
  const all = [head, ...rows].map(r => r.map(t => t && t.url ? t.text : String(t)));
  const ws = head.map((_, c) => Math.max(...all.map(r => dispW(r[c]))));
  return '```\n' + all.map(r => r.map((t, c) => pad(t, ws[c])).join('  ')).join('\n') + '\n```';
}
function gateSection(title, rows, asTable, cols = GATE_HEAD) {
  const head = { type: 'section', text: { type: 'mrkdwn', text: `*${title}*` } };
  if (!rows.length) return [head, { type: 'section', text: { type: 'mrkdwn', text: '없음' } }];
  const links = rows.filter(r => r.some(t => t && t.url)).map(r => `<${r.find(t => t && t.url).url}|${r.find(t => typeof t === 'string')}>`);
  return asTable ? [head, tableBlock(rows, cols)] : [head, { type: 'section', text: { type: 'mrkdwn', text: textTable(rows, cols) + (links.length ? `\n시나리오: ${links.join(' · ')}` : '') } }];
}

// 충족 개수 내림차순 묶음 · 묶음 안은 대시보드 순서 유지
function buildEntryGateBlocks(active) {
  const groups = new Map();
  for (const s of active) {
    const ec = s.entry_conditions;
    const key = ec ? `${ec.required_pass_count}/${ec.required_total}` : '-';
    if (!groups.has(key)) groups.set(key, { n: ec ? ec.required_pass_count : -1, rows: [] });
    groups.get(key).rows.push(entryGateLine(s));
  }
  // 묶음 제목 = header 블록(큰 글씨) + 색 이모지 (2026-10-14 사용자 지시: 「충족 N/M」 눈에 띄게)
  const dot = key => {
    if (key === '-') return '⚪';
    const [n, t] = key.split('/').map(Number);
    return n === t ? '🟢' : n * 2 >= t ? '🟡' : '🔴';
  };
  const blocks = [{
    type: 'section',
    text: { type: 'mrkdwn', text: `*✅ 활성 ${active.length}종 · 진입조건 충족*${groups.size ? '' : '\n\n없음'}` },
  }];
  for (const [key, g] of [...groups.entries()].sort((a, b) => b[1].n - a[1].n)) {
    blocks.push(
      { type: 'header', text: { type: 'plain_text', text: `${dot(key)} ${key === '-' ? '판정 없음' : `충족 ${key}`}` } },
      { type: 'section', text: { type: 'mrkdwn', text: g.rows.join('\n\n') } },
    );
  }
  return blocks;
}

let TABLE_MODE = true; // false = 코드 블록 대체 (table 블록 거부 시 main 이 전환)
function buildBlocks(analysis) {
  const stocks = analysis.stocks || [];
  // 섹션 내부 순서 = 대시보드 카드 순서 (2026-08-26 사용자 지시)
  const active = sortByDashboard(stocks.filter(s => s.category === 'active'), '활성');
  // 🌟 추천 = decision에 '활성추천'(매수타점) 라벨이 뜬 종목 (2026-06-04 사용자 확정 구조)
  const isRecommend = s => (s.decision || '').includes('활성추천');
  // 추천종목(스캐너 영속 추천 풀 출신) 식별: recommend_sourced 플래그 또는 recommendations.top3 코드
  const recsPool = ((analysis.recommendations && analysis.recommendations.top3) || []).map(r => r.code);
  const isRecommendSourced = s => s.recommend_sourced === true || recsPool.includes(s.code);
  const recommend = sortByDashboard(stocks.filter(s => s.category === 'active' && isRecommend(s)), '추천');
  const summary = analysis.summary || {};
  const timeStr = analysis.time ? `${analysis.time.slice(0,2)}:${analysis.time.slice(2)}` : '';
  // 분석 단계 라벨 (2026-06-19): DANTE_PHASE 환경변수 pre/regular → 🌅 프리분석 / 📊 정규분석
  const phaseRaw = process.env.DANTE_PHASE || '';
  const phaseLabel = phaseRaw === 'pre' ? '🌅 프리분석' : phaseRaw === 'regular' ? '📊 정규분석' : '';

  // 종목 카드 5블록 (종목명 / 4지 / 추세 / 시그널 / 추천전략)
  // 2026-05-26: 시그널 + 추천전략 행동 통합 구조로 재설계 (사용자 확정)
  // 가격·평가익 라인 제거 (대시보드에서 확인) → 의사결정 액션만 집중
  const renderLine = s => {
    const dec = s.decision || '?';
    const position = s.position && s.position.status === 'holding'
      ? `\n📦 포지션: 보유${s.position.quantity != null ? ` ${s.position.quantity}주` : ''}${s.position.average_price != null ? ` · 평단 ${Number(s.position.average_price).toLocaleString()}원` : ''}`
      : '';
    // §4.9 Vision 추세 판정
    const trend = s.trend_judgment
                || (s.vision_trend && s.vision_recommendation ? `${s.vision_trend} → ${s.vision_recommendation}` : null)
                || '추세 판정 대기 (Vision 파이프라인 구축 후 자동 갱신)';
    // 🔔 시그널 (4지 ↔ 추세 일치/불일치 진단)
    const signal = s.signal || '시그널 분석 대기';
    // 🛡️ 추천전략 행동 (시그널 기반 구체 권장)
    const auto_action = s.auto_conservative_action || s.action_trigger || '보수 행동 분석 대기';
    // 💰 투자자공매(수급) — 외국인/프로그램/공매도 판별 (2026-06-19 추가, full 회차만 존재)
    const supply = s.supply_demand && !/데이터\s*없음/.test(s.supply_demand) ? `\n💰 수급: ${s.supply_demand}` : '';
    // 📋 우량주 심사(재무) — screen_quality_value.py 산출, 있을 때만 표시 (유저판단 바로 밑)
    const quality = s.quality_grade ? `\n📋 우량주 심사: ${s.quality_grade}` : '';
    return `*${s.name}*\n🟡 유저판단: ${dec}${position}${quality}\n📈 추세: ${trend}${supply}\n🤖 AI판단: ${signal}\n🛡️ 추천전략: ${auto_action}`;
  };

  const blocks = [
    {
      type: 'header',
      text: { type: 'plain_text', text: `📊 JCK Stock Report ${analysis.date || ''} ${timeStr}` },
    },
    {
      type: 'context',
      elements: [
        {
          type: 'mrkdwn',
          text: `${phaseLabel ? phaseLabel + ' · ' : ''}🤖 *AI 자동 판정 모드 (Claude Code)* — 유저판단은 사용자 직접 검토 후 최종 확정 필요`,
        },
      ],
    },
    {
      type: 'section',
      fields: [
        { type: 'mrkdwn', text: `*🚦 시장 충격도*\n${summary.market_urgency || '-'}` },
        { type: 'mrkdwn', text: `*⚡ 활성 평가익 합산*\n${summary.active_total_eval_gain || '-'}` },
      ],
    },
    { type: 'divider' },
  ];

  // 활성 = 🎯 스윙 충족표 (2026-10-19 사용자 지시: 충족 개수 묶음 → 표 · 밥그릇3 표는 두 번째 메시지)
  // 종전 묶음: buildEntryGateBlocks (backup/slack_send.js.bak_table_20261019)
  blocks.push(...gateSection(`🎯 손익비 매수 (${timeStr} 분석 기준)`, gateRows(analysis).swing, TABLE_MODE));

  // 🌟 추천 (활성추천 · 매수타점) — 활성 다음에 배치
  if (recommend.length > 0) {
    blocks.push({ type: 'divider' });
    blocks.push({
      type: 'section',
      text: { type: 'mrkdwn', text: `*🌟 추천 ${recommend.length}종 (활성추천 · 매수타점)*` },
    });
    for (let i = 0; i < recommend.length; i += 2) {
      const pair = recommend.slice(i, i + 2);
      blocks.push({
        type: 'section',
        fields: pair.map(s => ({ type: 'mrkdwn', text: renderLine(s) })),
      });
    }
  }


  // 🍚 밥그릇3 매수 — 2026-10-23 사용자 지시: 🚨 위험 종목 섹션 삭제, 그 자리에 밥그릇3 (두 번째 메시지 폐지)
  //   table 블록은 메시지당 1개라 손익비 표와 같은 메시지에선 줄 목록으로 쓴다
  const b3 = gateRows(analysis).bowl3;
  blocks.push({ type: 'divider' });
  blocks.push({
    type: 'section',
    text: { type: 'mrkdwn', text: `*🍚 밥그릇3 매수*\n${b3.length ? b3.map(([name, v, sl]) => `• *${name}*  ${v}${sl.url ? `  <${sl.url}|시나리오>` : ''}`).join('\n') : '없음'}` },
  });

  // 대시보드 링크 버튼
  blocks.push({ type: 'divider' });
  blocks.push({
    type: 'actions',
    elements: [
      {
        type: 'button',
        text: { type: 'plain_text', text: '📊 분석 대시보드 열기' },
        url: DASHBOARD_URL,
        style: 'primary',
      },
    ],
  });

  // 푸터
  blocks.push({
    type: 'context',
    elements: [
      {
        type: 'mrkdwn',
        text: `모델 ${analysis.model || '-'} · 학습용 참고 자료 (투자 권유 아님)`,
      },
    ],
  });

  return blocks;
}

// ====== 아침(08:30) 회차 = 노출 종목 1장 (종목명·점수만) ======
// 2026-10-01 사용자 지시: 눌림·돌파·밥그릇3 카드 3장 → 1장으로 축약, 테마 추가. 상세는 대시보드.
// 종전 상세 카드: backup/slack_send.js.bak_compact_card_20261001
// 목록 수집은 exposure.js (auto_intake.js 와 공용 · 2026-10-12 분리)
const { collectExposure: collectExposureRows, loadDataJson } = require('./exposure.js');
// 👔 임원매수 (2026-10-16) — screen_insider_buys.py 산출. 표시 전용이라 exposure.js(자동 편입 공용)에 넣지 않는다.
const insiderLines = analysis => {
  const d = loadDataJson('insider_buys', analysis.date);
  const lab = x => x.theme ? `${x.name}(${x.theme})` : x.name;
  return [['👔 임원매수 코스피', d && d.kospi.map(lab)], ['👔 임원매수 코스닥', d && d.kosdaq.map(lab)]];
};
const collectExposure = analysis => [
  ...collectExposureRows(analysis).map(s => [s.title, s.rows && s.rows.map(r => r.label)]),
  ...insiderLines(analysis),
];

const exposureLine = ([title, items]) =>
  `*${title}* ${items == null ? '— (데이터 없음)' : items.length ? items.join(' · ') : '없음'}`;

// 08:30 노출 카드 — 🎯 손익비는 표(2026-10-19 사용자 지시), 나머지 줄은 그대로
const RR_HEAD = ['순위', '종목', '테마', '승률', '재무', '시나리오'];
function rrRows(analysis) {
  const rr = loadDataJson('rr_breakout', analysis.date);
  if (!rr) return null;
  return rr.picks.map((p, i) => [i + 1, p.name, (p.themes || [])[0] || '-', `${Math.round(p.win * 100)}%`,
    p.fin_ok === true ? '✅ 3년 성장' : p.fin_ok === false ? `❌ ${p.fin}` : (p.fin || '-'), scenLink(p.code)]);
}
// 08:30 🍚 밥그릇3 표 — 점수 순 5종 (대시보드 탭과 같은 순서) · 두 번째 메시지 (table 블록은 메시지당 1개)
const B3_HEAD = ['순위', '종목', '테마', '점수', '시나리오'];
function bowl3Rows(analysis) {
  const b3 = loadDataJson('bowl3_quality', analysis.date);
  if (!b3) return null;
  stockTag('000000'); // 테마 사전 로드
  return (b3.passed || []).slice().sort((a, b) => (b.score ?? 0) - (a.score ?? 0)).slice(0, 5)
    .map((r, i) => [i + 1, r.name, _tags.theme[r.code] || '-', r.score ?? '-', scenLink(r.code)]);
}
function buildRecommendBowl3Blocks(analysis) {
  const rows = bowl3Rows(analysis);
  return rows == null ? [{ type: 'section', text: { type: 'mrkdwn', text: '*🍚 밥그릇3* — (데이터 없음)' } }] : gateSection('🍚 밥그릇3', rows, TABLE_MODE, B3_HEAD);
}
function buildRecommendBlocks(analysis) {
  const rows = rrRows(analysis);
  const rest = collectExposure(analysis).filter(([title]) => title !== '🎯 손익비' && title !== '🍚 밥그릇3');
  return [
    { type: 'section', text: { type: 'mrkdwn', text: `*📋 노출 종목 ${analysis.date || ''}*` } },
    ...(rows == null ? [{ type: 'section', text: { type: 'mrkdwn', text: '*🎯 손익비* — (데이터 없음)' } }] : gateSection('🎯 손익비', rows, TABLE_MODE, RR_HEAD)),
    { type: 'section', text: { type: 'mrkdwn', text: `${rest.map(exposureLine).join('\n')}\n<${DASHBOARD_URL}|📊 대시보드>` } },
  ];
}

function buildRecommendFallback(analysis) {
  return [`📋 노출 종목 ${analysis.date || ''}`,
    ...collectExposure(analysis).map(exposureLine).map(s => s.replace(/\*/g, ''))].join('\n');
}

function buildFallbackText(analysis) {
  const stocks = sortByDashboard(analysis.stocks || [], null);
  const summary = analysis.summary || {};
  const timeStr = analysis.time ? `${analysis.time.slice(0,2)}:${analysis.time.slice(2)}` : '';
  return [
    `📊 JCK Stock Report ${analysis.date} ${timeStr}`,
    `시장 충격: ${summary.market_urgency || '-'}`,
    `평가익 합산: ${summary.active_total_eval_gain || '-'}`,
    ...stocks.map(s => `${s.name} ${s.decision} ${s.current_price}원 (${s.change_pct}%)`),
    `대시보드: ${DASHBOARD_URL}`,
  ].join('\n');
}

function postToSlack(payload) {
  return new Promise((resolve, reject) => {
    const url = new URL(WEBHOOK_URL);
    const body = JSON.stringify(payload);
    const req = https.request({
      hostname: url.hostname,
      path: url.pathname,
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Content-Length': Buffer.byteLength(body),
      },
    }, res => {
      let data = '';
      res.on('data', c => data += c);
      res.on('end', () => {
        if (res.statusCode === 200 && data === 'ok') {
          resolve({ ok: true });
        } else {
          reject(new Error(`Slack ${res.statusCode}: ${data}`));
        }
      });
    });
    req.on('error', reject);
    req.write(body);
    req.end();
  });
}

async function main() {
  const jsonPath = findLatestAnalysisJson();
  log(`분석 JSON 로드: ${jsonPath}`);
  const analysis = JSON.parse(fs.readFileSync(jsonPath, 'utf-8'));

  const isRecommend = RECOMMEND_ONLY || analysis.mode === 'recommend-only';
  const blocks = isRecommend ? buildRecommendBlocks(analysis) : buildBlocks(analysis);
  const fallback = isRecommend ? buildRecommendFallback(analysis) : buildFallbackText(analysis);

  const payloads = [{ username: USERNAME, icon_emoji: ICON_EMOJI, text: fallback, blocks }];
  // full 회차 밥그릇3 는 첫 메시지 안 (🚨 위험 종목 자리, 2026-10-23) — 두 번째 메시지는 08:30 회차만
  if (isRecommend) payloads.push({ username: USERNAME, icon_emoji: ICON_EMOJI, text: '🍚 밥그릇3', blocks: buildRecommendBowl3Blocks(analysis) });

  if (DRY_RUN) {
    log(`--dry-run: Slack 발송 생략. 페이로드 미리보기 (${payloads.length}장)`);
    payloads.forEach((p, i) => {
      console.log(`─────────── 카드 ${i + 1}/${payloads.length} ───────────`);
      console.log(JSON.stringify(p, null, 2).slice(0, 2200));
    });
    console.log('───────────────────────────');
    log(`카드 ${payloads.length}장 / blocks ${payloads.map(p => p.blocks.length).join('+')}개`);
    return;
  }

  if (!WEBHOOK_URL) {
    log(`!!! SLACK_WEBHOOK_URL 미설정 (.env). 발송 불가.`);
    process.exit(1);
  }

  log(`Slack 발송: ${USERNAME} → Webhook (${payloads.length}장)`);
  let sent = 0;
  try {
    for (const p of payloads) { await postToSlack(p); sent++; }
  } catch (e) {
    // table 블록 미지원·거부 → 코드 블록 표로 다시 보낸다 (이미 나간 메시지는 그대로)
    if (!/invalid_blocks|table|400/i.test(e.message)) throw e;
    log(`⚠️ table 블록 거부(${e.message}) — 코드 블록 표로 재발송`);
    TABLE_MODE = false;
    const retry = isRecommend
      ? [{ username: USERNAME, icon_emoji: ICON_EMOJI, text: fallback, blocks: buildRecommendBlocks(analysis) },
         { username: USERNAME, icon_emoji: ICON_EMOJI, text: '🍚 밥그릇3', blocks: buildRecommendBowl3Blocks(analysis) }]
      : [{ username: USERNAME, icon_emoji: ICON_EMOJI, text: fallback, blocks: buildBlocks(analysis) }];
    for (const p of retry.slice(sent)) await postToSlack(p); // 이미 나간 메시지는 다시 보내지 않음
  }
  log(`✅ 발송 완료 (${payloads.length}장)`);
}

if (require.main === module) {
  main().catch(e => {
    console.error('치명적 오류:', e.message);
    process.exit(1);
  });
}

module.exports = { buildBlocks, buildFallbackText };
