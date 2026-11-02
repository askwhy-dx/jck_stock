/**
 * claude_dante_analyze.js — Claude Code CLI 호출로 9종 차트를 분석하여
 * 4지 판정 + 핵심 진단 + 액션 트리거 JSON을 산출하고 분석_대시보드.html을 패치.
 *
 * Anthropic API 사용 안함. 로컬 Claude Code CLI('claude -p ...') 호출:
 *   - 사용자 Claude Code 구독으로 동작 (별도 API 키 불필요)
 *   - --allowedTools "Read,Write" 로 이미지 Read + JSON Write 권한 부여
 *   - claude_enhance.js 패턴 정합 ([SOP] AI 리포트2 §6단계와 동일 호출 방식)
 *
 * 입력:
 *   - stock/images/{종목명}_{YYYY-MM-DD}.png (toss_capture.py 산출)
 *   - stock/data/prices_{YYYY-MM-DD}.json (toss_capture.py 산출 — 현재가/등락원/등락률)
 *   - stock/stocks.json (활성+후보 종목 라인업)
 *   - stock/AI단테.md (마스터 §6 평단 SSOT)
 *
 * 출력:
 *   - stock/data/dante_analysis_{YYYY-MM-DD}_{HHMM}.json
 *   - stock/분석_대시보드.html (4지 판정 셀 자동 패치)
 *
 * 사용:
 *   node claude_dante_analyze.js                # 오늘 캡처본 분석
 *   node claude_dante_analyze.js --dry-run      # claude CLI 호출 없이 프롬프트만 점검
 *   node claude_dante_analyze.js --normalize-latest  # 최신 JSON 조건을 카드 SSOT로 재검증
 *   node claude_dante_analyze.js --date=2026-05-19  # 특정 날짜 캡처본
 *
 * §4.6.13 4지 선다 룰 일시 완화: 사용자 응답 필수 룰은 자동화 모드에서
 * "🤖 AI 자동 판정 (검토 필요)" 표기로 명시되어 메일 본문에 강조됨.
 */
const fs = require('fs');
const path = require('path');
const { execSync, spawnSync } = require('child_process');

const argv = process.argv.slice(2);
const DRY_RUN = argv.includes('--dry-run');
const RECOMMEND_ONLY = argv.includes('--recommend-only');
const PRICES_ONLY = argv.includes('--prices-only');  // 캡처 시세만 카드 배너에 주입 (Claude 호출 없음)
const NORMALIZE_LATEST = argv.includes('--normalize-latest');
const REFRESH_ENTRY_SUMMARY = argv.includes('--refresh-entry-summary');
const DATE_ARG = (argv.find(a => a.startsWith('--date=')) || '').slice('--date='.length);

const KST_NOW = new Date(Date.now() + 9 * 60 * 60 * 1000);
const TODAY = DATE_ARG || KST_NOW.toISOString().slice(0, 10);
const HHMM = KST_NOW.toISOString().slice(11, 16).replace(':', '');

const ROOT = __dirname;
const IMAGES_DIR = path.join(ROOT, 'images');
const DATA_DIR = path.join(ROOT, 'data');
const TEMP_DIR = path.join(DATA_DIR, 'temp');
const HTML_PATH = path.join(ROOT, '분석_대시보드.html');
const AI_DANTE_PATH = path.join(ROOT, 'AI단테.md');
const PRICES_PATH = path.join(DATA_DIR, `prices_${TODAY}.json`);
const API_CHART_DIR = path.join(DATA_DIR, 'api_chart');
const API_SNAPSHOT_PATH = path.join(DATA_DIR, `api_snapshot_${TODAY}.json`);
const OUT_JSON = path.join(DATA_DIR, `dante_analysis_${TODAY}_${HHMM}.json`);
const HOLDINGS_FLOW_PATH = path.join(DATA_DIR, `holdings_flow_${TODAY}.json`);
const THEME_FLOW_PATH = path.join(DATA_DIR, `theme_flow_${TODAY}.json`);
const SOP_FLOW_PATH = path.join(ROOT, 'sop_테마발굴_보조지표.md');

const MAX_RETRIES = 2;
const CLI_TIMEOUT_MS = 600000; // 10분
// 빈 MCP 설정: claude CLI가 커넥터(Gmail/Calendar/Drive/Slack 등)를 안 띄우게 강제.
// 인증대기 커넥터로 인한 claude -p hang 방아쇠를 원천 차단 (counsel_report와 동일 대응).
const EMPTY_MCP_PATH = path.join(ROOT, 'config', 'empty_mcp.json');

function log(msg) {
  const ts = new Date(Date.now() + 9 * 60 * 60 * 1000).toISOString().replace('T', ' ').slice(0, 19);
  console.log(`[${ts}] ${msg}`);
}

function readFileSafe(file) {
  try { return fs.readFileSync(file, 'utf-8'); } catch { return ''; }
}

/**
 * claude CLI를 동기 실행하되 hang을 확실히 끊는다.
 * - stdout/stderr를 파이프가 아닌 파일로 리다이렉트 → claude가 남긴 손자 프로세스가
 *   파이프를 물고 있어도 spawnSync가 블록되지 않음 (execSync 무한 대기 버그의 근본 원인).
 * - 타임아웃 시 taskkill /T /F 로 프로세스 트리를 강제 종료.
 * - --strict-mcp-config 로 커넥터를 아예 로딩하지 않음.
 * @returns {{ timedOut: boolean, stdout: string, stderr: string }}
 */
function runClaudeCli(prompt, label, allowedTools, maxTurns) {
  if (!fs.existsSync(TEMP_DIR)) fs.mkdirSync(TEMP_DIR, { recursive: true });
  const outLog = path.join(TEMP_DIR, `_cli_out_${label}.txt`);
  const errLog = path.join(TEMP_DIR, `_cli_err_${label}.txt`);
  let outFd, errFd;
  try {
    outFd = fs.openSync(outLog, 'w');
    errFd = fs.openSync(errLog, 'w');
    const res = spawnSync(
      `claude -p --allowedTools "${allowedTools}" --max-turns ${maxTurns} --strict-mcp-config --mcp-config "${EMPTY_MCP_PATH}"`,
      {
        cwd: ROOT,
        input: prompt,
        stdio: ['pipe', outFd, errFd],
        timeout: CLI_TIMEOUT_MS,
        killSignal: 'SIGKILL',
        windowsHide: true,
        shell: true,
      }
    );
    const timedOut = !!(res.error && res.error.code === 'ETIMEDOUT');
    if (timedOut && res.pid) {
      try { execSync(`taskkill /PID ${res.pid} /T /F`, { stdio: 'ignore' }); } catch {}
    }
    return { timedOut, stdout: readFileSafe(outLog), stderr: readFileSafe(errLog) };
  } finally {
    try { if (outFd !== undefined) fs.closeSync(outFd); } catch {}
    try { if (errFd !== undefined) fs.closeSync(errFd); } catch {}
  }
}

// 📋 재무 우량주 심사 — data/quality_screen/<code>.json 읽어 1줄 요약 (없으면 null = 슬랙 줄 생략)
function loadQualityScreen(code) {
  try {
    const p = path.join(ROOT, 'data', 'quality_screen', `${code}.json`);
    if (!fs.existsSync(p)) return null;
    const c = JSON.parse(fs.readFileSync(p, 'utf-8'));
    if (c.error || !c.checks) return null;
    const emoji = { A: '🅰️', B: '🅱️', C: '🅲', D: '🅳' }[c.grade] || '';
    const m = k => (c.checks[k] ? '✅' : '❌');
    const detail = `매출${m('1_매출증가')}·순익${m('2_순익50억')}·부채자본${m('3_부채자본')}·부채자산${m('4_부채자산')}·자산${m('5_자산증가')}·위치${m('6_가격위치')}`;
    return `${emoji} ${c.grade}등급 · ${c.passed}/6 · ${detail}`;
  } catch (e) {
    return null;
  }
}

function loadStocksMeta() {
  const stocksJson = JSON.parse(fs.readFileSync(path.join(ROOT, 'stocks.json'), 'utf-8'));
  const aiDante = fs.readFileSync(AI_DANTE_PATH, 'utf-8');
  const section6Match = aiDante.match(/## 6\. 현재 종목 현황[\s\S]*?(?=\n## 6\.X|\n## 7\.|\n---\n\n## )/);
  const section6Text = section6Match ? section6Match[0] : '';
  // 🍚 밥그릇3 관찰 종목(data/bowl3_watch.json) — 분석·충족 판정만 하고 category 'bowl3' (라인업·카드 없음, 2026-10-19)
  let watch = [];
  try { watch = JSON.parse(fs.readFileSync(path.join(DATA_DIR, 'bowl3_watch.json'), 'utf-8')).stocks || []; } catch { /* 없으면 생략 */ }
  const have = new Set(stocksJson.stocks.map(s => s.code));
  const bowl3 = watch.filter(w => !have.has(w.code)).map(w => ({ ...w, tag: 'bowl3' }));
  return {
    stocks: [...stocksJson.stocks, ...bowl3],
    section6_text: section6Text,
    positions: parsePositionMap(section6Text, stocksJson.stocks),
  };
}

/**
 * AI단테.md §6의 라인업 SSOT에서 실제 포지션 상태를 기계적으로 읽는다.
 * 분석 모델의 진입 판정과 실제 보유 여부를 분리해 Slack에 체결 상태가 누락되지 않게 한다.
 */
function parsePositionMap(section6Text, stocks = []) {
  const map = new Map();
  const activeLine = String(section6Text || '').split(/\r?\n/)
    .find(line => /^>\s*-\s*\*\*활성\*\*\s*:/.test(line));
  if (!activeLine) return map;

  const markers = stocks.map(s => ({
    stock: s,
    marker: `(${s.code})`,
    index: activeLine.indexOf(`(${s.code})`),
  })).filter(x => x.index >= 0).sort((a, b) => a.index - b.index);

  for (let i = 0; i < markers.length; i += 1) {
    const { stock, marker, index } = markers[i];
    const end = i + 1 < markers.length ? markers[i + 1].index : activeLine.length;
    const segment = activeLine.slice(index + marker.length, end);
    const labelMatch = segment.match(/^\s*\*\*([^*]+)\*\*/);
    const label = labelMatch ? labelMatch[1].trim() : 'UNKNOWN';
    const status = /미체결|보유\s*0주|종결|청산/.test(label)
      ? 'unfilled'
      : /체결|보유|트레일링|잔량/.test(label) ? 'holding' : 'unknown';
    const fillMatch = segment.match(/(?:\d{2}-\d{2}\s+)?([\d.]+)주\s*@\s*([\d,]+)(?:원)?/);
    map.set(stock.code, {
      status,
      label,
      quantity: fillMatch ? Number(fillMatch[1]) : null,
      average_price: fillMatch ? Number(fillMatch[2].replace(/,/g, '')) : null,
      source: 'AI단테.md §6 라인업 SSOT',
    });
  }
  return map;
}

/** 보유 중인 종목은 추가매수 대기 여부와 무관하게 포지션 판정을 "보유 유지"로 고정한다. */
function normalizeDecisionForPosition(stock, position, logger = log) {
  if (!stock || !position) return stock;
  stock.position = { ...position };
  if (position.status === 'holding' && /매수\s*준비|보류\s*\(관망\)/.test(String(stock.decision || ''))) {
    const before = stock.decision;
    stock.decision = '🟡 보유 유지';
    logger(`포지션 판정 교정(§6 SSOT): ${stock.name}(${stock.code}) "${before}" → "${stock.decision}"`);
  }
  return stock;
}

function loadPrices() {
  if (!fs.existsSync(PRICES_PATH)) {
    log(`⚠️  가격 JSON 없음: ${PRICES_PATH} (toss_capture.py 선실행 필요). 빈 가격으로 진행.`);
    return {};
  }
  const data = JSON.parse(fs.readFileSync(PRICES_PATH, 'utf-8'));
  const stocks = data.stocks || {};
  // 방어: 토스가 등락률을 부호 없이 표시해 change_pct에 부호가 빠진 레코드는
  // change_won 부호로 보정 (음봉을 '양봉'으로 오판 + 라인업/배너 색상 오류 방지)
  for (const v of Object.values(stocks)) {
    const cw = v && v.change_won, cp = v && v.change_pct;
    if (cw && cp && !/^[+-]/.test(String(cp)) && String(cw).startsWith('-')) {
      v.change_pct = '-' + String(cp);
    }
  }
  return stocks;
}

function loadFlowData() {
  const holdings = fs.existsSync(HOLDINGS_FLOW_PATH)
    ? JSON.parse(fs.readFileSync(HOLDINGS_FLOW_PATH, 'utf-8'))
    : null;
  const theme = fs.existsSync(THEME_FLOW_PATH)
    ? JSON.parse(fs.readFileSync(THEME_FLOW_PATH, 'utf-8'))
    : null;
  return { holdings, theme };
}

function loadChartImagePaths() {
  if (!fs.existsSync(IMAGES_DIR)) return {};
  const map = {};
  for (const f of fs.readdirSync(IMAGES_DIR)) {
    if (f.includes('_투자자공매_')) continue;  // 수급 이미지는 별도 로더(loadFlowImagePaths)에서 처리
    const m = f.match(/^(.+)_(\d{4}-\d{2}-\d{2})\.png$/);
    if (!m || m[2] !== TODAY) continue;
    map[m[1]] = path.join(IMAGES_DIR, f);
  }
  // 2026-10-21: 15:30·19:00 은 토스 Open API 수치 파일(toss_api_snapshot.py)이 PNG 를 대신한다
  if (fs.existsSync(API_CHART_DIR)) for (const f of fs.readdirSync(API_CHART_DIR)) {
    const m = f.match(/^(.+)_(\d{4}-\d{2}-\d{2})\.txt$/);
    if (!m || m[2] !== TODAY || m[1].endsWith('_수급')) continue;   // API 파일이 있으면 PNG(16:30 매집 캡처 등)보다 우선
    map[m[1]] = path.join(API_CHART_DIR, f);
  }
  return map;
}

// 투자자공매(수급) 이미지 로더 — {종목}_투자자공매_{투자자|프로그램|공매도}_{날짜}.png 를 종목별로 그룹화
function loadFlowImagePaths() {
  if (!fs.existsSync(IMAGES_DIR)) return {};
  const map = {};  // { 종목명: { 투자자: path, 프로그램: path, 공매도: path } }
  for (const f of fs.readdirSync(IMAGES_DIR)) {
    const m = f.match(/^(.+)_투자자공매_(투자자|프로그램|공매도)_(\d{4}-\d{2}-\d{2})\.png$/);
    if (!m || m[3] !== TODAY) continue;
    (map[m[1]] ||= {})[m[2]] = path.join(IMAGES_DIR, f);
  }
  if (fs.existsSync(API_CHART_DIR)) for (const f of fs.readdirSync(API_CHART_DIR)) {
    const m = f.match(/^(.+)_수급_(\d{4}-\d{2}-\d{2})\.txt$/);
    if (m && m[2] === TODAY) map[m[1]] = { 투자자: path.join(API_CHART_DIR, f) };
  }
  return map;
}

// 토스 Open API 당일 수치 → entry_conditions 기계 주입 (모델이 읽은 값보다 우선). 당일 행이 없으면 unknown
function applyApiSnapshot(stock, snap) {
  if (!snap) return;
  const ec = (stock.entry_conditions && typeof stock.entry_conditions === 'object') ? stock.entry_conditions : (stock.entry_conditions = {});
  const set = (k, v) => { ec[k] = { ...(ec[k] || {}), value_shares: v, source: 'toss_open_api', reason: v === null ? 'API 당일 행 없음' : `API 당일 ${v.toLocaleString()}주` }; };
  set('foreign_net_buy', snap.foreign_net_today);
  set('program_net_buy', snap.program_net_today);
  if (snap.bar_date === TODAY) {
    ec.bullish_candle = { ...(ec.bullish_candle || {}), status: snap.close > snap.open ? 'pass' : 'fail', open_price: snap.open, reference_price: snap.close, reason: 'API 당일 봉' };
    ec.volume = { ...(ec.volume || {}), actual: snap.volume };
  }
}

const REQUIRED_ENTRY_CONDITIONS = [
  'price',
  'bullish_candle',
  'volume',
  'foreign_net_buy',
  'program_net_buy',
];

function normalizeConditionStatus(value) {
  const raw = (value && typeof value === 'object') ? value.status : value;
  const s = String(raw || '').trim().toLowerCase();
  if (s === 'pass' || s === 'true' || s === '충족') return 'pass';
  if (s === 'fail' || s === 'false' || s === '미충족') return 'fail';
  if (s === 'not_required' || s === 'not-required' || s === '해당없음') return 'not_required';
  return 'unknown';
}

function parseSignedNumber(value) {
  if (typeof value === 'number') return Number.isFinite(value) ? value : null;
  if (value === undefined || value === null) return null;
  const m = String(value).replace(/,/g, '').match(/[+-]?\d+(?:\.\d+)?/);
  if (!m) return null;
  const n = Number(m[0]);
  return Number.isFinite(n) ? n : null;
}

/**
 * 모델 출력의 진입조건을 기계적으로 정규화한다.
 * 외국인·프로그램은 당일 순매수 수량이 양수여야 pass다. 숫자가 없으면 UNKNOWN이며,
 * 사용자가 직접 확인했다고 source=user_confirmed로 기록한 경우에만 명시 status를 허용한다.
 */
function normalizeEntryConditions(stock, evaluatedAt, marketSnapshot = {}, entryRule = {}) {
  const raw = stock && stock.entry_conditions && typeof stock.entry_conditions === 'object'
    ? stock.entry_conditions : {};
  const out = {
    source: String(raw.source || 'schedule_flow_images'),
    evaluated_at: String(raw.evaluated_at || evaluatedAt),
  };

  for (const key of REQUIRED_ENTRY_CONDITIONS) {
    const original = raw[key] && typeof raw[key] === 'object' ? raw[key] : {};
    const item = { ...original, status: normalizeConditionStatus(raw[key]) };
    if (key === 'foreign_net_buy' || key === 'program_net_buy') {
      const value = parseSignedNumber(item.value_shares);
      const source = String(item.source || out.source);
      if (value !== null) item.status = value > 0 ? 'pass' : 'fail';
      else if (!/user_confirmed/i.test(source)) item.status = 'unknown';
      item.value_shares = value;
    }
    out[key] = item;
  }

  const volumeActual = parseSignedNumber(out.volume.actual);
  if (entryRule.volumeMinimum !== null && entryRule.volumeMinimum !== undefined) {
    out.volume.minimum = entryRule.volumeMinimum;
    out.volume.maximum = entryRule.volumeMaximum;
    out.volume.source = 'dashboard_next_entry_rule';
    if (volumeActual === null) out.volume.status = 'unknown';
    else {
      const aboveMin = volumeActual >= entryRule.volumeMinimum;
      const belowMax = entryRule.volumeMaximum === null || entryRule.volumeMaximum === undefined
        ? true : volumeActual <= entryRule.volumeMaximum;
      out.volume.status = aboveMin && belowMax ? 'pass' : 'fail';
    }
  }

  // 2026-10-21 사용자 지시: 15:30·19:00 은 장 마감 뒤라 체결강도는 판정하지 않는다(토스 Open API 미제공) — 수급·거래량으로 본다
  out.execution_strength = { status: 'not_required', value_pct: null, threshold_pct: null };

  const required = [...REQUIRED_ENTRY_CONDITIONS];
  if (out.execution_strength.status !== 'not_required') required.push('execution_strength');
  out.required_pass_count = required.filter(k => out[k].status === 'pass').length;
  out.required_total = required.length;
  out.failed = required.filter(k => out[k].status === 'fail');
  out.unknown = required.filter(k => out[k].status === 'unknown');
  out.all_required_met = out.required_pass_count === out.required_total;
  stock.entry_conditions = out;

  const unfilled = /미체결|보유\s*0주/.test(String(stock.eval_gain || ''));
  if (unfilled && /매수\s*준비/.test(String(stock.decision || '')) && !out.all_required_met) {
    stock.decision = '⏸ 보류 (관망)';
    const missing = [...out.failed, ...out.unknown].join(', ') || 'UNKNOWN';
    stock.auto_conservative_action = `진입조건 게이트 미충족(${missing}) - ${stock.auto_conservative_action || '조건 재확인'}`;
  }
  return out;
}

function plainText(html) {
  return String(html || '')
    .replace(/<[^>]+>/g, ' ')
    .replace(/&gt;/g, '>')
    .replace(/&lt;/g, '<')
    .replace(/&amp;/g, '&')
    .replace(/\s+/g, ' ')
    .trim();
}

/** 다음 대기 차수의 카드 조건을 SSOT로 읽어 모델의 임의 거래량 재산출을 막는다. */
function loadEntryRuleMap() {
  const map = new Map();
  if (!fs.existsSync(HTML_PATH)) return map;
  const html = fs.readFileSync(HTML_PATH, 'utf8');
  const codes = [...html.matchAll(/id="card-(\d{6})-active"/g)].map(m => m[1]);
  for (const code of codes) {
    const range = resolveCardRange(html, code);
    if (!range) continue;
    const card = html.slice(range.start, range.end);
    const table = (card.match(/<table class="portfolio-table">[\s\S]*?<\/table>/) || [])[0] || '';
    const rows = [...table.matchAll(/<tr>([\s\S]*?)<\/tr>/g)].map(m => m[1]);
    const waiting = rows.find(row => /data-label="(?:차수|스윙 분할 계획)"/.test(row) && /대기/.test(plainText(row)));
    if (!waiting) continue;
    const text = plainText(waiting);
    const volumeRange = text.match(/거래량[:\s]*([\d,]+)\s*[~～]\s*([\d,]+)\s*주/);
    const volumeMinimum = text.match(/거래량[:\s]*([\d,]+)\s*주\s*이상/);
    const strength = text.match(/체결강도[:\s]*([\d.]+)\s*%\s*이상/);
    map.set(code, {
      volumeMinimum: volumeRange ? parseSignedNumber(volumeRange[1])
        : (volumeMinimum ? parseSignedNumber(volumeMinimum[1]) : null),
      volumeMaximum: volumeRange ? parseSignedNumber(volumeRange[2]) : null,
      executionStrengthThreshold: strength ? parseSignedNumber(strength[1]) : null,
    });
  }
  return map;
}

// 1단계 교훈 회상: data/lessons.json (build_lessons.js 산출)을 판정 프롬프트용 블록으로 변환.
// 현재 풀이 작아(종결 8건) 전건 주입. 풀 확대 시(§4.9 CLIP) Top-5 검색으로 전환 예정.
function loadLessonsContext() {
  // 3단계 우선: 현재 종목별 유사 패턴 회상(recall_latest.json)이 있으면 사용
  const recallP = path.join(DATA_DIR, 'recall_latest.json');
  if (fs.existsSync(recallP)) {
    try {
      const r = JSON.parse(fs.readFileSync(recallP, 'utf-8'));
      const lines = (r.stocks || []).map(s => {
        if (s.note) return `- ${s.name}(${s.code}): ${s.note}`;
        const top = (s.matches || [])[0];
        const list = (s.matches || []).map(m => `${m.name}(${m.similarity}%·${m.result}·실현${m.realized_pct}%)`).join(' / ');
        return `- ${s.name}(${s.code}): [${s.confidence}] 유사도 ${s.avg_sim}% · 과거승률 ${s.win_rate}%\n    ${list}${top ? `\n    유사사례 교훈: ${top.key_lesson}` : ''}`;
      }).join('\n');
      if (lines) return `\n\n## 과거 유사 패턴 회상 (3단계 · 숫자지표 유사도 Top-${r.k} · ${r.as_of} 기준)\n> ${r.caveat}\n> 유사도 높은 사례 교훈을 diagnosis에 반영. '유사 선례 약함'은 참고만.\n${lines}`;
    } catch { /* 손상 시 폴백 */ }
  }
  // 폴백: 종결거래 전건 교훈 (recall 파일 없을 때)
  const p = path.join(DATA_DIR, 'lessons.json');
  if (!fs.existsSync(p)) return '';
  let data;
  try { data = JSON.parse(fs.readFileSync(p, 'utf-8')); } catch { return ''; }
  const trades = data.trades || [];
  if (!trades.length) return '';
  const lines = trades.map(t => {
    const tj = t.trajectory || {};
    const key = (t.lessons || []).find(l => /⚠️|📐|🔁/.test(l)) || (t.lessons || [])[0] || '';
    const timing = tj.exit_timing ? ` · ${tj.exit_timing}` : '';
    const eff = tj.capture_note ? ` · ${tj.capture_note}` : '';
    return `- ${t.name}(${t.code}) [${t.type}/${t.result}] 실현 ${t.realized_pct}% · 보유MFE ${tj.hold_mfe_pct ?? '—'}%${timing}${eff}\n    교훈: ${key}`;
  }).join('\n');
  return `\n\n## 과거 종결 거래 교훈 (회상 풀 ${trades.length}건 — 유사 상황 참고)\n` +
    `> 현재 종목과 같은 타입·유사 패턴의 사례를 찾아 진단에 반영할 것. 특히 '익절 효율'(보유 MFE 대비 실현 격차)·'매도 타이밍'·'1차 단독 체결 후 손절' 패턴 주의.\n${lines}`;
}

// 2단계 판정 원장: 분석 완료 시 각 종목 판정을 predictions.jsonl 에 append (status=pending).
// 20거래일 뒤 score_predictions.js 가 채점. 하루 다회차는 score 단계에서 19:00 1건으로 dedup.
function appendPredictions(analysis) {
  try {
    const LEDGER = path.join(DATA_DIR, 'predictions.jsonl');
    const existing = fs.existsSync(LEDGER)
      ? fs.readFileSync(LEDGER, 'utf-8').split('\n').filter(Boolean).map(l => { try { return JSON.parse(l); } catch { return null; } }).filter(Boolean)
      : [];
    const seen = new Set(existing.map(r => `${r.date_compact}_${r.time}_${r.code}`));
    const dc = (analysis.date || '').replace(/-/g, '');
    const rows = [];
    for (const s of analysis.stocks || []) {
      const pm = String(s.current_price || '').match(/[\d,]*\d(?:\.\d+)?/);  // 첫 숫자만
      const price = pm ? parseFloat(pm[0].replace(/,/g, '')) : NaN;
      if (!price) continue;
      if (seen.has(`${dc}_${analysis.time}_${s.code}`)) continue;
      rows.push({
        date: analysis.date, date_compact: dc, time: analysis.time,
        code: s.code, name: s.name, type: s.type || '스윙', category: s.category,
        decision: s.decision, entry_price: price, change_pct: s.change_pct,
        bar: (parseFloat(s.change_pct) >= 0) ? '양봉' : '음봉',
        vol_band: 'unknown', risk_level: s.risk_level, status: 'pending', sim: false,
      });
    }
    if (rows.length) {
      fs.appendFileSync(LEDGER, rows.map(r => JSON.stringify(r)).join('\n') + '\n', 'utf-8');
      log(`예측 원장 append: ${rows.length}건 → predictions.jsonl (status=pending)`);
    }
  } catch (e) { log(`예측 원장 append 실패(무시): ${e.message}`); }
}

function buildPrompt(images, prices, meta, flow, flowImages = {}) {
  const nActive = (meta.stocks || []).filter(s => s.tag === 'active').length;
  const nBowl3 = (meta.stocks || []).filter(s => s.tag === 'bowl3').length;
  const stockList = meta.stocks.map(s => {
    const p = prices[s.name];
    const position = meta.positions && meta.positions.get(s.code);
    const positionText = position && position.status === 'holding'
      ? ` · 포지션 보유${position.quantity != null ? ` ${position.quantity}주` : ''}${position.average_price != null ? ` @${position.average_price.toLocaleString()}` : ''}`
      : position && position.status === 'unfilled' ? ' · 포지션 미체결' : '';
    const plan = s.tag === 'bowl3' && s.p1
      ? ` · 🍚밥그릇3 관찰(라인업 아님 · 진입 기준 1차 ${s.p1.toLocaleString()} / 2차 ${s.p2.toLocaleString()} / 3차 ${s.p3.toLocaleString()} · 손절 ${s.stop.toLocaleString()} · 목표 ${s.target.toLocaleString()} · 거래량 ${s.vol[0].toLocaleString()}~${s.vol[1].toLocaleString()}주)`
      : '';
    return `- ${s.name} (${s.code}) [${s.tag}]${plan}` +
      (p ? ` 현재가 ${p.price}원${p.change_pct ? ' ('+p.change_pct+'%)' : ''}` +
        `${p.execution_strength_pct !== undefined && p.execution_strength_pct !== null ? ` · 체결강도 ${p.execution_strength_pct}%` : ''}` : '') +
      positionText;
  }).join('\n');

  const imageList = Object.entries(images).map(([name, p]) =>
    `- ${name}: ${p.replace(/\\/g, '/')}`
  ).join('\n');

  // 투자자공매(수급) 이미지 — 종목별 투자자별 매매동향/프로그램매매/공매도
  const flowImageEntries = Object.entries(flowImages);
  const flowImageCount = flowImageEntries.reduce((n, [, kinds]) => n + Object.keys(kinds).length, 0);
  const flowImageList = flowImageEntries.map(([name, kinds]) =>
    `- ${name}: ` + ['투자자', '프로그램', '공매도']
      .filter(k => kinds[k]).map(k => `[${k}] ${kinds[k].replace(/\\/g, '/')}`).join('  ')
  ).join('\n');
  const flowImageBlock = flowImageCount > 0 ? `

## 투자자공매(수급) 이미지 — Read tool로 종목별로 모두 읽을 것 (${flowImageCount}장)
> 각 종목의 [투자자]=일별 외국인/기관/개인 순매수, [프로그램]=프로그램 순매수, [공매도]=공매도 비율·수량 테이블.
${flowImageList}

### 수급 판별 룰 (투자자공매 → supply_demand 필드 산출)
- **🟢 매집**: 외국인(+프로그램) 순매수 지속/가속 + 개인 투매 흡수 + 공매도 비율 감소 → 하락이어도 바닥 다지기 신호 (전량 손절 신중)
- **🔴 이탈/분배**: 외국인 순매도 전환·지분율 하락 + 공매도 급증(>10%) + 프로그램 매도 → 약세 가속 (정리 우선)
- **⚪ 중립/혼조**: 방향성 불명확 또는 차익성(공매도 숏커버·패시브)
- ⚠️ "외국인 순매수"만으로 매수 근거 삼지 말 것 — 공매도·프로그램·지분율 추이를 교차 검증 (미래에셋·비에이치아이 사례)

### 체결임박 진입조건 게이트
- 외국인과 프로그램은 반드시 각 이미지(또는 수급 .txt)의 **당일/오늘 행 순매수 수량**을 읽는다. 수급 .txt 의 프로그램 순매수 = 차익 + 비차익. 종목 헤더의 \`외국인 순매수 N위\` 문구는 시장 순위 정보라 진입조건 판정에 사용하지 않는다. 이전 2~5일 순매수는 당일 값을 대체하지 못한다.
- 당일 외국인 순매수 수량 > 0이고 당일 프로그램 순매수 수량 > 0이어야 각각 pass다. 0·음수는 fail, 당일 숫자를 못 읽으면 unknown이다.
- 가격·양봉·거래량·외국인·프로그램 5개 필수조건 중 하나라도 fail/unknown이면 all_required_met=false다.
- 거래량 최소·최대값은 대시보드 다음 대기 차수 조건을 SSOT로 사용한다. 이미지에서 20일 평균을 다시 추정해 기준을 바꾸지 않는다.
- 체결강도는 판정하지 않는다(장 마감 뒤 회차 · execution_strength 는 항상 not_required).` : '';

  // 보조지표 컨텍스트 (19:00 회차에서 collect_theme_flow.py가 산출했을 때만)
  let flowContext = '';
  let flowOutputSchema = '';
  if (flow.holdings || flow.theme) {
    flowContext = `\n\n## 보조지표 — 외국인·기관 수급 (sop_테마발굴_보조지표.md 참조)\n`;
    if (flow.holdings) {
      flowContext += `\n### 보유종목 수급 현황 (잠정치)\n` +
        `\`\`\`json\n${JSON.stringify(flow.holdings, null, 2).slice(0, 3000)}\n\`\`\`\n`;
    }
    if (flow.theme) {
      flowContext += `\n### 신규 추천 후보 (1차 정량 필터 통과 종목)\n` +
        `\`\`\`json\n${JSON.stringify(flow.theme, null, 2).slice(0, 5000)}\n\`\`\`\n` +
        `\n위 candidates 배열에서 단테 정통 정성 판단(파동 위치·밥그릇 N구간·차트 형태) 적용 후 **상위 3개**를 ` +
        `\`recommendations.top3\`로 선정. 각 종목 진입 시나리오(1차a/1차b/2차/손절)를 §4.6.12 거래량 룰·§2.5.2 112일선 -1% 룰로 산출.\n`;
    }
    flowOutputSchema = `,
  "holdings_flow_summary": {
    "warning_count": 3,
    "favorable_count": 1,
    "headline": "활성·후보 수급 한 줄 요약 (이름·신호 포함)"
  },
  "recommendations": {
    "selected_sector": "전기전자",
    "sector_basis": "5일 외국인+기관 +XXX억 (전 업종 1위)",
    "top3": [
      {
        "code": "005930",
        "name": "삼성전자",
        "dante_score": 87,
        "dante_stars": "⭐⭐⭐⭐",
        "wave_position": "엘리엇 N파 추정",
        "scenario": {
          "entry_1a": "가격 + 트리거",
          "entry_1b": "가격 + 트리거",
          "entry_2": "가격 + 지지선",
          "stop_loss": "가격 (112일선 -1%)"
        },
        "diagnosis": "정량+정성 종합 한 줄",
        "review_required": true
      }
    ]
  }`;
  }

  return `당신은 주식단테 매매기법 정통 분석가입니다. 다음 차트 이미지와 투자자공매(수급) 이미지(또는 토스 Open API 수치 파일 .txt — 일봉 OHLCV·이동평균, 투자자·프로그램·공매도 표)를 Read tool로 모두 읽고 4지 판정을 결정하세요. 수급은 차트(추세)와 교차 검증해 supply_demand 필드로 산출하세요.

## 차트 이미지 (Read tool로 모두 분석할 것)
${imageList}${flowImageBlock}

## 종목 라인업 (stocks.json)
${stockList}

## AI단테.md §6 현재 종목 현황 (평단·시나리오 SSOT — 평단은 §6 체결 행 기준)
${meta.section6_text.slice(0, 5000)}${flowContext}${loadLessonsContext()}

## 판정 옵션 (활성 6지 — 2026-08-20 §4.4-D 신설로 4지 → 6지 확장 · 후보 분류 폐지)
> ⚠️ 본 판정은 **진단**이다. §4.6.13 사용자 질문 축도 같은 6지 라벨을 쓰지만 **집행 권한은 사용자 응답에만 있다** — 여기서 💰·🛡️를 냈다고 절반 익절·트레일링이 확정된 것이 아니다.
- 활성 종목: 🟢 매수 준비 / 🟡 보유 유지 / 💰 절반 익절 / 🛡️ 트레일링 전환 / ⏸ 보류 (관망) / 🚫 손절 검토
  - **💰 절반 익절**: **체결 종목이 목표 1차에 도달**(§4.4-D D1). 보유 50% 익절 후 잔량 트레일링 전환. **미체결 종목에는 절대 쓰지 않는다**(팔 수량이 없다).
  - **🛡️ 트레일링 전환**: 이미 절반 익절했거나 갭상 +2% 단독 체결 등으로 **분할매수가 종료**된 상태(§4.4-D D2). 잔량은 MA20 종가 이탈까지 보유하며 **잔량 상한 없음**.

## 판정 기준 (단테 정통)
0. **진입조건 강제 게이트**: 미체결 종목의 🟢 매수 준비는 entry_conditions의 필수조건이 전부 pass일 때만 허용한다. 외국인·프로그램 당일값 중 하나라도 0·음수·UNKNOWN이면 ⏸ 보류다.
   - **포지션 보유 종목**은 다음 차수 진입조건이 미충족이어도 decision을 **🟡 보유 유지**로 낸다. 추가매수 보류는 auto_conservative_action에 적는다. 손절·목표 도달 시에만 🚫 손절 검토 / 💰 절반 익절 / 🛡️ 트레일링 전환을 사용한다.
1. §2.5.2 양봉 + 거래량 동반 = 진입 트리거. 음봉 또는 거래량 위축 = 보류
2. §4.6.12 거래량: 눌림 차수 = 평균 0.7~1.5배 / 불타기 차수 = 1.5배+ / 음봉 거래량 2배+ = 진입 금지
3. §2.6 손절: 손절선 종가 이탈 시 무조건 컷. 손절 5% 이내 근접 = 손절 검토
4. §4.7 평단 SSOT: 사용자 체결 행 = 진실값. 시세 vs 평단 격차 ±50% 초과 시 본 분석에서 사용자 재확인 메모
5. **§4.9 추세 판정 (Vision)**: 5/20/60/112/224일선 정배열 + 채널 위치 + 거래량 정상도 + 트레일링 손절 여유 기반 종합 판정 (✅ 추세 유효 / ⚠️ 추세 약화 / 🛑 추세 이탈) + 자동 권장 조치 (🟢 매수·매수 준비 / 🟡 보유 유지 / ⏸ 보류 / 🚫 손절·패스)
6. **🔔 시그널 + 🛡️ 추천전략 행동 (§4.6.14 갱신)**: 4지 판정(사용자 의지)과 추세 판정(AI 객관 진단)을 비교하여 일치/불일치 시그널 산출 + 추천전략 행동 권장
   - **✅ 일치 (⭐⭐⭐)**: 두 판정 동의 → 추천전략 = (1) 현 결정 강화 (2) 다음 트리거 모니터링 (3) 익절·청산 라인 명시
   - **⚠️ 불일치 (재검토 필요)**: 두 판정 충돌 → 추천전략 = (1) 추가 매수·진입 자제 (2) 손절 모니터링 강화 (3) 트리거 발효 조건 더 까다롭게
   - **🚨 충돌 (손절 영역)**: 사용자 보유 의지 ↔ 추세 이탈 → 추천전략 = (1) 즉시 손절 검토 (2) 트레일링 전환 (3) 분할 부분 매도
7. **📚 과거 교훈 회상 (1단계)**: 위 '과거 종결 거래 교훈' 풀에서 현재 종목과 유사한 사례(같은 타입·유사 패턴·익절 효율/매도 타이밍)를 찾아, diagnosis에 교훈 1개를 구체적으로 인용한다 (예: "DS단석 사례 — 거래대금 100위 밖 + 목표가 고집은 익절 효율 저하").
8. **🎯 §4.4-D 목표 도달 후 처리 (2026-08-20 신설 · 활성 6지 연동)**: **체결** 종목이 **목표 1차에 도달**하면 decision을 **💰 절반 익절**로 낸다 — 보유 50% 익절(홀수 내림 · 잔량 1주면 생략하고 곧바로 트레일링), 잔량은 **MA20 종가 이탈 트레일링** + **분할매수 중단**(잔여 차수 지정가 취소). 이미 트레일링 중이면 **🛡️ 트레일링 전환**을 유지한다.
   - 🚨 **잔량에 상한을 두지 말 것** — 목표 2차·평단 +20%를 잔량 청산선으로 쓰면 백테스트상 현행보다 나빠진다(돌파 기대값 +1.93% → -0.05%).
   - 목표 1차는 저항대 기준이되 **평단 +8~15%** 범위 안이어야 하며, 범위 밖이면 절반 익절을 생략하고 트레일링으로 직행한다. **목표 1차 미달 매도는 계획 이탈**이므로 권장하지 않는다(오답노트 「조기 익절」 반복 사례).
   - 손절선은 §4.4 2축 산정값 그대로 유지한다(트레일링선이 손절선 위여도 손절 후퇴가 아니다).

## 작업
**Write tool로 다음 경로에 JSON만 저장하세요** (다른 출력 없이):
${OUT_JSON.replace(/\\/g, '/')}

JSON 스키마:
\`\`\`json
{
  "stocks": [
    {
      "code": "022100",
      "name": "포스코DX",
      "category": "active",
      "type": "스윙",
      "decision": "⏸ 보류 (관망)",
      "diagnosis": "한 문장 핵심 진단 (관찰 근거 → 추론 → 결론 3단)",
      "trend_judgment": "§4.9 Vision 추세 판정 한 줄 (예: '⚠️ 추세 약화 · 5/20/60일선 이탈 + 거래량 -41% 위축 · 권장: ⏸ 보류')",
      "signal": "🔔 4지 ↔ 추세 일치/불일치 진단 (예: '⚠️ 불일치 (재검토 필요)' 또는 '✅ 일치 (신뢰도 ⭐⭐⭐)' 또는 '🚨 충돌 (손절 영역)')",
      "auto_conservative_action": "🛡️ 시그널 기반 추천전략 행동 (예: '3차 30,500 진입 신중히 (양봉 + 거래량 1.5배 회복 확인 후) / 손절 28.5K 모니터링 강화')",
      "action_trigger": "다음 차수 매수가·수량 + 양봉 + 거래량 범위 (대시보드용)",
      "current_price": "30,750",
      "change_pct": "-8.87",
      "eval_gain": "+XX,XXX원 또는 -XX,XXX원",
      "supply_demand": "투자자공매 판별 한 줄 (예: '🟢 매집 · 외국인 5일 순매수 가속 + 공매도 1.5→0.7% 감소' / '🔴 이탈 · 외국인 지분율 16→12% + 공매도 23% 급증'). 투자자공매 이미지 없으면 '데이터 없음'",
      "entry_conditions": {
        "source": "schedule_flow_images",
        "evaluated_at": "YYYY-MM-DD HH:mm KST",
        "price": {"status": "pass|fail|unknown", "actual": 0, "required": "카드의 다음 차수 가격조건", "reason": "한 줄"},
        "bullish_candle": {"status": "pass|fail|unknown", "open_price": 0, "reference_price": 0, "reason": "한 줄"},
        "volume": {"status": "pass|fail|unknown", "actual": 0, "minimum": 0, "maximum": 0, "reason": "한 줄"},
        "foreign_net_buy": {"status": "pass|fail|unknown", "value_shares": 0, "direction": "buy|sell|unknown", "source": "schedule_flow_image|user_confirmed", "reason": "반드시 투자자 이미지의 당일/오늘 행"},
        "program_net_buy": {"status": "pass|fail|unknown", "value_shares": 0, "source": "schedule_flow_image", "reason": "반드시 당일/오늘 행"},
        "execution_strength": {"status": "pass|fail|unknown|not_required", "value_pct": null, "threshold_pct": null, "reason": "카드에 기준이 있을 때만 필수"},
        "all_required_met": false
      },
      "risk_level": "low|medium|high|critical"
    }
  ],
  "summary": {
    "active_total_eval_gain": "+X.XX만",
    "market_urgency": "전체 시장 충격도 한 줄"
  }${flowOutputSchema}
}
\`\`\`

stocks 배열 순서는 위 '종목 라인업'과 동일 (활성 ${nActive}종${nBowl3 ? ` + 🍚 밥그릇3 관찰 ${nBowl3}종 — [bowl3] 종목도 **빠짐없이** stocks 에 넣고, 진입조건은 그 줄의 「진입 기준」 1차 가격·거래량으로 판정. 포지션 없음(미체결)` : ''}). **category는 전 종목 "active"** (후보 분류 폐지 — 매집 여부와 무관). 추세 약화·이탈은 category가 아니라 decision(🚫 손절 검토 등)으로 표현한다. decision은 위 판정 옵션(**활성 6지**) 중 정확한 문자열 그대로 사용 (이모지 + 한글 포함). 💰 절반 익절·🛡️ 트레일링 전환은 **체결 종목 전용**이다.${flow.holdings || flow.theme ? '\n\n**보조지표가 제공된 경우 holdings_flow_summary + recommendations 두 필드를 반드시 채워서 출력하세요.** recommendations.top3는 정확히 3개. 데이터 부족 시 빈 배열 대신 \"recommendations\": {\"error\": \"...\"} 형태로 명시.' : ''}

작업 완료 후 추가 응답 불필요. JSON 파일 저장으로 종료.`;
}

function callClaudeCode(prompt) {
  if (!fs.existsSync(TEMP_DIR)) fs.mkdirSync(TEMP_DIR, { recursive: true });
  const promptFile = path.join(TEMP_DIR, `dante_prompt_${HHMM}.txt`);
  fs.writeFileSync(promptFile, prompt, 'utf-8');

  for (let attempt = 1; attempt <= MAX_RETRIES; attempt++) {
    try {
      log(`Claude Code CLI 호출 (시도 ${attempt}/${MAX_RETRIES})`);
      const res = runClaudeCli(prompt, `analyze_${HHMM}`, 'Read,Write,Glob,Grep', 30);

      if (res.timedOut) {
        log(`  ⚠ Claude 호출 타임아웃(10분) → 프로세스 트리 강제 종료 (시도 ${attempt}/${MAX_RETRIES})`);
      } else {
        const out = res.stdout || '';
        log(`  CLI 응답 길이: ${out.length} bytes (끝 200자): ${out.slice(-200)}`);
      }

      if (fs.existsSync(OUT_JSON)) {
        const parsed = JSON.parse(fs.readFileSync(OUT_JSON, 'utf-8'));
        if (Array.isArray(parsed.stocks) && parsed.stocks.length > 0) {
          log(`  분석 결과 검증 OK (${parsed.stocks.length}종)`);
          return parsed;
        }
        log(`  ⚠ JSON 스키마 검증 실패 — stocks 배열 비어있음 (시도 ${attempt})`);
      } else if (!res.timedOut) {
        log(`  ⚠ JSON 파일 미생성 (시도 ${attempt}/${MAX_RETRIES})`);
      }

      const combined = `${res.stderr || ''} ${res.stdout || ''}`;
      if (combined.includes('hit your limit') || combined.includes('rate limit') || combined.includes('resets')) {
        log(`  ⚠ Claude Code Rate limit 감지 — 중단`);
        throw new Error('CLAUDE_RATE_LIMITED');
      }
    } catch (err) {
      if (err.message === 'CLAUDE_RATE_LIMITED') throw err;
      log(`  Claude Code 호출 오류 (시도 ${attempt}): ${err.message?.slice(0, 200)}`);
    }
  }
  throw new Error('Claude Code CLI 호출 실패 (재시도 모두 소진)');
}

function renderHoldingsFlowSection(holdings) {
  if (!holdings || !Array.isArray(holdings.stocks)) return '';
  const rows = holdings.stocks.map(s => {
    if (s.error) return `<tr><td>${s.name}</td><td colspan="4" style="color:#8b949e;">데이터 없음 (${s.error})</td></tr>`;
    const fmt = (n) => (n >= 0 ? '+' : '') + Math.round(n).toLocaleString();
    return `<tr>
      <td><strong>${s.name}</strong> <span style="color:#8b949e;font-size:0.85em;">${s.code} · ${s.tag}</span></td>
      <td style="text-align:right;">${fmt(s.today.total)}억</td>
      <td style="text-align:right;">${fmt(s.sum_5d.total)}억</td>
      <td style="text-align:right;">${fmt(s.sum_20d.total)}억</td>
      <td>${s.signal} <span style="color:#8b949e;font-size:0.85em;">${s.signal_reason}</span></td>
    </tr>`;
  }).join('\n');
  const agg = holdings.aggregate || {};
  const headline = agg.headline_for_email || agg.headline || '';
  return `
<section id="holdings-flow-section" style="margin:24px 0;padding:16px;background:#161b22;border:1px solid #30363d;border-radius:8px;">
  <h3 style="margin-top:0;">📊 외국인·기관 수급 현황 <span style="color:#8b949e;font-size:0.85em;">(${holdings.date} ${holdings.data_freshness})</span></h3>
  ${headline ? `<p style="color:#79c0ff;">${headline}</p>` : ''}
  <table style="width:100%;border-collapse:collapse;">
    <thead><tr style="background:#21262d;">
      <th style="padding:8px;text-align:left;">종목</th>
      <th style="padding:8px;text-align:right;">오늘</th>
      <th style="padding:8px;text-align:right;">5일 누적</th>
      <th style="padding:8px;text-align:right;">20일 누적</th>
      <th style="padding:8px;text-align:left;">신호</th>
    </tr></thead>
    <tbody>${rows}</tbody>
  </table>
  <p style="color:#8b949e;font-size:0.85em;margin-top:8px;">활성 5일 합산 ${agg.active_total_5d >= 0 ? '+' : ''}${Math.round(agg.active_total_5d || 0)}억 · 후보 5일 합산 ${agg.candidate_total_5d >= 0 ? '+' : ''}${Math.round(agg.candidate_total_5d || 0)}억 · 🔴 ${agg.warning_count || 0}종 · 🟢 ${agg.favorable_count || 0}종</p>
</section>`;
}

// 🌊 눌림 탭 — 새 생애주기 4버킷 (돌파 탭과 공통 용어: 🟢노출·🎓졸업·⏳지속관찰·❌이탈)
function renderRecommendationsSection(tf) {
  if (!tf) return '';
  const active = Array.isArray(tf.candidates) ? tf.candidates : [];     // 🟢 노출 (80+, 반등확인 완료)
  const pending = Array.isArray(tf.pending) ? tf.pending : [];          // ⏳ 반등확인 대기
  const grad10 = Array.isArray(tf.graduated10) ? tf.graduated10 : [];   // 🎓 졸업① (+10%)
  const grad20 = Array.isArray(tf.graduated20) ? tf.graduated20 : [];   // 🎓 졸업② (+20%)
  const watching = Array.isArray(tf.watching) ? tf.watching : [];       // ⏳ 지속관찰
  const removed = Array.isArray(tf.removed) ? tf.removed : [];          // ❌ 이탈 (추세선)
  const isPromoted = (v) => v && v !== 'null' && v !== 'none';
  const mb = tf.market_breadth || {};                                  // ⚠️ 과열 회피 레짐
  const overheated = mb.overheated === true;

  const card = (r, i) => {
    const star = '⭐'.repeat(r.stars || 0);
    const px = (r.close_today ?? 0).toLocaleString();
    const ref = r.ref_close || r.close_today || 0;
    const gradPx = ref ? Math.round(ref * 1.2).toLocaleString() : '—';
    const seen = r.first_seen ? ` · 포착 ${r.first_seen}~` : '';
    const promo = isPromoted(r.promoted)
      ? `<span style="margin-left:6px;padding:2px 6px;background:#1a2e1a;border-radius:3px;color:#3fb950;font-size:0.8em;">✅ 스윙 편입</span>` : '';
    // 🪑 짝궁뎅이(쌍바닥) 배지 — 전 쌍바닥 표시. 거래량 다이버전스(2차 바닥 거래량 감소) 동반 시 강조(가점 부여 케이스)
    const db = r.double_bottom
      ? `<span style="margin-left:6px;padding:2px 6px;background:${r.db_vol_div ? '#1a2438' : '#1c1c22'};border-radius:3px;color:${r.db_vol_div ? '#79c0ff' : '#8b949e'};font-size:0.8em;" title="눌림 구간 쌍바닥${r.db_bounce_pct ? ` · 넥라인 +${r.db_bounce_pct}%` : ''}">🪑 짝궁뎅이${r.db_vol_div ? ' · 거래량↓' : ''}</span>` : '';
    // ✅ 반등확인 배지 (2단계: 눌림 셋업 → 정배열+전일고가 돌파 확인)
    const cf = r.confirmed
      ? `<span style="margin-left:6px;padding:2px 6px;background:#10241a;border-radius:3px;color:#3fb950;font-size:0.8em;" title="셋업 ${r.setup_date || ''} → ${r.confirm_days || ''}일 후 정배열+전일고가 돌파 확인">✅ 반등확인</span>` : '';
    // ⚠️ 시장 과열 배지 (브레드스 ≥ 임계값 — 눌림 진입 실패율 ↑, 경고만)
    const oh = overheated
      ? `<span style="margin-left:6px;padding:2px 6px;background:#2a1a0d;border-radius:3px;color:#f0883e;font-size:0.8em;" title="시장 브레드스 과열 — 약세장 재검증상 과열 구간 눌림 진입 실패율↑">⚠️ 시장 과열</span>` : '';
    // 🅰️/🅱️ 펀더멘탈 우량 배지 (①~⑤ soft 게이트 · ⑥ 가격위치 제외 · 컷 ≥4/5)
    //   soft = 탈락 없음. 배지 + 노출 정렬 1순위로만 쓴다.
    const qf = r.quality_fund;
    const qb = (qf && qf.badge)
      ? `<span style="margin-left:6px;padding:2px 6px;background:${qf.grade === 'A' ? '#1a2e1a' : '#1c2333'};border-radius:3px;color:${qf.grade === 'A' ? '#3fb950' : '#79c0ff'};font-size:0.8em;" title="펀더멘탈 ①매출증가 ②순익50억+흑자 ③부채/자본≤1 ④부채/자산&lt;0.8 ⑤자산증가 중 ${qf.passed}개 충족 (⑥가격위치 제외 — 돌·눌·재와 방향 상충)">${qf.grade === 'A' ? '🅰️ 우량' : '🅱️'} 펀더 ${qf.passed}/5</span>` : '';
    return `
    <div style="background:#0d1117;border:1px solid #30363d;border-radius:6px;padding:12px;margin-bottom:8px;">
      <div style="display:flex;justify-content:space-between;align-items:baseline;">
        <strong style="font-size:1.05em;">#${i + 1} ${r.name} <span style="color:#8b949e;font-size:0.85em;">${r.code} · ${r.market || ''}</span>${promo}${qb}${cf}${db}${oh}</strong>
        <span style="color:#79c0ff;">${star} 눌림 점수 ${r.score ?? 0}/100</span>
      </div>
      <div style="color:#8b949e;font-size:0.85em;margin:4px 0;">종가 ${px}원${seen} · 졸업가 ${gradPx}원(+20%) · 추세선(112/224) 종가 이탈 시 자동 제거</div>
    </div>`;
  };
  const pendRow = (d) => `
    <div style="background:#161d2a;border:1px solid #2a3a5a;border-radius:6px;padding:10px;margin-bottom:6px;">
      <strong style="color:#79c0ff;">⏳ ${d.name} <span style="color:#8b949e;font-size:0.85em;">${d.code} · ${d.market || ''}</span></strong>
      <span style="color:#8b949e;font-size:0.85em;margin-left:8px;">눌림 셋업(${d.setup_date || '당일'}, ${d.score ?? 0}점) · 반등확인 대기 — 정배열+셋업고가(${(d.setup_high ?? 0).toLocaleString()}원) 돌파 시 승격</span>
    </div>`;
  const gradRow = (d) => `
    <div style="background:#0d1f0d;border:1px solid #2ea04366;border-radius:6px;padding:10px;margin-bottom:6px;">
      <strong style="color:#3fb950;">🎓 ${d.name} <span style="color:#8b949e;font-size:0.85em;">${d.code}</span></strong>
      <span style="color:#3fb950;margin-left:8px;">+${d.gain_pct ?? 20}% 졸업</span>
    </div>`;
  const watchRow = (d) => `
    <div style="background:#1f1a0d;border:1px solid #5a4a1a;border-radius:6px;padding:10px;margin-bottom:6px;">
      <strong style="color:#d29922;">⏳ ${d.name} <span style="color:#8b949e;font-size:0.85em;">${d.code}</span></strong>
      <span style="color:#8b949e;font-size:0.85em;margin-left:8px;">추세 유효·노출 미달 (${d.score ?? 0}점)</span>
    </div>`;
  const rmRow = (d) => `
    <div style="background:#21130d;border:1px solid #5a2d1a;border-radius:6px;padding:10px;margin-bottom:6px;">
      <strong style="color:#f0883e;">❌ ${d.name} <span style="color:#8b949e;font-size:0.85em;">${d.code}</span></strong>
      <span style="color:#8b949e;font-size:0.85em;margin-left:8px;">추세선 종가 이탈 — 제거</span>
    </div>`;

  // 버킷별 카드(고정 4칸 · 비어도 자리 유지). 종목은 해당 버킷 카드 안에 배치.
  const bucket = (title, accent, arr, rowFn, empty) => {
    const body = arr.length ? arr.map(rowFn).join('\n')
      : `<p style="color:#8b949e;margin:6px 0 0;font-size:0.9em;">${empty}</p>`;
    return `
  <div style="border:1px solid ${accent}66;border-radius:8px;padding:12px 14px;margin-bottom:12px;background:#161b22;">
    <h4 style="margin:0 0 8px;color:${accent};">${title} (${arr.length}종)</h4>
    ${body}
  </div>`;
  };

  const mbPct = mb.pct != null ? (mb.pct * 100).toFixed(0) : null;
  const mbBanner = mbPct == null ? '' : `
  <div style="border:1px solid ${overheated ? '#f0883e66' : '#2ea04366'};border-radius:8px;padding:8px 12px;margin-bottom:12px;background:${overheated ? '#1f1409' : '#0d1f0d'};color:${overheated ? '#f0883e' : '#3fb950'};font-size:0.9em;">
    ${overheated ? '⚠️ 시장 과열' : '✅ 시장 정상'} · 브레드스 ${mbPct}% (시총1000억+ 종목 중 112선 위 ${mb.above}/${mb.total})${overheated ? ` — 브레드스 ≥${(mb.threshold * 100).toFixed(0)}% 과열 구간은 눌림 진입 실패율↑ (약세장 재검증). 진입 신중.` : ''}
  </div>`;
  return `
<section id="new-recommendations" style="margin:0;">
  ${mbBanner}
  ${bucket('🟢 노출 · 반등확인 완료 (score≥80)', '#79c0ff', active, card, '현재 없음')}
  ${bucket('⏳ 반등확인 대기 · 눌림 셋업 후 전일고가 돌파 대기', '#388bfd', pending, pendRow, '현재 없음')}
  ${bucket('🎓 졸업① · +10% 도달', '#3fb950', grad10, gradRow, '현재 없음')}
  ${bucket('🎓 졸업② · +20% 도달', '#2ea043', grad20, gradRow, '현재 없음')}
  ${bucket('⏳ 지속관찰 · 추세 유효·노출 미달', '#d29922', watching, watchRow, '현재 없음')}
  ${bucket('❌ 이탈 · 112/224 추세선 종가 이탈', '#f0883e', removed, rmRow, '현재 없음')}
  <p style="margin:4px 0 0;color:#8b949e;font-size:0.85em;">♻️ 매 08:15 자동 분류 · 🎓졸업·❌이탈 자동 판별 · 진입(스윙)만 사용자 요청 시 · ⚠️ 차트 미확인</p>
</section>`;
}

// 💥 돌파 탭 자동 렌더 (breakout_flow_{date}.json → #breakout-cards 섹션 교체)
function renderBreakoutSection(bf) {
  if (!bf) return '';
  const active = Array.isArray(bf.active) ? bf.active : [];
  const grad10 = Array.isArray(bf.graduated10) ? bf.graduated10 : [];
  const grad20 = Array.isArray(bf.graduated20) ? bf.graduated20 : [];
  const watching = Array.isArray(bf.watching) ? bf.watching : [];

  // 유형(유형1 당일 폭발 / 유형2 D-3 유지)은 카드 분리 없이 종목에 라벨로 표기 (2026-06-11 사용자 지시)
  const kindTag = (r) => r.kind === 'type2'
    ? `<span style="margin-left:6px;padding:1px 6px;background:#0d2818;border-radius:3px;color:#3fb950;font-size:0.78em;">🌱 D-3 유지</span>`
    : `<span style="margin-left:6px;padding:1px 6px;background:#2a0d0d;border-radius:3px;color:#f85149;font-size:0.78em;">🔥 당일 폭발</span>`;

  const card = (r, i) => {
    const star = '⭐'.repeat(r.stars || 0);
    const px = (r.current_close ?? r.breakout_close ?? 0).toLocaleString();
    const ref = r.ref_close || r.breakout_close || 0;
    const gradPx = ref ? Math.round(ref * 1.2).toLocaleString() : '—';
    const cap = r.market_cap_billion ? ` · 시총 ${r.market_cap_billion.toLocaleString()}억` : '';
    const bod = r.breakout_date ? `${r.breakout_date.slice(4, 6)}-${r.breakout_date.slice(6, 8)}` : '';
    return `
    <div style="background:#0d1117;border:1px solid #30363d;border-radius:6px;padding:12px;margin-bottom:8px;">
      <div style="display:flex;justify-content:space-between;align-items:baseline;">
        <strong style="font-size:1.05em;">#${i + 1} ${r.name} <span style="color:#8b949e;font-size:0.85em;">${r.code} · ${r.market || ''}</span></strong>
        <span style="color:#f0883e;">${star} 돌파 점수 ${r.score}/100</span>
      </div>
      <div style="color:#8b949e;font-size:0.85em;margin:2px 0;">${r.line} 돌파 · 거래량 ${r.vmult}배${cap} · 돌파일(${bod}) 종가 ${(r.breakout_close || 0).toLocaleString()}원</div>
      <div style="margin:8px 0;color:#c9d1d9;">현재 ${px}원 · 졸업가 ${gradPx}원(+20%) · 추세선(112/224) 종가 이탈 시 자동 제거 (차트 미확인 · 정량 기준)</div>
    </div>`;
  };

  const t1 = active.filter(a => a.kind === 'type1');
  const t2 = active.filter(a => a.kind === 'type2');
  const removed = Array.isArray(bf.removed) ? bf.removed : [];
  const activeSorted = active.slice().sort((a, b) => (b.score || 0) - (a.score || 0));

  const gradRow = (d) => `
    <div style="background:#0d1f0d;border:1px solid #2ea04366;border-radius:6px;padding:10px;margin-bottom:6px;">
      <strong style="color:#3fb950;">🎓 ${d.name} <span style="color:#8b949e;font-size:0.85em;">${d.code}</span></strong>
      <span style="color:#3fb950;margin-left:8px;">+${d.gain_pct ?? 20}% 졸업 (${d.graduated_date || ''})</span>
    </div>`;
  const watchRow = (d) => `
    <div style="background:#1f1a0d;border:1px solid #5a4a1a;border-radius:6px;padding:10px;margin-bottom:6px;">
      <strong style="color:#d29922;">⏳ ${d.name} <span style="color:#8b949e;font-size:0.85em;">${d.code}</span></strong>
      <span style="color:#8b949e;font-size:0.85em;margin-left:8px;">추세 유효 (포착 ${d.first_seen || ''})</span>
    </div>`;
  const rmRow = (d) => `
    <div style="background:#21130d;border:1px solid #5a2d1a;border-radius:6px;padding:10px;margin-bottom:6px;">
      <strong style="color:#f85149;">❌ ${d.name} <span style="color:#8b949e;font-size:0.85em;">${d.code}</span></strong>
      <span style="color:#8b949e;font-size:0.85em;margin-left:8px;">돌파선 종가 이탈 — 제거</span>
    </div>`;

  // 버킷별 카드(고정 4칸 · 비어도 자리 유지)
  const bucket = (title, accent, arr, rowFn, empty) => {
    const body = arr.length ? arr.map(rowFn).join('\n')
      : `<p style="color:#8b949e;margin:6px 0 0;font-size:0.9em;">${empty}</p>`;
    return `
  <div style="border:1px solid ${accent}66;border-radius:8px;padding:12px 14px;margin-bottom:12px;background:#161b22;">
    <h4 style="margin:0 0 8px;color:${accent};">${title} (${arr.length}종)</h4>
    ${body}
  </div>`;
  };

  return `
<section id="breakout-cards" style="margin:0;">
  <p style="color:#8b949e;font-size:0.85em;margin:0 0 10px;">모집단 ${bf.universe_size || '—'}종 · ${bf.scanner_version || ''}</p>
  ${bucket('🟢 노출 · 돌파 당일', '#f0883e', activeSorted, card, '현재 없음')}
  ${bucket('🎓 졸업① · +10% 도달', '#3fb950', grad10, gradRow, '현재 없음')}
  ${bucket('🎓 졸업② · +20% 도달', '#2ea043', grad20, gradRow, '현재 없음')}
  ${bucket('⏳ 지속관찰 · 당일 이후·추세 이탈 감시', '#d29922', watching, watchRow, '현재 없음')}
  ${bucket('❌ 이탈 · 돌파선 종가 이탈', '#f85149', removed, rmRow, '현재 없음')}
  <p style="margin:4px 0 0;color:#8b949e;font-size:0.85em;">♻️ 매 08:15 자동 분류 · 🎓졸업·❌이탈 자동 판별 · 진입(스윙)만 사용자 요청 시</p>
</section>`;
}

// 🧭 테마 탭 자동 렌더 (theme_rotation_{date}.json → #theme-cards 섹션 교체)
function renderThemeSection(tr) {
  if (!tr || !tr.pool) return '';
  const pool = tr.pool;
  const sgn = (v, d = 1) => (v == null ? '—' : `${v > 0 ? '+' : ''}${Number(v).toFixed(d)}`);
  const pct = v => `<span style="color:${v > 0 ? '#f85149' : v < 0 ? '#58a6ff' : '#c9d1d9'};">${sgn(v)}%</span>`;
  const small = s => `<span class="hd-note">${s}</span>`;
  const md = s => (s ? `${s.slice(4, 6)}/${s.slice(6, 8)}` : '');
  const bd = md(tr.basis_date);
  const cap = b => (b >= 10000 ? `${(b / 10000).toFixed(1)}조` : `${Number(b).toLocaleString()}억`);
  const gil = (ma, to) => (ma == null ? '—' : `${Number(ma).toLocaleString()} ${small(to == null ? '' : to > 0 ? '(이미 아래)' : `(${sgn(to)}%)`)}`);

  const activeTable = (rows, empty) => rows.length ? `
  <table class="stock-table" style="font-size:0.88em;margin:4px 0 12px;">
    <thead><tr><th>테마</th><th>종목</th><th>시총</th><th>편입</th><th>현재가</th><th>1차 길목(10일선)</th><th>2차 길목(20일선)</th><th>거래대금 비율</th><th>224일선</th></tr></thead>
    <tbody>
${rows.map(v => `      <tr><td style="text-align:left;">${v.theme}</td><td style="text-align:left;"><strong>${v.name}</strong> ${small(v.code)}${v.in_today ? '' : ` ${small('(오늘 조건 밖·추적 중)')}`}</td><td style="white-space:nowrap;">${cap(v.market_cap_billion)}</td><td style="white-space:nowrap;">${md(v.entry_basis)} ${Number(v.entry_close).toLocaleString()}</td><td style="white-space:nowrap;">${Number(v.close).toLocaleString()} ${pct(v.gain)}</td><td>${gil(v.ma10, v.to_ma10)}</td><td>${gil(v.ma20, v.to_ma20)}</td><td>${v.tv_ratio == null ? '—' : `<strong>${v.tv_ratio.toFixed(2)}</strong>`}</td><td>${v.gap224 == null ? '—' : `${sgn(v.gap224, 0)}%`}</td></tr>`).join('\n')}
    </tbody>
  </table>` : `<p style="color:#8b949e;margin:6px 0 12px;font-size:0.9em;">${empty}</p>`;

  const endLabel = { graduated: '🎓 졸업', removed: '❌ 이탈', expired: '⌛ 만료' };
  const endRows = (rows, empty) => rows.length ? `
  <table class="stock-table" style="font-size:0.86em;margin:4px 0 12px;">
    <thead><tr><th>결과</th><th>테마</th><th>종목</th><th>편입</th><th>종료</th><th>결과 수익률</th><th>보유</th></tr></thead>
    <tbody>
${rows.map(h => `      <tr><td>${endLabel[h.status] || h.status}</td><td style="text-align:left;">${h.theme}</td><td style="text-align:left;"><strong>${h.name}</strong> ${small(h.code)}</td><td>${md(h.entry_basis)} ${Number(h.entry_close).toLocaleString()}</td><td>${md(h.end_date)} ${Number(h.close).toLocaleString()}</td><td>${pct(h.gain)}</td><td>${h.held_days}일</td></tr>`).join('\n')}
    </tbody>
  </table>` : `<p style="color:#8b949e;margin:6px 0 12px;font-size:0.9em;">${empty}</p>`;

  const st = pool.stats || {};
  return `
<section id="theme-cards" style="margin:0;">
  <div class="highlight" style="background:#0d1f2a;border-left:4px solid #39c5cf;margin-bottom:16px;">
    <strong style="color:#39c5cf;">📊 기준</strong> <strong>${bd} 종가</strong> · 오늘 신규 후보 ${(tr.picks || []).length}종 · 추적 🟢 ${pool.touched.length} · ⏳ ${pool.waiting.length}
    · <strong>누적</strong> 🎓 ${st.graduated || 0} · ❌ ${st.removed || 0} · ⌛ ${st.expired || 0} · 승률 ${st.win_rate == null ? '—' : `${st.win_rate}%`} ${small('(졸업 ÷ (졸업+이탈))')}
    ${tr.rules ? `<br>${small(`범위·테마 조건: ${tr.rules.theme}`)}` : ''}
    ${(tr.groups || []).length ? `<br>${small(tr.groups.map(g => `${g.theme} ${g.n}종: 20일선 위 ${Math.round(g.above20 * 100)}% · 거래대금비 ${g.tv_ratio.toFixed(2)} · 고점대비 ${sgn(g.off_high)}% · 4주 ${sgn(g.cum)}%`).join(' | '))}` : ''}
  </div>
  <h3 style="color:#3fb950;margin:8px 0 4px;">🟢 길목 도달 ${small('(저가가 10일선에 닿음 — 매수 검토 구간)')} (${pool.touched.length}종)</h3>
${activeTable(pool.touched, '현재 없음')}
  <h3 style="color:#d29922;margin:8px 0 4px;">⏳ 대기 ${small('(편입됨 · 10일선까지 아직 내려오지 않음)')} (${pool.waiting.length}종)</h3>
${activeTable(pool.waiting, '현재 없음')}
  <h3 style="color:#2ea043;margin:8px 0 4px;">🎓 졸업 ${small('(편입일 종가 +10% · 최근 14일)')} (${pool.graduated.length}종)</h3>
${endRows(pool.graduated, '최근 없음')}
  <h3 style="color:#f85149;margin:8px 0 4px;">❌ 이탈 · ⌛ 만료 ${small('(종가 20일선 아래 / 20거래일 경과 · 최근 14일)')} (${pool.ended.length}종)</h3>
${endRows(pool.ended, '최근 없음')}
  <p style="margin:4px 0 0;color:#8b949e;font-size:0.85em;">♻️ 매 아침 회차 자동 갱신 · 거래대금 비율 = 최근 주 일평균 ÷ 직전 주 일평균 · 편입 후엔 오늘 조건에서 빠져도 졸업·이탈·만료까지 추적 · ⚠️ 차트·재무·공시 미확인 — 매수 신호 아님 · 생성 ${tr.generated_at || ''}</p>
</section>`;
}

// 📊 요약 탭 스캐너 손익 평가 — forward-test (08:15 스케줄 누적 신호를 단테 분할매수로 측정)
//   eval_forward.py 산출 data/forward_scorecard.json 을 읽어 렌더 (백테스트 아님)
function renderScannerEval(sc) {
  if (!sc) return null;
  const b = sc.breakout || {}, p = sc.pullback || {};
  const row = (label, pv, bv, hl) => `      <tr${hl ? ' style="background:#0d1f0d;"' : ''}><td>${label}</td><td style="text-align:center;">${pv}</td><td style="text-align:center;">${bv}</td></tr>`;
  // 🏆 승 종목 노출 (eval_forward wins_detail)
  const winNames = q => ((q.wins_detail || []).map(w => `${w.name} +${w.peak_pct}%·${w.days}일`).join('<br>')) || '—';
  return `
  <p style="font-size:0.85em;color:#8b949e;margin:0 0 12px;">
    <strong>📡 forward-test (08:15 스케줄 실구동 누적 신호)</strong> · 측정 기법 = <strong>대시보드 졸업 기준</strong>(포착일 종가 단일 진입) → <strong>포착일 종가 +10% 졸업① 이상 = 승 / 돌파선(112·224) 종가 이탈 = 패</strong>. <span style="color:#d29922;">승 == 돌파·눌림 탭 졸업①+② 카운트와 동일 기준. 원장 누적분만 집계 — 회차 쌓일수록 표본 증가.</span>
  </p>
  <table class="stock-table" style="font-size:0.92em;">
    <thead><tr><th>지표</th><th>🌊 눌림</th><th>💥 돌파</th></tr></thead>
    <tbody>
${row('누적 신호', p.signals ?? 0, b.signals ?? 0)}
${row('승 / 패', `${p.wins ?? 0} / ${p.losses ?? 0}`, `${b.wins ?? 0} / ${b.losses ?? 0}`)}
${row('승률 (+10% 졸업)', `${p.win_rate_pct ?? 0}%`, `${b.win_rate_pct ?? 0}%`, true)}
${row('평균 손실 (이탈)', `${p.avg_loss_pct ?? 0}%`, `${b.avg_loss_pct ?? 0}%`)}
${row('기대값 (1신호당)', `${p.expectancy_pct ?? 0}%`, `${b.expectancy_pct ?? 0}%`, true)}
${row('진행중(미청산)', p.open ?? 0, b.open ?? 0)}
${row('🏆 승 종목 (+10% 졸업)', winNames(p), winNames(b))}
    </tbody>
  </table>
  <p style="font-size:0.78em;color:#8b949e;margin-top:8px;">생성: ${sc.generated || ''} · 실거래 매매일지는 🏁 종료 탭 · 전체 과거 백테스트는 backtest_scanners.py</p>`;
}

function renderHoldingsTrendSection(holdingsTrend) {
  if (!Array.isArray(holdingsTrend) || holdingsTrend.length === 0) return '';

  const color = (st) => st.startsWith('🛑') ? '#f85149'
    : st.startsWith('⚠️') ? '#d29922'
    : st.startsWith('✅') ? '#3fb950'
    : '#8b949e';

  const rows = holdingsTrend.map(h => {
    const chg = (h.change_pct != null) ? `${h.change_pct >= 0 ? '+' : ''}${h.change_pct}%` : '—';
    const close = h.close_today != null ? h.close_today.toLocaleString() : '—';
    const vol = h.vol_ratio != null ? `${h.vol_ratio}배` : '—';
    return `<tr>
      <td><strong>${h.name}</strong> <span style="color:#8b949e;font-size:0.85em;">${h.code} · ${h.tag || ''}</span></td>
      <td style="text-align:right;">${close}원</td>
      <td style="text-align:right;color:${(h.change_pct || 0) >= 0 ? '#3fb950' : '#f85149'};">${chg}</td>
      <td style="text-align:right;">${vol}</td>
      <td style="color:${color(h.status)};"><strong>${h.status}</strong></td>
      <td style="color:#c9d1d9;font-size:0.9em;">${h.reason || ''}</td>
    </tr>`;
  }).join('\n');

  const broke = holdingsTrend.filter(h => h.trend_break).map(h => h.name);
  const headline = broke.length
    ? `🛑 추세 이탈 ${broke.length}종 — ${broke.join(' / ')} (장 시작 전 손절·트레일링 점검 권장)`
    : `✅ 보유·후보 ${holdingsTrend.length}종 추세 유효 (전일 확정 종가 기준)`;

  return `
<section id="holdings-trend-section" style="margin:24px 0;padding:16px;background:#161b22;border:1px solid #30363d;border-radius:8px;">
  <h3 style="margin-top:0;">🔎 기존 보유·후보 추세 점검 <span style="color:#8b949e;font-size:0.85em;">(전일 확정 OHLCV · 차트 미확인)</span></h3>
  <p style="color:${broke.length ? '#f0883e' : '#79c0ff'};">${headline}</p>
  <table style="width:100%;border-collapse:collapse;">
    <thead><tr style="background:#21262d;">
      <th style="padding:8px;text-align:left;">종목</th>
      <th style="padding:8px;text-align:right;">전일 종가</th>
      <th style="padding:8px;text-align:right;">등락률</th>
      <th style="padding:8px;text-align:right;">거래량</th>
      <th style="padding:8px;text-align:left;">추세 판정</th>
      <th style="padding:8px;text-align:left;">근거</th>
    </tr></thead>
    <tbody>${rows}</tbody>
  </table>
  <p style="color:#8b949e;font-size:0.85em;margin-top:8px;">※ 장 시작 전 수치 판정(5/20/60/112/224 이평·거래량). 추세 이탈 종목은 당일 19:00 회차 분석으로 손절/트레일링 최종 확정.</p>
</section>`;
}

function buildRecommendOnlyPrompt(meta, themeFlow) {
  const candidates = (themeFlow && Array.isArray(themeFlow.candidates)) ? themeFlow.candidates : [];
  const dropped = (themeFlow && Array.isArray(themeFlow.dropped)) ? themeFlow.dropped : [];
  if (candidates.length === 0 && dropped.length === 0) {
    return null;
  }
  const candidatesJson = JSON.stringify(candidates.slice(0, 12), null, 2);
  const droppedJson = JSON.stringify(dropped.slice(0, 15), null, 2);
  return `당신은 주식단테 매매기법 정통 분석가입니다. 다음은 **영속 추천 풀(recommend_pool)** 데이터입니다. 매 회차 ① 기존 풀 전 종목을 오늘 OHLCV로 재평가(통과↔이탈)한 뒤 ② 신규 종목을 편입하며, **사용자 지시 전에는 어떤 종목도 풀에서 삭제하지 않습니다**. 단테 §2.1 (밥그릇 4구간) + §2.5.2 (장기 이평 릴레이) + §4.4 (1차a 안착가 진입) 정합 정성 판단 후 **(A) 통과 종목 진단 카드 + (B) 이탈 종목 서술**을 모두 산출하세요.

## (A) 추천 풀 — 통과 종목 (기존 풀 재평가 통과 + 오늘 신규 편입 누적 · 224 돌파 후 첫 눌림 + 지지선 근접 + 거래량 감소 + DART 공시 필터)
\`\`\`json
${candidatesJson}
\`\`\`

## (B) 추천 풀 — 이탈 종목 (조건 이탈 누적 · 회복 시 통과 복귀 가능 · consecutive_drop_days = 연속 이탈 회차)
\`\`\`json
${droppedJson}
\`\`\`

## AI단테.md §6 (현재 보유·후보 — 편입 상태 참조용)
${meta.section6_text.slice(0, 3000)}

## 판정 기준 (단테 정통)
1. §2.1 밥그릇 3구간 본격 진입 = 224 돌파 후 첫 눌림(=2구간 후반)이 가장 신뢰도 높음.
2. 224 또는 112 지지선 ±1~3% 근접 + 거래량 감소(0.7배↓) 종목 우선.
3. **§6 후보/활성 편입 종목(promoted != null)도 통과 카드에 유지** — 풀에서 제외하지 말고 진단에 "✅ 후보/활성 편입됨" 명시. 조건 이탈 시에만 (B) 이탈로 이동 (사용자 지시 전 삭제 금지).
4. 시총·유동성 충분, 음봉이라도 눌림 자연스러우면 OK (반등 트리거 대기).
5. **DART disclosure 정보 참고** — 영업이익 YoY 증가·호재 공시(자사주취득/배당)는 강한 가산점, 카드 진단에 인용.
6. **시나리오(1차a/1차b/2차/손절) 산출 금지** — 추천 카드에는 시나리오 라인을 포함하지 말 것. 시나리오는 사용자가 §4.6.13에서 "매수" 또는 "후보 풀"을 선택하여 stocks.json 활성/후보로 편입한 후, 다음 19:00 full 분석에서 별도 산출.

## 작업
**Write tool로 다음 경로에 JSON만 저장하세요** (다른 출력 없이):
${OUT_JSON.replace(/\\/g, '/')}

JSON 스키마:
\`\`\`json
{
  "mode": "recommend-only",
  "recommendations": {
    "scanner_date": "${themeFlow?.date || ''}",
    "headline": "한 줄 요약 (당일 핵심 패턴 + 통과 종목 수 + 이탈 종목 수)",
    "top3": [
      {
        "code": "풀 통과 종목의 code 그대로",
        "name": "풀 통과 종목의 name 그대로",
        "market": "KOSPI 또는 KOSDAQ",
        "dante_pullback_score": 92,
        "stars": 4,
        "promoted": "입력 promoted 값 그대로 (null 또는 candidate/active)",
        "first_seen": "입력 first_seen 그대로 (추천 풀 최초 편입일)",
        "wave_position": "엘리엇 2파 조정 마무리 등 추정 (선택)",
        "disclosure_badges": ["✅ 2025 연간 영업이익 YoY +10.5%", "➕ 현금ㆍ현물배당결정 (20260507)"],
        "diagnosis": "정량+정성+공시 종합 한 줄 (224 돌파 N일전 · 지지선 위치 · 눌림 깊이 · 거래량 동향 · 공시 호재 자연어로 녹임. promoted면 '✅ 후보/활성 편입됨' 명시)",
        "review_required": true
      }
    ],
    "dropped": [
      {
        "code": "풀 이탈 종목의 code 그대로",
        "name": "풀 이탈 종목의 name 그대로",
        "market": "KOSPI 또는 KOSDAQ",
        "reason_code": "ma112_break / pullback_too_deep / vol_surge_selling / above_224_overheated / ma224_break / data_missing / support_gap / breakout_aged_out / no_pullback / market_cap_below / other 중 입력 그대로",
        "prev_score": 92,
        "consecutive_drop_days": "입력 consecutive_drop_days 그대로 (연속 이탈 회차)",
        "narration": "112일선 X원 -2.3% 종가 이탈 + 거래량 평균 1.8배 폭증 매도 → 단기 추세 균열. (정량 reject + 오늘 OHLCV 수치를 자연어로 결합. 1~2문장 · 회복 조건 1개 명시)"
      }
    ]
  }
}
\`\`\`

**중요 — 출력 정책 (2026-06-02 영속 풀 전환)**:
- (A) top3 = 풀 통과 종목 **전부** (편입 종목 promoted 포함 · 삭제 금지). 1개도 12개도 그대로. 시나리오 라인 절대 포함 금지.
- (B) dropped = 풀 이탈 종목 입력 그대로 **모두** 자연어 narration 채워서 반환. 임의로 줄이거나 빈 배열로 만들지 말 것 (이탈도 누적 추적 — 사용자 지시 전 삭제 금지).
- 통과·이탈 둘 다 0종이면 \`top3: [], dropped: []\` 빈 배열.
- \`recommendations.error\` 응답 금지.

작업 완료 후 추가 응답 불필요. JSON 파일 저장으로 종료.`;
}

function patchFlowSections(html, analysis) {
  let patched = html;
  const holdingsHtml = renderHoldingsFlowSection(analysis.holdings_flow_data || null) ||
    (analysis._injected_holdings ? '' : '');
  const recsHtml = renderRecommendationsSection(analysis.theme_flow_data || null);
  // 기존 보유·후보 추세점검 카드는 눌림 탭에서 제거 (2026-06-11 사용자 지시) — 미렌더.
  // dupRegex가 과거 회차에 삽입된 holdings-trend-section 도 함께 제거한다.
  const trendHtml = '';
  const recsTabHtml = `${recsHtml}`;

  // 마커 기반 삽입 (마커 없으면 body 끝)
  const HOLDINGS_MARK = '<!-- HOLDINGS_FLOW_PLACEHOLDER -->';
  const RECS_MARK = '<!-- RECOMMENDATIONS_PLACEHOLDER -->';

  if (holdingsHtml) {
    if (patched.includes(HOLDINGS_MARK)) {
      patched = patched.replace(HOLDINGS_MARK, holdingsHtml);
    } else {
      patched = patched.replace(/<body([^>]*)>/i, `<body$1>\n${holdingsHtml}`);
    }
  }
  if (recsTabHtml) {
    // Step 1: 이전 회차에 삽입된 추세 점검·추천 섹션 모두 제거 (중복 누적 방지)
    const dupRegex = /<section id="(?:new-recommendations|holdings-trend-section)"[\s\S]*?<\/section>\s*/g;
    const dupCount = (patched.match(dupRegex) || []).length;
    if (dupCount > 0) {
      patched = patched.replace(dupRegex, '');
      log(`body 끝 누적 섹션 ${dupCount}개 제거`);
    }

    // Step 2: 추천 탭 안 #recommendation-cards-wrapper 영역에 카드 삽입 (부모 컨테이너 내부 = 정상 너비)
    const wrapperRegex = /(<div id="recommendation-cards-wrapper"[^>]*>)[\s\S]*?(<\/div>\s*<\/section>)/;
    if (wrapperRegex.test(patched)) {
      patched = patched.replace(wrapperRegex, `$1\n${recsTabHtml}\n$2`);
      log(`추천 탭 wrapper 안 카드 갱신 완료`);
    } else if (patched.includes(RECS_MARK)) {
      patched = patched.replace(RECS_MARK, recsTabHtml);
    } else {
      // 2026-10-19 🌊 눌림 탭 삭제 — wrapper 가 없으면 body 끝에 붙이지 않고 건너뛴다
      log(`[INFO] recommendation-cards-wrapper 없음(눌림 탭 삭제) — 눌림 패치 생략`);
    }
  }
  // 💥 돌파 탭: #breakout-cards-wrapper 안 <section id="breakout-cards"> 통째 교체
  const breakoutHtml = renderBreakoutSection(analysis.breakout_flow_data || null);
  if (breakoutHtml) {
    const boWrap = /(<div id="breakout-cards-wrapper"[^>]*>)[\s\S]*?(<\/div>\s*<p class="highlight"[^>]*background:#f8514922)/;
    if (boWrap.test(patched)) {
      patched = patched.replace(boWrap, `$1\n${breakoutHtml}\n  $2`);
      log(`💥 돌파 탭 wrapper 안 카드 갱신 완료`);
    } else {
      log(`[WARN] breakout-cards-wrapper 마커 미발견 — 돌파 탭 패치 생략`);
    }
  }
  // 🧭 테마 탭: <section id="theme-cards"> 통째 교체
  const themeHtml = renderThemeSection(analysis.theme_rotation_data || null);
  if (themeHtml) {
    const thSec = /<section id="theme-cards"[\s\S]*?<\/section>/;
    if (thSec.test(patched)) {
      patched = patched.replace(thSec, () => themeHtml.trim());
      log(`🧭 테마 탭 갱신 완료`);
    } else {
      log(`[WARN] theme-cards 마커 미발견 — 테마 탭 패치 생략`);
    }
  }

  // 📊 요약 탭 #scanner-eval = forward-test (eval_forward.py → forward_scorecard.json). 매 회차 갱신.
  try {
    const scPath = path.join(DATA_DIR, 'forward_scorecard.json');
    if (fs.existsSync(scPath)) {
      const evalInner = renderScannerEval(JSON.parse(fs.readFileSync(scPath, 'utf-8')));
      const ev = /(<section id="scanner-eval">)[\s\S]*?(<\/section>)/;
      if (evalInner && ev.test(patched)) {
        patched = patched.replace(ev, (m, a, c) => `${a}${evalInner}${c}`);
        log(`📊 요약 탭 스캐너 평가(forward-test·단테 분할) 갱신`);
      }
    }
  } catch (e) { log(`[WARN] scanner-eval 갱신 실패: ${e.message}`); }

  if (holdingsHtml || recsTabHtml || breakoutHtml) {
    log(`HTML 보조지표 섹션 패치: holdings=${!!holdingsHtml}, recs=${!!recsHtml}, trend=${!!trendHtml}, breakout=${!!breakoutHtml}`);
  }
  return patched;
}

function patchCardBodyHeaders(html, analysis) {
  // 매 회차마다 각 종목 카드의 "차트 주석 분석 / 차트 시나리오 요약 / 자동 분기 룰"
  // 헤더 라인의 날짜·시세·핵심 진단을 dante_analysis 결과로 자동 갱신.
  // 본문 풍부한 콘텐츠(시나리오 ul·자동 분기 룰 ① ② ③)는 사용자 손작업 보존.
  let patched = html;
  const dateStr = analysis.date || '';
  const mmdd = dateStr.slice(5);  // MM-DD
  let count = 0;

  const truncate = (s, n) => {
    if (!s) return '';
    return s.length > n ? s.slice(0, n) + '...' : s;
  };

  for (const s of analysis.stocks || []) {
    const id = s.code;
    const cardStart = patched.indexOf(`id="card-${id}"`);
    if (cardStart < 0) continue;
    const nextCard = patched.indexOf('<div class="card"', cardStart + 1);
    const cardEnd = nextCard < 0 ? patched.length : nextCard;

    const oldCard = patched.slice(cardStart, cardEnd);
    let newCard = oldCard;

    const priceStr = s.current_price ? `${s.current_price}원` : '';
    const chgStr = s.change_pct ? ` (${s.change_pct}%)` : '';
    const gainStr = s.eval_gain && s.category === 'active' ? ` · ${s.eval_gain}` : '';
    const diag = truncate(s.diagnosis, 100);
    const trig = truncate(s.action_trigger, 100);

    // 1. 차트 주석 분석 헤더
    newCard = newCard.replace(
      /<strong>📊\s*차트 주석 분석[^<]*<\/strong>/,
      `<strong>📊 차트 주석 분석 (${dateStr} · ${priceStr}${chgStr}${gainStr}${diag ? ' · ' + diag : ''})</strong>`
    );

    // 2. 차트 시나리오 요약 헤더 (📌 이모지는 strong 외부)
    newCard = newCard.replace(
      /<strong>차트 시나리오 요약[^<]*<\/strong>/,
      `<strong>차트 시나리오 요약 (${dateStr} · ${priceStr}${chgStr}${trig ? ' · ' + trig : ''})</strong>`
    );

    // 3. 자동 분기 룰 헤더 (📐 이모지는 strong 외부, 형식: "MM-DD 자동 분기 룰 (...)")
    newCard = newCard.replace(
      /<strong>[\d\-]+\s*자동 분기 룰[^<]*<\/strong>/,
      `<strong>${mmdd} 자동 분기 룰 (${priceStr}${chgStr}${gainStr})</strong>`
    );

    if (newCard !== oldCard) {
      patched = patched.slice(0, cardStart) + newCard + patched.slice(cardEnd);
      count++;
    }
  }
  log(`HTML 카드 헤더 자동 갱신: ${count}/${(analysis.stocks || []).length}건`);
  return patched;
}

// 종목 코드 → 화면 노출 카드의 [start,end) 범위 해석.
// 우선순위: card-{code}-active (활성 suffix) → card-{code} (bare) → card-{code}-cand (후보 suffix).
// -active를 먼저 찾으므로 비에이치아이(card-083650-active + 구 hidden card-083650)처럼
// 같은 코드의 hidden 구카드가 있어도 활성 카드를 정확히 집는다.
function resolveCardRange(html, code) {
  for (const cid of [`card-${code}-active`, `card-${code}`, `card-${code}-cand`]) {
    const idx = html.indexOf(`id="${cid}"`);
    if (idx < 0) continue;
    const start = html.lastIndexOf('<div class="card"', idx);
    if (start < 0) continue;
    const next = html.indexOf('<div class="card"', idx + 1);
    const end = next < 0 ? html.length : next;
    return { start, end, cid };
  }
  return null;
}

// summary-grid의 "현재가 (날짜)" 4배너를 캡처 시세(prices_*.json)로 자동 주입.
// prices = { 종목명: {code, price:"38,200", change_won:"+5,750", change_pct:"17.71"} }
// analysis(있으면) = Claude 분석 JSON. 활성 종목 eval_gain을 배너에 부가 표기.
// prices-only 모드(analysis 없음)에서는 가격+등락률만 주입, stale eval span은 제거(오표기 방지).
function patchCardPriceBanners(html, prices, analysis) {
  let patched = html;
  let count = 0;
  const skipped = [];
  const mmdd = ((analysis && analysis.date) || TODAY).slice(5);
  const evalByCode = {};
  for (const s of (analysis && analysis.stocks) || []) {
    if (s.eval_gain && s.category === 'active') evalByCode[s.code] = s.eval_gain;
  }
  // class="value" 뒤 인라인 style 등 추가 속성도 허용([^>]*) — 수기 추가 카드 배너 스킵 방지
  // 2026-07-23: label 안에 <span class="hd-note">(07-22)</span> 같은 태그가 들어가도 매칭되도록
  // [^<]* → [\s\S]*?  (기존 [^<]*는 태그가 끼는 순간 배너 패치가 조용히 skip돼 시세가 동결됨)
  const bannerRe = /<div class="summary-item"><div class="label">현재가[\s\S]*?<\/div><div class="value[^>]*>[\s\S]*?<\/div><\/div>/;
  for (const [name, p] of Object.entries(prices)) {
    if (!p || !p.code || !p.price) continue;
    if (p.tag === 'bowl3') continue; // 🍚 밥그릇3 관찰 종목 = 카드 없음 (종료 탭 옛 카드를 잡지 않게)
    const range = resolveCardRange(patched, p.code);
    if (!range) { skipped.push(`${name}(카드없음)`); continue; }
    const oldCard = patched.slice(range.start, range.end);
    if (!bannerRe.test(oldCard)) { skipped.push(`${name}(배너없음)`); continue; }

    const cw = String(p.change_won || '').trim();
    const cp = String(p.change_pct || '').trim();
    const neg = cw.startsWith('-') || cp.startsWith('-');
    const zero = !p.change_pct || parseFloat(p.change_pct) === 0;
    const cls = zero ? '' : (neg ? ' loss' : ' profit');
    // 괄호 부연설명은 .value-note / .hd-note 로 축소 (2026-07-23 전역 룰 · 재주입 시에도 스타일 유지)
    // 🐛 2026-08-07 수정: loadPrices()가 이미 change_pct에 부호를 보정해 넣는데(L141~148,
    //    toss_capture.py도 SSOT 단계에서 보정) 여기서 부호를 한 번 더 붙여 음봉이 「--1.98%」로
    //    이중 부호 표기됐다. 표시 단계에서는 크기만 쓰고 부호는 neg 판정으로 단일 부착한다.
    const pctAbs = cp.replace(/^[+-]/, '');
    const pct = pctAbs ? ` <span class="value-note">(${neg ? '-' : '+'}${pctAbs}%)</span>` : '';
    const ev = evalByCode[p.code]
      ? ` <span class="value-note">(평가 ${evalByCode[p.code]})</span>` : '';
    const newBanner = `<div class="summary-item"><div class="label">현재가 <span class="hd-note">(${mmdd})</span></div><div class="value${cls}">${p.price}원${pct}${ev}</div></div>`;

    const newCard = oldCard.replace(bannerRe, newBanner);
    if (newCard !== oldCard) {
      patched = patched.slice(0, range.start) + newCard + patched.slice(range.end);
      count++;
    }
  }
  log(`HTML 현재가 배너 자동 패치: ${count}건${skipped.length ? ' / skip: ' + skipped.join(', ') : ''}`);
  return patched;
}

// ───────────────────────────────────────────────────────────────────
// 🛡️ AI 추천전략 박스 자동 주입 (2026-08-05 신설 · AI단테.md §4.2)
// 활성 카드 매수 테이블(portfolio-table) 바로 아래에 auto_conservative_action을
// **슬랙 발행 원문 그대로** 2열 표로 주입한다(사용자 확정: "슬랙과 동일한 내용으로 기입").
// ⚠️ 문구는 절대 다듬지 않는다 — 절 구분자(· / →)로 쪼개 행 라벨만 붙인다
//    (§4.2 「산문 요약 블록 금지」 준수용 구조화이며 텍스트 자체는 무수정).
// ⚠️ 이 박스는 SSOT가 아니다 — 체결·손절·목표의 진실값은 §6 + 매수 테이블.
//    LLM이 당일 시세로 진입가를 리베이스해 카드와 어긋날 수 있고, 그래도 교정하지 않는다.
const STRATEGY_BOX_MARK = '<!-- 🛡️ AI 추천전략 박스 (자동 생성 · patchAiStrategyBoxes · 수기 편집 금지 — 다음 회차에 덮어씀) -->';

function escapeHtml(s) {
  return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

// 절 텍스트 → 행 라벨. 첫 번째 「일반 행동」 절에만 🎯를 주고 나머지 기본값은 ⚠️ 조건.
function labelClause(text, state) {
  if (/익절|트레일링/.test(text)) return '💰 익절';
  if (/금지|자제/.test(text)) return '🚫 금지';
  if (/이탈|컷|손절|폐기|제외|취소|소멸|하회/.test(text)) return '🛑 무효화';
  if (/경고|주의/.test(text)) return '⚠️ 경고';
  if (!state.baseUsed) { state.baseUsed = true; return '🎯 기본 행동'; }
  return '⚠️ 조건';
}

function buildStrategyBox(s, stamp) {
  const state = { baseUsed: false };
  const clauses = String(s.auto_conservative_action)
    .split(/\s+·\s+|\s+\/\s+|\s+→\s+/)
    .map(t => t.trim())
    .filter(Boolean);
  const row = (label, text) =>
    `        <tr><td style="text-align:left;width:120px;">${label}</td><td style="text-align:left;">${text}</td></tr>`;
  const rows = [
    row('🔔 판정', escapeHtml(`${s.decision || '판정 없음'} · ${s.signal || '시그널 없음'}`)),
    ...clauses.map(c => row(labelClause(c, state), escapeHtml(c))),
  ].join('\n');

  return `  ${STRATEGY_BOX_MARK}
  <div class="warn-box" style="background:#1e1a2e;border-left:4px solid #a371f7;margin-bottom:12px;">
    🛡️ <strong>AI 추천전략 <span class="hd-note">(슬랙 ${stamp} 원문 · claude_dante_analyze.js)</span></strong>
    <table style="margin:8px 0;font-size:0.88em;">
      <thead><tr><th>구분</th><th>내용</th></tr></thead>
      <tbody>
${rows}
      </tbody>
    </table>
  </div>
`;
}

function findAiStrategyBoxRanges(card) {
  const ranges = [];
  let cursor = 0;

  while (true) {
    const headingAt = card.indexOf('<strong>AI 추천전략', cursor);
    if (headingAt < 0) break;

    const previousEnd = ranges.length ? ranges[ranges.length - 1].end : -1;
    const divAt = card.lastIndexOf('<div class="warn-box"', headingAt);
    const markerAt = card.lastIndexOf(STRATEGY_BOX_MARK, headingAt);
    const innerTableEnd = card.indexOf('</table>', headingAt);
    const closeAt = innerTableEnd < 0 ? -1 : card.indexOf('</div>', innerTableEnd);
    if (divAt < previousEnd || innerTableEnd < 0 || closeAt < 0) {
      return { ranges, error: '기존 박스 경계 불명' };
    }

    const startAt = markerAt > previousEnd && markerAt < divAt ? markerAt : divAt;
    const start = card.lastIndexOf('\n', startAt) + 1;
    let end = closeAt + '</div>'.length;
    const nl = card.slice(end).match(/^\r?\n/);
    if (nl) end += nl[0].length;
    ranges.push({ start, end });
    cursor = end;
  }

  return { ranges, error: null };
}

function patchAiStrategyBoxes(html, analysis) {
  const stamp = `${analysis.date || TODAY} ${String(analysis.time || HHMM).slice(0, 2)}:${String(analysis.time || HHMM).slice(2)} 회차`;
  let patched = html;
  let replaced = 0, inserted = 0, duplicatesRemoved = 0, staleRemoved = 0;
  const skipped = [];

  for (const s of analysis.stocks || []) {
    const range = resolveCardRange(patched, s.code);
    if (!range) { skipped.push(`${s.name}(카드없음)`); continue; }

    const card = patched.slice(range.start, range.end);
    const found = findAiStrategyBoxRanges(card);
    if (found.error) { skipped.push(`${s.name}(${found.error})`); continue; }

    let newCard = card;
    for (let i = found.ranges.length - 1; i >= 0; i--) {
      const r = found.ranges[i];
      newCard = newCard.slice(0, r.start) + newCard.slice(r.end);
    }

    if (!s.auto_conservative_action) {
      if (found.ranges.length) {
        staleRemoved += found.ranges.length;
        patched = patched.slice(0, range.start) + newCard + patched.slice(range.end);
      }
      skipped.push(`${s.name}(추천전략 없음${found.ranges.length ? '·기존 삭제' : ''})`);
      continue;
    }

    // 대시보드는 CRLF 우세 — 파일의 지배적 줄바꿈에 맞춰 주입한다(혼합 줄바꿈 방지)
    const eol = (patched.match(/\r\n/g) || []).length > (patched.split('\n').length / 2) ? '\r\n' : '\n';
    const box = buildStrategyBox(s, stamp).replace(/\n/g, eol);
    // 기존 박스를 모두 제거한 뒤 portfolio-table(카드 첫 표) 직후에 최신 박스 1개만 삽입한다.
    // 수기 편입 당시 남은 구형 박스가 자동 박스 아래에 중복 노출되는 문제를 매 회차 정리한다.
    const tEnd = newCard.indexOf('</table>');
    if (tEnd < 0) { skipped.push(`${s.name}(매수 테이블 없음)`); continue; }
    let at = tEnd + '</table>'.length;
    const nl = newCard.slice(at).match(/^\r?\n/);
    if (nl) at += nl[0].length;
    newCard = newCard.slice(0, at) + box + newCard.slice(at);
    if (found.ranges.length) replaced++;
    else inserted++;
    duplicatesRemoved += Math.max(0, found.ranges.length - 1);
    patched = patched.slice(0, range.start) + newCard + patched.slice(range.end);
  }

  log(`HTML AI 추천전략 박스: 교체 ${replaced}건 / 신규 ${inserted}건 / 중복 제거 ${duplicatesRemoved}건 / 전략 누락 stale 제거 ${staleRemoved}건${skipped.length ? ' / skip: ' + skipped.join(', ') : ''}`);
  return patched;
}

function patchSummaryActionTable(html, analysis) {
  let patched = html;
  let count = 0;
  for (const s of analysis.stocks) {
    const rowRegex = new RegExp(
      `(card-${s.code}[^]*?<td data-label="4지 판정"[^>]*>)\\s*<strong[^>]*>[^<]*</strong>[^<]*(?=</td>)`,
      'm'
    );
    if (rowRegex.test(patched)) {
      // 💰 절반 익절 / 🛡️ 트레일링 전환 = 2026-08-20 §4.4-D 신설분 (활성 6지).
      // 두 판정을 빠뜨리면 else 로 떨어져 손절색(#f85149)으로 칠해지므로 반드시 유지할 것.
      const color = s.decision.startsWith('🟢') ? '#3fb950'
        : s.decision.startsWith('💰') ? '#2ea043'
        : s.decision.startsWith('🛡️') ? '#58a6ff'
        : s.decision.startsWith('🟡') ? '#f0883e'
        : s.decision.startsWith('⏸') ? '#d29922'
        : '#f85149';
      patched = patched.replace(rowRegex,
        `$1<strong style="color:${color};">${s.decision} 🤖 AI 자동 판정 (검토 필요)</strong>`
      );
      count++;
    }
  }
  log(`HTML 4지 판정 패치: ${count}/${analysis.stocks.length}건`);
  return patched;
}

const ENTRY_GATE_SUMMARY_START = '<!-- AUTO:ENTRY_GATE_SUMMARY:START -->';
const ENTRY_GATE_SUMMARY_END = '<!-- AUTO:ENTRY_GATE_SUMMARY:END -->';
const ENTRY_GATE_LABELS = {
  price: '가격',
  bullish_candle: '양봉',
  volume: '거래량',
  foreign_net_buy: '외국인',
  program_net_buy: '프로그램',
};

function renderEntryGateStatus(status) {
  const normalized = normalizeConditionStatus(status);
  const label = normalized === 'pass' ? 'PASS' : normalized === 'fail' ? 'FAIL' : 'UNKNOWN';
  const color = normalized === 'pass' ? '#3fb950' : normalized === 'fail' ? '#f85149' : '#8b949e';
  return `<td style="text-align:center;"><strong style="color:${color};">${label}</strong></td>`;
}

function formatGateNumber(value) {
  const number = parseSignedNumber(value);
  return number === null ? null : Number(number).toLocaleString();
}

function renderEntryGateSummary(analysis) {
  const source = (Array.isArray(analysis && analysis.stocks) ? analysis.stocks : []).filter(s => s.category !== 'bowl3');
  const rows = source.map((stock, index) => {
    const gate = stock.entry_conditions && typeof stock.entry_conditions === 'object'
      ? stock.entry_conditions : {};
    const statuses = Object.fromEntries(
      REQUIRED_ENTRY_CONDITIONS.map(key => [key, normalizeConditionStatus(gate[key])])
    );
    const passCount = REQUIRED_ENTRY_CONDITIONS.filter(key => statuses[key] === 'pass').length;
    const holding = stock.position && stock.position.status === 'holding';
    return { stock, gate, statuses, passCount, holding, index };
  }).sort((a, b) => b.passCount - a.passCount || a.index - b.index);

  const eligible = rows.filter(row => row.passCount === REQUIRED_ENTRY_CONDITIONS.length);
  const unfilled = rows.filter(row => !row.holding);
  const maxUnfilledPass = unfilled.length ? Math.max(...unfilled.map(row => row.passCount)) : 0;
  const watch = unfilled.filter(row => row.passCount === maxUnfilledPass);
  const foreignPassed = rows.filter(row => row.statuses.foreign_net_buy === 'pass').length;
  const stamp = `${escapeHtml(analysis.date || '')} ${escapeHtml(
    analysis.time ? `${String(analysis.time).slice(0, 2)}:${String(analysis.time).slice(2)}` : ''
  )}`.trim();

  const tableRows = rows.map(({ stock, statuses, passCount, holding }) => {
    const rowStyle = passCount === 5 ? ' style="background:#0d1f0d;"' : '';
    return `      <tr${rowStyle}>
        <td><strong>${escapeHtml(stock.name || stock.code || 'UNKNOWN')}</strong></td>
        <td style="text-align:center;">${holding ? '보유' : '미체결'}</td>
        <td style="text-align:right;">${escapeHtml(stock.current_price || 'UNKNOWN')}</td>
        ${renderEntryGateStatus(statuses.price)}
        ${renderEntryGateStatus(statuses.bullish_candle)}
        ${renderEntryGateStatus(statuses.volume)}
        ${renderEntryGateStatus(statuses.foreign_net_buy)}
        ${renderEntryGateStatus(statuses.program_net_buy)}
        <td style="text-align:center;"><strong>${passCount}/5</strong></td>
      </tr>`;
  }).join('\n');

  const watchRows = watch.map(({ stock, statuses, passCount }) => {
    const missing = REQUIRED_ENTRY_CONDITIONS
      .filter(key => statuses[key] !== 'pass')
      .map(key => ENTRY_GATE_LABELS[key])
      .join(', ');
    return `      <tr><td><strong>${escapeHtml(stock.name || stock.code || 'UNKNOWN')}</strong></td><td>${escapeHtml(missing || '없음')}</td><td style="text-align:center;">${passCount === 5 ? '매수 검토' : '매수 대기'}</td></tr>`;
  }).join('\n');

  const watchNotes = watch.flatMap(({ stock, gate, statuses }) => {
    const notes = [];
    if (statuses.volume !== 'pass') {
      const actual = formatGateNumber(gate.volume && gate.volume.actual);
      const minimum = formatGateNumber(gate.volume && gate.volume.minimum);
      if (actual !== null && minimum !== null) {
        notes.push(`${stock.name} 거래량: ${actual}주, 최소 ${minimum}주 미달`);
      }
    }
    return notes;
  });
  watchNotes.push(`외국인 순매수 조건 PASS: ${foreignPassed}/${rows.length}종`);
  watchNotes.push(`결론: 5/5 충족 ${eligible.length}종 - ${eligible.length ? '매수 검토 필요' : '신규매수 및 추가매수 없음'}`);

  return `${ENTRY_GATE_SUMMARY_START}
<section id="entry-gate-summary">
  <h2 style="color:#3fb950;">진입조건 5개 충족 현황 <span class="hd-note">(${stamp || 'UNKNOWN'} 분석)</span></h2>
  <div class="card">
    <div class="summary-grid" style="margin-top:0;">
      <div class="summary-item"><div class="label">5/5 매수 가능</div><div class="value" style="color:${eligible.length ? '#3fb950' : '#f85149'};">${eligible.length}종</div></div>
      <div class="summary-item"><div class="label">미체결 최고 충족</div><div class="value" style="color:#d29922;">${maxUnfilledPass}/5</div></div>
      <div class="summary-item"><div class="label">외국인 순매수 PASS</div><div class="value">${foreignPassed}/${rows.length}종</div></div>
    </div>
    <div style="overflow-x:auto;">
      <table class="stock-table" style="font-size:0.9em;min-width:860px;">
        <thead><tr><th>종목</th><th>구분</th><th>현재가</th><th>가격</th><th>양봉</th><th>거래량</th><th>외국인</th><th>프로그램</th><th>충족</th></tr></thead>
        <tbody>
${tableRows}
        </tbody>
      </table>
    </div>
    <h3 style="margin-top:20px;color:#d29922;">최상위 관찰 종목</h3>
    <table class="stock-table" style="font-size:0.9em;">
      <thead><tr><th>종목</th><th>미충족 조건</th><th>판정</th></tr></thead>
      <tbody>
${watchRows || '      <tr><td colspan="3" style="text-align:center;">미체결 종목 없음</td></tr>'}
      </tbody>
    </table>
    <ul style="margin:12px 0 0 20px;color:#8b949e;font-size:0.88em;">
${watchNotes.map(note => `      <li>${escapeHtml(note)}</li>`).join('\n')}
    </ul>
  </div>
</section>
${ENTRY_GATE_SUMMARY_END}`;
}

function patchEntryGateSummary(html, analysis) {
  const rendered = renderEntryGateSummary(analysis);
  const start = html.indexOf(ENTRY_GATE_SUMMARY_START);
  const end = html.indexOf(ENTRY_GATE_SUMMARY_END);
  if (start >= 0 && end >= start) {
    return html.slice(0, start) + rendered + html.slice(end + ENTRY_GATE_SUMMARY_END.length);
  }
  const tabMarker = '<div class="tab-content" id="tab-summary">';
  const tabAt = html.indexOf(tabMarker);
  if (tabAt < 0) throw new Error('요약 탭 마커 없음: #tab-summary');
  const insertAt = tabAt + tabMarker.length;
  return html.slice(0, insertAt) + `\n\n${rendered}` + html.slice(insertAt);
}

// theme_flow 폴백: Claude recommend-only 출력이 비었거나 error여도 HTML 추천 섹션(통과/이탈)이
// 항상 갱신되도록 collect_theme_flow.py 정량 결과를 recommendations 형태로 채운다.
// (slack_send.js buildRecommendBlocks의 폴백과 동일 정책 — HTML/Slack 데이터 정합)
function applyRecommendFallback(analysis, themeFlow) {
  if (!themeFlow) return;
  const recs = analysis.recommendations || (analysis.recommendations = {});
  const candidates = Array.isArray(themeFlow.candidates) ? themeFlow.candidates : [];
  const dropped = Array.isArray(themeFlow.dropped) ? themeFlow.dropped : [];
  const promoted = (v) => v && v !== 'null' && v !== 'none';
  const won = (n) => (n != null ? Number(n).toLocaleString() : '-');

  if (!Array.isArray(recs.top3) || recs.top3.length === 0) {
    recs.top3 = candidates.map(c => ({
      code: c.code, name: c.name, market: c.market,
      stars: c.stars, dante_pullback_score: c.score,
      promoted: c.promoted, first_seen: c.first_seen,
      diagnosis: `종가 ${won(c.close_today)} · 224 돌파 후 첫 눌림 정합 통과 (score ${c.score})${promoted(c.promoted) ? ' · ✅ 후보/활성 편입됨' : ''}`,
    }));
  }
  if (!Array.isArray(recs.dropped) || recs.dropped.length === 0) {
    recs.dropped = dropped.map(d => ({
      code: d.code, name: d.name, market: d.market,
      reason_code: d.reason_code, prev_score: d.score,
      consecutive_drop_days: d.consecutive_drop_days,
      narration: `${d.reject || d.reason_code || '조건 이탈'}${d.close_today != null ? ` (종가 ${won(d.close_today)})` : ''}`,
    }));
  }
  // 폴백 데이터로 채워졌으면 '후보 없음' error는 제거 (HTML 렌더 차단 방지)
  if ((recs.top3.length || recs.dropped.length) && recs.error) delete recs.error;
  if (!recs.headline && (recs.top3.length || recs.dropped.length)) {
    recs.headline = `영속 추천 풀 — 통과 ${recs.top3.length}종 · 이탈 추적 ${recs.dropped.length}종 (재평가 후 신규 서칭)`;
  }
}

function runRecommendOnly() {
  log(`=== 추천 전용 모드 (${TODAY} ${HHMM} KST) — 08:15 밥그릇 스캐너 회차 ===`);
  const meta = loadStocksMeta();
  const flow = loadFlowData();
  if (!flow.theme) {
    log(`!!! theme_flow_${TODAY}.json 없음. collect_theme_flow.py 선실행 필요.`);
    process.exit(1);
  }
  log(`밥그릇 후보 ${(flow.theme.candidates || []).length}종 수집됨. Claude 정성 판단 호출.`);

  // 기존 보유·후보 추세 점검 (collect_theme_flow.py가 전일 OHLCV로 산출)
  const holdingsTrend = Array.isArray(flow.theme.holdings_trend) ? flow.theme.holdings_trend : [];
  const brokeNames = holdingsTrend.filter(h => h.trend_break).map(h => h.name);
  log(`기존 종목 추세 점검: ${holdingsTrend.length}종 · 추세 이탈 ${brokeNames.length}종${brokeNames.length ? ' (' + brokeNames.join(', ') + ')' : ''}`);

  // 추천 결과 패치 + 저장 헬퍼 (후보 유무와 무관하게 추세 점검은 항상 발행)
  const finalize = (analysis) => {
    analysis.mode = 'recommend-only';
    analysis.date = TODAY;
    analysis.time = HHMM;
    analysis.analyzed_at = new Date(Date.now() + 9 * 60 * 60 * 1000).toISOString();
    analysis.theme_flow_data = flow.theme;
    analysis.holdings_trend = holdingsTrend;
    // 💥 돌파 스캐너 결과 로드 (collect_breakout_flow.py [B1b] 산출) → 돌파 탭 자동 패치용
    try {
      const bfPath = path.join(DATA_DIR, `breakout_flow_${TODAY}.json`);
      if (fs.existsSync(bfPath)) {
        analysis.breakout_flow_data = JSON.parse(fs.readFileSync(bfPath, 'utf-8'));
        log(`💥 breakout_flow 로드: active ${(analysis.breakout_flow_data.active || []).length}종`);
      } else {
        log(`(breakout_flow_${TODAY}.json 없음 — 돌파 탭 패치 생략)`);
      }
    } catch (e) { log(`breakout_flow 로드 실패: ${e.message}`); }
    // 🧭 테마 로테이션 로드 (collect_theme_rotation.py [B1e] 산출) → 테마 탭 자동 패치용
    try {
      const trPath = path.join(DATA_DIR, `theme_rotation_${TODAY}.json`);
      if (fs.existsSync(trPath)) {
        analysis.theme_rotation_data = JSON.parse(fs.readFileSync(trPath, 'utf-8'));
        log(`🧭 theme_rotation 로드: 길목 ${(analysis.theme_rotation_data.picks || []).length}종`);
      } else {
        log(`(theme_rotation_${TODAY}.json 없음 — 테마 탭 패치 생략)`);
      }
    } catch (e) { log(`theme_rotation 로드 실패: ${e.message}`); }
    applyRecommendFallback(analysis, flow.theme);   // Claude 출력이 비어도 HTML 통과/이탈 항상 갱신
    fs.writeFileSync(OUT_JSON, JSON.stringify(analysis, null, 2), 'utf-8');
    log(`추천 결과 저장: ${OUT_JSON}`);

    const html = fs.readFileSync(HTML_PATH, 'utf-8');
    const patched = patchFlowSections(html, analysis);
    if (patched !== html) {
      fs.writeFileSync(HTML_PATH, patched, 'utf-8');
      log(`HTML 추천 섹션 패치 완료: ${HTML_PATH}`);
    }
  };

  const prompt = buildRecommendOnlyPrompt(meta, flow.theme);
  if (!prompt) {
    log(`!!! 신규 후보 0개 — Claude 호출 생략. 추천 카드 유지 + 기존 종목 추세 점검만 발행.`);
    if (fs.existsSync(OUT_JSON)) fs.unlinkSync(OUT_JSON);
    finalize({ recommendations: { error: '후보 없음 (정량 필터 통과 0개)' } });
    log(`=== 추천 전용 모드 완료 (추세 점검 ${holdingsTrend.length}종) ===`);
    return;
  }

  if (DRY_RUN) {
    log(`--dry-run: Claude CLI 호출 생략. 프롬프트 길이 ${prompt.length} bytes.`);
    if (!fs.existsSync(TEMP_DIR)) fs.mkdirSync(TEMP_DIR, { recursive: true });
    fs.writeFileSync(path.join(TEMP_DIR, `recommend_prompt_preview_${HHMM}.txt`), prompt, 'utf-8');
    return;
  }

  if (fs.existsSync(OUT_JSON)) fs.unlinkSync(OUT_JSON);
  const analysis = callClaudeCodeRecommend(prompt);
  finalize(analysis);
  log(`=== 추천 전용 모드 완료 ===`);
}

function callClaudeCodeRecommend(prompt) {
  if (!fs.existsSync(TEMP_DIR)) fs.mkdirSync(TEMP_DIR, { recursive: true });
  fs.writeFileSync(path.join(TEMP_DIR, `recommend_prompt_${HHMM}.txt`), prompt, 'utf-8');

  for (let attempt = 1; attempt <= MAX_RETRIES; attempt++) {
    try {
      log(`Claude Code CLI 호출 [recommend-only] (시도 ${attempt}/${MAX_RETRIES})`);
      const res = runClaudeCli(prompt, `recommend_${HHMM}`, 'Read,Write', 12);
      if (res.timedOut) {
        log(`  ⚠ Claude 호출 타임아웃(10분) → 프로세스 트리 강제 종료 (시도 ${attempt}/${MAX_RETRIES})`);
      }
      if (fs.existsSync(OUT_JSON)) {
        const parsed = JSON.parse(fs.readFileSync(OUT_JSON, 'utf-8'));
        if (parsed.recommendations) {
          log(`  추천 결과 검증 OK`);
          return parsed;
        }
      }
      log(`  ⚠ recommendations 키 없음 (시도 ${attempt})`);
    } catch (err) {
      log(`  Claude CLI 오류 (시도 ${attempt}): ${err.message?.slice(0, 200)}`);
    }
  }
  throw new Error('Claude Code CLI 호출 실패 (recommend-only)');
}

// 시세 전용 패치 모드: Claude 호출 없이 prices_{TODAY}.json을 카드 4배너 현재가에 주입.
// 자동 파이프라인이 캡처 후 곧바로 호출하거나, 수동 시세 보정용으로 단독 실행 가능.
function runPricesOnly() {
  log(`=== 시세 전용 배너 패치 모드 (${TODAY}) — Claude 호출 없음 ===`);
  const prices = loadPrices();
  if (!Object.keys(prices).length) {
    log(`!!! prices_${TODAY}.json 없음/빈값 — toss_capture.py 선실행 필요. 중단.`);
    process.exit(1);
  }
  const html = fs.readFileSync(HTML_PATH, 'utf-8');
  const patched = patchCardPriceBanners(html, prices, null);
  if (patched !== html) {
    fs.writeFileSync(HTML_PATH, patched, 'utf-8');
    log(`HTML 현재가 배너 패치 완료: ${HTML_PATH} (node upload_dashboard.js 로 업로드 필요)`);
  } else {
    log(`변경 없음 (배너 이미 최신).`);
  }
}

function runNormalizeLatest() {
  const files = fs.readdirSync(DATA_DIR)
    .filter(name => /^dante_analysis_\d{4}-\d{2}-\d{2}_\d{4}\.json$/.test(name))
    .sort((a, b) => a.localeCompare(b));
  if (!files.length) throw new Error('최신 분석 JSON 없음');
  const file = files[files.length - 1];
  const fullPath = path.join(DATA_DIR, file);
  const analysis = JSON.parse(fs.readFileSync(fullPath, 'utf8'));
  const meta = loadStocksMeta();
  const prices = loadPrices();
  const rules = loadEntryRuleMap();
  for (const s of analysis.stocks || []) {
    const gate = normalizeEntryConditions(
      s,
      String(s.entry_conditions?.evaluated_at || `${TODAY} ${HHMM} KST`),
      prices[s.name] || {},
      rules.get(s.code) || {},
    );
    normalizeDecisionForPosition(s, meta.positions.get(s.code));
    log(`진입조건 재검증: ${s.name}(${s.code}) ${gate.all_required_met ? 'PASS' : 'WAIT'} ${gate.required_pass_count}/${gate.required_total}`);
  }
  fs.writeFileSync(fullPath, JSON.stringify(analysis, null, 2), 'utf8');
  const html = fs.readFileSync(HTML_PATH, 'utf8');
  const patched = patchEntryGateSummary(html, analysis);
  if (patched !== html) fs.writeFileSync(HTML_PATH, patched, 'utf8');
  log(`최신 분석 JSON 조건 재검증 저장: ${file}`);
}

function runRefreshEntrySummary() {
  const files = fs.readdirSync(DATA_DIR)
    .filter(name => /^dante_analysis_\d{4}-\d{2}-\d{2}_\d{4}\.json$/.test(name))
    .sort((a, b) => a.localeCompare(b));
  if (!files.length) throw new Error('최신 분석 JSON 없음');
  const file = files[files.length - 1];
  const analysis = JSON.parse(fs.readFileSync(path.join(DATA_DIR, file), 'utf8'));
  const html = fs.readFileSync(HTML_PATH, 'utf8');
  const patched = patchEntryGateSummary(html, analysis);
  fs.writeFileSync(HTML_PATH, patched, 'utf8');
  log(`요약 탭 진입조건 표 갱신: ${file}`);
}

function main() {
  if (REFRESH_ENTRY_SUMMARY) {
    runRefreshEntrySummary();
    return;
  }
  if (NORMALIZE_LATEST) {
    runNormalizeLatest();
    return;
  }
  if (PRICES_ONLY) {
    runPricesOnly();
    return;
  }
  if (RECOMMEND_ONLY) {
    runRecommendOnly();
    return;
  }

  log(`=== Claude Code 단테 분석 시작 (${TODAY} ${HHMM} KST) ===`);

  const meta = loadStocksMeta();
  const prices = loadPrices();
  const images = loadChartImagePaths();
  const flowImages = loadFlowImagePaths();
  const flow = loadFlowData();

  const flowImgCount = Object.values(flowImages).reduce((n, k) => n + Object.keys(k).length, 0);
  log(`종목: ${meta.stocks.length}종 / 가격 추출: ${Object.keys(prices).length}종 / 차트 이미지: ${Object.keys(images).length}장 / 투자자공매: ${flowImgCount}장 (${Object.keys(flowImages).length}종)`);
  log(`보조지표: holdings_flow=${flow.holdings ? 'OK' : '없음'} / theme_flow=${flow.theme ? 'OK' : '없음'}`);

  if (Object.keys(images).length === 0) {
    log(`!!! 오늘(${TODAY}) 차트 이미지·API 수치 파일 없음. toss_api_snapshot.py 선실행 필요.`);
    process.exit(1);
  }

  const prompt = buildPrompt(images, prices, meta, flow, flowImages);

  if (DRY_RUN) {
    log(`--dry-run: Claude Code CLI 호출 생략. 프롬프트 점검.`);
    log(`프롬프트 길이: ${prompt.length} bytes`);
    log(`이미지 경로 ${Object.keys(images).length}개: ${Object.keys(images).join(', ')}`);
    log(`투자자공매 ${flowImgCount}장: ${Object.keys(flowImages).join(', ')}`);
    log(`가격 ${Object.keys(prices).length}종: ${Object.entries(prices).map(([k,v]) => k+':'+v.price).join(' / ')}`);
    if (!fs.existsSync(TEMP_DIR)) fs.mkdirSync(TEMP_DIR, { recursive: true });
    const previewFile = path.join(TEMP_DIR, `dante_prompt_preview_${HHMM}.txt`);
    fs.writeFileSync(previewFile, prompt, 'utf-8');
    log(`프롬프트 미리보기 저장: ${previewFile}`);
    return;
  }

  // 이전 결과 제거 (재시도 시 stale 방지)
  if (fs.existsSync(OUT_JSON)) fs.unlinkSync(OUT_JSON);

  const analysis = callClaudeCode(prompt);
  analysis.analyzed_at = new Date(Date.now() + 9*60*60*1000).toISOString();
  analysis.date = TODAY;
  analysis.time = HHMM;
  analysis.model = 'claude-code-cli';
  // 보조지표 raw 데이터를 분석 결과에 포함 (HTML 렌더 + 추적용)
  if (flow.holdings) analysis.holdings_flow_data = flow.holdings;
  if (flow.theme) analysis.theme_flow_data = flow.theme;

  // 추천종목(스캐너 영속 추천 풀 출신, stocks.json source==='recommend') → recommend_sourced 주입
  // Slack 추천/후보 섹션 분리용 (§4.6.16): 추천종목은 활성추천일 때만 '추천'에, 아니면 슬랙 미노출(대시보드 전담)
  const recommendCodes = new Set((meta.stocks || []).filter(s => s.source === 'recommend').map(s => s.code));
  // category는 LLM이 정할 값이 아니라 stocks.json tag(SSOT)를 그대로 따른다. LLM이 활성 종목을
  // 임의로 candidate로 강등(추세 약화 오판 등)하면 verify_sync 게이트가 발행을 차단하므로, 발행 전에
  // SSOT tag로 강제 교정한다. 후보 분류 폐지(2026-06-11) 상태에선 전 종목 active로 수렴.
  // 강등된 종목의 decision이 후보 전용 문구면 활성 4지로 정규화(⏸ 보류는 양쪽 공통이라 그대로 둠).
  const tagByCode = new Map((meta.stocks || []).map(s => [s.code, s.tag]));
  const entryRuleMap = loadEntryRuleMap();
  const apiSnap = fs.existsSync(API_SNAPSHOT_PATH) ? (JSON.parse(fs.readFileSync(API_SNAPSHOT_PATH, 'utf-8')).stocks || {}) : {};
  // 밥그릇3 관찰 종목은 카드가 없으므로 관찰 목록의 거래량 범위를 진입 규칙으로 쓴다
  for (const s of meta.stocks || []) {
    if (s.tag === 'bowl3' && s.vol && !entryRuleMap.has(s.code)) entryRuleMap.set(s.code, { volumeMinimum: s.vol[0], volumeMaximum: s.vol[1], executionStrengthThreshold: null });
  }
  const normalizeDecisionToActive = (d) => {
    if (/활성추천/.test(d)) return '🟢 매수 준비';
    if (/후보\s*풀\s*유지/.test(d)) return '🟡 보유 유지';
    if (/후보제외/.test(d)) return '🚫 손절 검토';
    return d; // ⏸ 보류 (관망) 등 활성·후보 공통 문구는 유지
  };
  for (const s of analysis.stocks || []) {
    const ssotTag = tagByCode.get(s.code);
    if (ssotTag && s.category !== ssotTag) {
      log(`⚠ category 교정(SSOT): ${s.name}(${s.code}) ${s.category} → ${ssotTag}`);
      if (ssotTag === 'active') {
        const nd = normalizeDecisionToActive(s.decision || '');
        if (nd !== s.decision) { log(`   decision 정규화: "${s.decision}" → "${nd}"`); s.decision = nd; }
      }
      s.category = ssotTag;
    }
    if (recommendCodes.has(s.code)) s.recommend_sourced = true;
    applyApiSnapshot(s, apiSnap[s.name]);
    const gate = normalizeEntryConditions(
      s,
      `${TODAY} ${HHMM} KST`,
      prices[s.name] || {},
      entryRuleMap.get(s.code) || {},
    );
    normalizeDecisionForPosition(s, meta.positions.get(s.code));
    log(`진입조건 게이트: ${s.name}(${s.code}) ${gate.all_required_met ? 'PASS' : 'WAIT'} ${gate.required_pass_count}/${gate.required_total}`);
    const q = loadQualityScreen(s.code);   // 📋 재무 우량주 심사 (screen_quality_value.py 산출, 있을 때만)
    if (q) s.quality_grade = q;
  }

  fs.writeFileSync(OUT_JSON, JSON.stringify(analysis, null, 2), 'utf-8');
  log(`분석 결과 저장: ${OUT_JSON}`);

  // HTML 패치
  const html = fs.readFileSync(HTML_PATH, 'utf-8');
  // 카드 패치는 라인업 종목만 — 🍚 밥그릇3 관찰 종목은 카드가 없고, 정규식이 다른 카드를 잘못 잡을 수 있다
  const cardAnalysis = { ...analysis, stocks: (analysis.stocks || []).filter(s => s.category !== 'bowl3') };
  let patched = patchEntryGateSummary(html, cardAnalysis);
  patched = patchSummaryActionTable(patched, cardAnalysis);
  patched = patchCardBodyHeaders(patched, cardAnalysis);
  patched = patchCardPriceBanners(patched, prices, cardAnalysis);  // 4배너 현재가 자동 주입
  patched = patchAiStrategyBoxes(patched, cardAnalysis);           // 🛡️ AI 추천전략 박스 (슬랙 원문 그대로)
  patched = patchFlowSections(patched, cardAnalysis);
  if (patched !== html) {
    fs.writeFileSync(HTML_PATH, patched, 'utf-8');
    log(`HTML 패치 완료: ${HTML_PATH}`);
  } else {
    log(`HTML 변경 없음 (패치 패턴 미매치 — 메일 본문에는 분석 결과 정상 포함)`);
  }

  appendPredictions(analysis);

  log(`=== 분석 완료 ===`);
}

if (require.main === module) {
  try {
    main();
  } catch (e) {
    console.error('치명적 오류:', e.message);
    process.exit(1);
  }
}

// 테스트·외부 호출용 export (require 시 main 자동 실행 안 함 — 위 require.main 가드)
module.exports = { runClaudeCli, patchCardPriceBanners, resolveCardRange, patchCardBodyHeaders, patchAiStrategyBoxes, buildStrategyBox, patchFlowSections, renderRecommendationsSection, renderHoldingsTrendSection, renderBreakoutSection, renderThemeSection, renderScannerEval, normalizeEntryConditions, loadEntryRuleMap, parsePositionMap, normalizeDecisionForPosition, renderEntryGateSummary, patchEntryGateSummary };
