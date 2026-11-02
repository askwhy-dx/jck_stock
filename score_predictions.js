/**
 * score_predictions.js — AI 판정 적중률 채점 시스템 (시뮬레이션)
 *
 * "AI가 내린 4지 판정(🟢🟡⏸🚫)이 맞았나"를 과거 KRX 종가로 채점한다.
 *
 *   [A] 적재  : dante_analysis_*.json → predictions 원장 (--from-analysis)
 *   [C] 조회  : krx_ohlcv_cache/ 에서 진입일 +N거래일 시세 경로 수집
 *   [D] 채점  : 판정 → 정답 규칙 적용 (맞음/틀림 + 실제 수익률)
 *   [E] 집계  : 전체 / 판정별 / 조건별 적중률
 *
 * 두 가지 함정 처리:
 *   ① 거래일 카운트 — 달력일이 아니라 "캐시에 파일이 존재하는 날짜" 시퀀스로 +N
 *   ② 시장 자동판별 — code를 KOSPI·KOSDAQ 캐시 양쪽에서 찾고 어느 시장인지 기록
 *
 * 사용:
 *   node score_predictions.js            # (기본) 원장 채점 — predictions.jsonl 대기분 중 윈도우 경과분 채점 → scorecard.json
 *   node score_predictions.js --backfill # 기존 dante_analysis_*.json 을 원장에 적재 후 채점
 *   node score_predictions.js --simulate # 과거 기준일 합성 데모 (채점 엔진·함정 검증)
 *
 * 스윙 채점 윈도우 = 진입 판정일 + 10거래일 (2026-05-27 변경, 기존 20 → 10).
 */
const fs = require('fs');
const path = require('path');

const ROOT = __dirname;
const CACHE_DIR = path.join(ROOT, 'data', 'krx_ohlcv_cache');
const DATA_DIR = path.join(ROOT, 'data');

const argv = process.argv.slice(2);
const SIMULATE = argv.includes('--simulate');   // 과거 기준일 합성 데모
const BACKFILL = argv.includes('--backfill');    // 기존 dante_analysis_*.json → 원장 적재

// 채점 파라미터
const WINDOW = { '스윙': 10, '중장기': 10 };  // 거래일 (스윙·중장기 모두 +10, 2026-05-27 변경)
const TARGET = 0.05;  // 🟢 익절 기준 +5%
const STOP = 0.03;    // 🟢 손절 기준 -3%
const HOLD_FLOOR = 0.03;  // 🟡 방어 -3% 이내
const WAIT_CAP = 0.05;    // ⏸ 큰 상승 안 놓침 +5% 이내

// ───────────────────────────────────────────────────────────
// 함정 ① : 거래일 시퀀스 = 캐시 파일이 존재하는 날짜만 (휴장일 자동 제외)
// ───────────────────────────────────────────────────────────
function loadTradingDates() {
  const set = new Set();
  for (const f of fs.readdirSync(CACHE_DIR)) {
    const m = f.match(/^(\d{8})_(KOSPI|KOSDAQ)\.json$/);
    if (m) set.add(m[1]);
  }
  return [...set].sort();  // "YYYYMMDD" 문자열 정렬 = 시간순
}

function forwardTradingDate(dates, anchor, n) {
  const i = dates.indexOf(anchor);
  if (i < 0 || i + n >= dates.length) return null;
  return dates[i + n];
}

// ───────────────────────────────────────────────────────────
// 함정 ② : 시장 구분 없이 code로 조회. 양쪽 캐시 병합 + market 기록
// ───────────────────────────────────────────────────────────
const _dayCache = new Map();
function loadDay(date) {
  if (_dayCache.has(date)) return _dayCache.get(date);
  const map = new Map();
  for (const mkt of ['KOSPI', 'KOSDAQ']) {
    const fp = path.join(CACHE_DIR, `${date}_${mkt}.json`);
    if (!fs.existsSync(fp)) continue;
    const j = JSON.parse(fs.readFileSync(fp, 'utf-8'));
    for (const r of j.rows) {
      map.set(r.code, { code: r.code, name: r.name, market: mkt,
        close: r.close, high: r.high, low: r.low, volume: r.volume, fluc_pct: r.fluc_pct });
    }
  }
  _dayCache.set(date, map);
  return map;
}

// ───────────────────────────────────────────────────────────
// [D] 채점 — 판정별 정답 규칙
// ───────────────────────────────────────────────────────────
function scoreOne(pred, dates) {
  const N = WINDOW[pred.type] || 20;
  const i = dates.indexOf(pred.date_compact);
  if (i < 0) return { status: 'pending', reason: '진입일이 캐시에 없음' };
  if (i + N >= dates.length) return { status: 'pending', reason: `채점 윈도우 미경과 (+${N}거래일 데이터 없음)` };

  const P = pred.entry_price;
  const scoreDate = dates[i + N];

  // 경로 스캔 (D+1 ~ D+N) — 🟢 의 익절/손절 선후 판정
  let hitTarget = false, hitStop = false, eventDay = null;
  for (let k = 1; k <= N; k++) {
    const row = loadDay(dates[i + k]).get(pred.code);
    if (!row) continue;
    if (row.low <= P * (1 - STOP)) { hitStop = true; eventDay = dates[i + k]; break; }  // 보수적: 손절 우선
    if (row.high >= P * (1 + TARGET)) { hitTarget = true; eventDay = dates[i + k]; break; }
  }

  const exitRow = loadDay(scoreDate).get(pred.code);
  if (!exitRow) return { status: 'pending', reason: '채점일 시세 없음(거래정지 등)' };
  const exit = exitRow.close;
  const ret = (exit - P) / P * 100;

  let hit;
  const d = pred.decision;
  if (d.startsWith('🟢')) hit = hitTarget ? true : (hitStop ? false : exit > P);
  else if (d.startsWith('🚫')) hit = exit < P;            // 피한 게 맞음
  // 💰 절반 익절 / 🛡️ 트레일링 전환 (2026-08-20 §4.4-D 신설) = 🟡 과 동일한 「방어」 축으로 채점.
  // 절반은 이미 실현됐고 잔량은 MA20 트레일링이라, 성패는 상승폭이 아니라 잔량 방어 여부다.
  // ⚠️ 이 분기를 빼면 두 판정이 마지막 else(⏸ 큰 상승 안 놓침 = exit <= P*1.05)로 떨어져
  //    익절 후 주가가 오를수록 "실패"로 뒤집혀 채점된다.
  else if (d.startsWith('🟡') || d.startsWith('💰') || d.startsWith('🛡️')) hit = exit >= P * (1 - HOLD_FLOOR);
  else hit = exit <= P * (1 + WAIT_CAP);                  // ⏸ 큰 상승 안 놓침

  return {
    status: 'scored', score_date: scoreDate, exit_price: exit,
    ret_pct: +ret.toFixed(2), hit,
    path: hitTarget ? '익절선 먼저' : hitStop ? '손절선 먼저' : '미발동',
    event_day: eventDay,
  };
}

// ───────────────────────────────────────────────────────────
// [E] 집계
// ───────────────────────────────────────────────────────────
function groupBy(scored, keyFn) {
  const g = {};
  for (const r of scored) {
    const k = keyFn(r);
    if (!g[k]) g[k] = { n: 0, hit: 0, ret: 0 };
    g[k].n++; g[k].hit += r.hit ? 1 : 0; g[k].ret += r.ret_pct;
  }
  return Object.fromEntries(Object.entries(g).map(([k, v]) =>
    [k, { n: v.n, hit_rate: +(v.hit / v.n * 100).toFixed(1), avg_ret: +(v.ret / v.n).toFixed(2) }]));
}

function aggregate(rows) {
  const scored = rows.filter(r => r.status === 'scored');
  return {
    n_total: rows.length,
    n_scored: scored.length,
    overall: scored.length ? {
      hit_rate: +(scored.filter(r => r.hit).length / scored.length * 100).toFixed(1),
      avg_ret: +(scored.reduce((s, r) => s + r.ret_pct, 0) / scored.length).toFixed(2),
    } : null,
    by_decision: groupBy(scored, r => r.decision),
    by_bar: groupBy(scored, r => r.bar),
    by_vol_band: groupBy(scored, r => r.vol_band),
  };
}

// ───────────────────────────────────────────────────────────
// [A] 적재 — 실제 dante_analysis_*.json → 원장
// ───────────────────────────────────────────────────────────
function buildLedgerFromAnalysis() {
  const preds = [];
  for (const f of fs.readdirSync(DATA_DIR)) {
    const m = f.match(/^dante_analysis_(\d{4}-\d{2}-\d{2})_(\d{4})\.json$/);
    if (!m) continue;
    const j = JSON.parse(fs.readFileSync(path.join(DATA_DIR, f), 'utf-8'));
    if (!Array.isArray(j.stocks)) continue;
    const date = j.date || m[1], time = j.time || m[2];  // JSON 내부값 없으면 파일명 사용
    for (const s of j.stocks) {
      const pm = String(s.current_price || '').match(/[\d,]*\d(?:\.\d+)?/);  // 첫 숫자만 ("30,650 (5/21…)" → 30650)
      const price = pm ? parseFloat(pm[0].replace(/,/g, '')) : NaN;
      if (!price) continue;
      preds.push({
        date, date_compact: date.replace(/-/g, ''), time,
        code: s.code, name: s.name, market: null, type: s.type || '스윙',
        decision: s.decision, entry_price: price,
        bar: (parseFloat(s.change_pct) >= 0) ? '양봉' : '음봉',
        vol_band: 'unknown', status: 'pending', sim: false,
      });
    }
  }
  return preds;
}

// ───────────────────────────────────────────────────────────
// 시뮬레이션 — 과거 기준일에 합성 판정 생성 (실가격·실코드, 판정만 결정적 합성)
// ───────────────────────────────────────────────────────────
function buildSimLedger(dates) {
  const anchor = dates[dates.length - 1 - 20];  // forward 20거래일 확보되는 가장 최근일
  const day = loadDay(anchor);
  const all = [...day.values()].filter(r => r.close > 0 && r.volume > 0)
    .sort((a, b) => a.code.localeCompare(b.code));
  const step = Math.max(1, Math.floor(all.length / 40));
  const sample = all.filter((_, idx) => idx % step === 0).slice(0, 40);

  // vol_band: 직전 5거래일 평균 거래량 대비 (함정① 거래일 시퀀스 재사용)
  const ai = dates.indexOf(anchor);
  const prior = dates.slice(ai - 5, ai);
  const avgVol = {};
  for (const c of sample.map(r => r.code)) {
    let s = 0, n = 0;
    for (const pd of prior) { const r = loadDay(pd).get(c); if (r) { s += r.volume; n++; } }
    avgVol[c] = n ? s / n : null;
  }

  const decisions = ['🟢 매수 준비', '🚫 손절 검토', '🟡 보유 유지', '⏸ 보류 (관망)'];
  const preds = sample.map((r, idx) => {
    const ratio = avgVol[r.code] ? r.volume / avgVol[r.code] : null;
    const vol_band = ratio == null ? 'unknown' : ratio < 0.7 ? '위축' : ratio > 1.5 ? '급증' : '보통';
    return {
      date: `${anchor.slice(0, 4)}-${anchor.slice(4, 6)}-${anchor.slice(6, 8)}`,
      date_compact: anchor, code: r.code, name: r.name, market: r.market,
      type: '스윙', decision: decisions[idx % 4], entry_price: r.close,
      bar: r.fluc_pct >= 0 ? '양봉' : '음봉', vol_band, status: 'pending', sim: true,
    };
  });
  return { anchor, preds };
}

function calDayDiff(a, b) {
  const d = (s) => new Date(+s.slice(0, 4), +s.slice(4, 6) - 1, +s.slice(6, 8));
  return Math.round((d(b) - d(a)) / 86400000);
}

// ───────────────────────────────────────────────────────────
// ── 영속 원장(predictions.jsonl) 입출력 ──
const LEDGER_PATH = path.join(DATA_DIR, 'predictions.jsonl');
const SCORECARD_PATH = path.join(DATA_DIR, 'scorecard.json');
const keyOf = (r) => `${r.date_compact}_${r.time || ''}_${r.code}`;

function loadLedger() {
  if (!fs.existsSync(LEDGER_PATH)) return [];
  return fs.readFileSync(LEDGER_PATH, 'utf-8').split('\n').filter(Boolean).map(l => JSON.parse(l));
}
function saveLedger(rows) {
  fs.writeFileSync(LEDGER_PATH, rows.map(r => JSON.stringify(r)).join('\n') + '\n', 'utf-8');
}

// 하루 1건만 유지: (날짜, 종목)별 최신 회차(=종가 19:00) 우선, 이미 채점된 행은 보존
function collapseDaily(rows) {
  const best = new Map();
  const rank = (r) => (r.status === 'scored' ? 1e7 : 0) + parseInt(r.time || '0', 10);
  for (const r of rows) {
    const k = `${r.date_compact}_${r.code}`;
    const cur = best.get(k);
    if (!cur || rank(r) >= rank(cur)) best.set(k, r);
  }
  return [...best.values()];
}

// 진입 거래량 밴드 (KRX: 진입일 거래량 / 직전 5거래일 평균) — 조건별 적중률 집계용
function volBand(code, dateCompact, dates) {
  const i = dates.indexOf(dateCompact);
  if (i < 5) return 'unknown';
  const cur = loadDay(dateCompact).get(code);
  if (!cur || !cur.volume) return 'unknown';
  let s = 0, n = 0;
  for (const pd of dates.slice(i - 5, i)) { const r = loadDay(pd).get(code); if (r && r.volume) { s += r.volume; n++; } }
  if (!n) return 'unknown';
  const ratio = cur.volume / (s / n);
  return ratio < 0.7 ? '위축' : ratio > 1.5 ? '급증' : '보통';
}

// dante_analysis_*.json → 원장 신규 행만 append (dedup)
function backfillIntoLedger() {
  const existing = loadLedger();
  const seen = new Set(existing.map(keyOf));
  let added = 0;
  for (const p of buildLedgerFromAnalysis()) {
    if (seen.has(keyOf(p))) continue;
    existing.push(p); seen.add(keyOf(p)); added++;
  }
  const collapsed = collapseDaily(existing);
  saveLedger(collapsed);
  console.log(`[backfill] 신규 ${added}건 적재 → 하루 1건(19:00) dedup 후 ${collapsed.length}건 (raw ${existing.length})`);
}

// 원장 채점: 대기(pending) 중 윈도우 경과분만 채점 + 성적표 산출
function runLedger(dates) {
  let rows = loadLedger();
  if (!rows.length) {
    console.log(`\n⚠ 원장이 비어있습니다 (${LEDGER_PATH}).`);
    console.log(`   → 분석 실행 시 자동 적재되거나, 'node score_predictions.js --backfill' 로 기존 분석을 가져오세요.`);
    return;
  }
  const before = rows.length;
  rows = collapseDaily(rows);  // append로 쌓인 하루 다회차 → 1건(19:00)으로 정규화
  if (rows.length !== before) console.log(`   (하루 1건 dedup: ${before} → ${rows.length}건)`);
  let newlyScored = 0, pending = 0;
  for (const r of rows) {
    if (!r.vol_band || r.vol_band === 'unknown') r.vol_band = volBand(r.code, r.date_compact, dates);
    if (r.status === 'scored') continue;
    const res = scoreOne(r, dates);
    if (res.status === 'scored') { Object.assign(r, res); newlyScored++; }
    else { r.pending_reason = res.reason; pending++; }
  }
  saveLedger(rows);

  const card = aggregate(rows);
  fs.writeFileSync(SCORECARD_PATH, JSON.stringify({ generated_at: new Date().toISOString(), window: WINDOW, ...card }, null, 2), 'utf-8');

  console.log(`\n📒 원장 ${rows.length}건 — 채점완료 ${card.n_scored} / 대기 ${pending}${newlyScored ? ` (이번 신규 채점 ${newlyScored})` : ''}`);
  if (!card.n_scored) {
    const near = rows.filter(r => r.status === 'pending').slice(0, 3).map(r => `${r.name}[${r.pending_reason || ''}]`).join(' · ');
    console.log(`   아직 채점 가능 판정 없음 (스윙 +${WINDOW['스윙']}거래일 경과 필요). ${near}`);
  } else {
    console.log(`📊 적중률 ${card.overall.hit_rate}% · 평균수익 ${card.overall.avg_ret >= 0 ? '+' : ''}${card.overall.avg_ret}% (n=${card.n_scored})`);
    const pg = (title, g) => { if (!Object.keys(g).length) return; console.log(`   ── ${title}`); for (const [k, v] of Object.entries(g)) console.log(`      ${k.padEnd(14)} 적중 ${String(v.hit_rate).padStart(5)}% · 평균 ${(v.avg_ret >= 0 ? '+' : '') + v.avg_ret}% (n=${v.n})`); };
    pg('판정별', card.by_decision); pg('양봉/음봉별', card.by_bar); pg('거래량 밴드별', card.by_vol_band);
  }
  console.log(`💾 성적표: ${SCORECARD_PATH}\n`);
}

// 시뮬레이션 데모 (과거 기준일 합성 판정 → 채점 엔진·함정 검증)
function runSimulate(dates) {
  const sim = buildSimLedger(dates);
  const preds = sim.preds, anchor = sim.anchor;
  console.log(`[SIM] 시뮬레이션 원장 생성: ${preds.length}건 (기준일 ${anchor}, 실코드·실종가, 판정만 합성)`);
  const scored = preds.map(p => ({ ...p, ...scoreOne(p, dates) }));

  const N = WINDOW['스윙'];
  const sd = forwardTradingDate(dates, preds[0].date_compact, N);
  console.log(`\n🔎 함정① 거래일 카운트: 진입 ${preds[0].date_compact} +${N}거래일 = ${sd} (달력 ${calDayDiff(preds[0].date_compact, sd)}일, 휴장 ${calDayDiff(preds[0].date_compact, sd) - N}일 제외)`);
  const mkt = scored.reduce((a, r) => (a[r.market] = (a[r.market] || 0) + 1, a), {});
  console.log(`🔎 함정② 시장 자동판별: ${Object.entries(mkt).map(([k, v]) => `${k}=${v}`).join(' / ')}`);

  const card = aggregate(scored);
  console.log(`\n📊 시뮬 성적표 (채점 ${card.n_scored}/${card.n_total}건) — 적중률 ${card.overall.hit_rate}% · 평균 ${card.overall.avg_ret}%`);
  const pg = (title, g) => { console.log(`   ── ${title}`); for (const [k, v] of Object.entries(g)) console.log(`      ${k.padEnd(14)} 적중 ${String(v.hit_rate).padStart(5)}% · 평균 ${(v.avg_ret >= 0 ? '+' : '') + v.avg_ret}% (n=${v.n})`); };
  pg('판정별', card.by_decision); pg('양봉/음봉별', card.by_bar); pg('거래량 밴드별', card.by_vol_band);

  fs.writeFileSync(path.join(DATA_DIR, 'predictions.sim.jsonl'), scored.map(r => JSON.stringify(r)).join('\n') + '\n', 'utf-8');
  fs.writeFileSync(path.join(DATA_DIR, 'scorecard.sim.json'), JSON.stringify({ generated_at: new Date().toISOString(), anchor, window: WINDOW, ...card }, null, 2), 'utf-8');
  console.log(`\n💾 시뮬 산출: data/predictions.sim.jsonl · data/scorecard.sim.json\n`);
}

function main() {
  const dates = loadTradingDates();
  console.log(`\n📦 거래일 캐시: ${dates.length}일 (${dates[0]} ~ ${dates[dates.length - 1]})`);
  if (SIMULATE) return runSimulate(dates);
  if (BACKFILL) backfillIntoLedger();
  runLedger(dates);
}

main();
