/**
 * recall_similar.js — 3단계: 유사 패턴 회상 (숫자 지표 유사도 · 이미지/CLIP 불필요)
 *
 * §4.9.4의 목적(비슷한 과거 상황 → 결과 표시)을 KRX OHLCV 기반 "기술적 상황 벡터"로 구현.
 * 차트 이미지/CLIP 대신, 이평 배열·이평 대비 위치·거래량·모멘텀·채널 위치를 벡터화해
 * 코사인/유클리드 유사도로 Top-K 과거 종결거래를 매칭하고 결과·교훈·신뢰도를 산출한다.
 *
 *   입력 : data/lessons.json (종결거래 + 진입일 + 결과/교훈)
 *          data/krx_ohlcv_cache/ (피처 계산)  ·  stocks.json (현재 라인업)
 *   출력 : data/vector_store.json (종결거래 피처 풀)
 *          data/recall_latest.json (+ recall_{date}.json) — 현재 종목별 Top-K 매칭
 *
 * 사용: node recall_similar.js
 */
const fs = require('fs');
const path = require('path');

const ROOT = __dirname;
const CACHE_DIR = path.join(ROOT, 'data', 'krx_ohlcv_cache');
const DATA_DIR = path.join(ROOT, 'data');
const K = 3;
const DIMS = ['p_ma5', 'p_ma20', 'p_ma60', 'p_ma112', 'p_ma224', 'aligned', 'vol_ratio', 'ret5', 'ret20', 'chan'];

function loadTradingDates() {
  const s = new Set();
  for (const f of fs.readdirSync(CACHE_DIR)) { const m = f.match(/^(\d{8})_(KOSPI|KOSDAQ)\.json$/); if (m) s.add(m[1]); }
  return [...s].sort();
}
const _dc = new Map();
function loadDay(d) {
  if (_dc.has(d)) return _dc.get(d);
  const map = new Map();
  for (const mk of ['KOSPI', 'KOSDAQ']) {
    const fp = path.join(CACHE_DIR, `${d}_${mk}.json`);
    if (!fs.existsSync(fp)) continue;
    for (const r of JSON.parse(fs.readFileSync(fp, 'utf-8')).rows)
      map.set(r.code, { close: r.close, high: r.high, low: r.low, volume: r.volume });
  }
  _dc.set(d, map); return map;
}
const mean = (a) => a.length ? a.reduce((s, x) => s + x, 0) / a.length : null;
function seriesField(code, i, n, dates, field) {
  const out = [];
  for (let k = Math.max(0, i - n + 1); k <= i; k++) { const r = loadDay(dates[k]).get(code); if (r && r[field] != null) out.push(r[field]); }
  return out;
}

// 기술적 상황 벡터 (code, 기준일) — 이력 224거래일 필요
function featureVector(code, anchorCompact, dates) {
  const i = dates.indexOf(anchorCompact);
  if (i < 224) return null;
  const cur = loadDay(anchorCompact).get(code);
  if (!cur || !cur.close) return null;
  const c = cur.close;
  const ma = (n) => mean(seriesField(code, i, n, dates, 'close'));
  const [ma5, ma20, ma60, ma112, ma224] = [5, 20, 60, 112, 224].map(ma);
  if ([ma5, ma20, ma60, ma112, ma224].some(x => x == null)) return null;
  const closeAt = (k) => { const r = loadDay(dates[i - k]).get(code); return r ? r.close : null; };
  const c5 = closeAt(5), c20 = closeAt(20);
  const highs = seriesField(code, i, 60, dates, 'high'), lows = seriesField(code, i, 60, dates, 'low');
  const hi = Math.max(...highs), lo = Math.min(...lows);
  const vol5 = mean(seriesField(code, i - 1, 5, dates, 'volume'));
  return {
    p_ma5: c / ma5 - 1, p_ma20: c / ma20 - 1, p_ma60: c / ma60 - 1, p_ma112: c / ma112 - 1, p_ma224: c / ma224 - 1,
    aligned: (ma5 > ma20) + (ma20 > ma60) + (ma60 > ma112) + (ma112 > ma224),  // 정배열 점수 0~4
    vol_ratio: vol5 ? cur.volume / vol5 : 1,
    ret5: c5 ? c / c5 - 1 : 0, ret20: c20 ? c / c20 - 1 : 0,
    chan: hi > lo ? (c - lo) / (hi - lo) : 0.5,  // 60일 채널 내 위치 0~1
  };
}

// z-점수 정규화 통계 (풀 기준) + 정규화 유클리드 거리
function zstats(vecs) {
  const st = {};
  for (const d of DIMS) { const xs = vecs.map(v => v[d]); const mu = mean(xs); const sd = Math.sqrt(mean(xs.map(x => (x - mu) ** 2))) || 1; st[d] = { mu, sd: sd || 1 }; }
  return st;
}
function dist(a, b, st) {
  let s = 0;
  for (const d of DIMS) { const za = (a[d] - st[d].mu) / st[d].sd, zb = (b[d] - st[d].mu) / st[d].sd; s += (za - zb) ** 2; }
  return Math.sqrt(s);
}

function main() {
  const dates = loadTradingDates();
  const lessonsP = path.join(DATA_DIR, 'lessons.json');
  if (!fs.existsSync(lessonsP)) { console.log('⚠ lessons.json 없음 — build_lessons.js 선실행 필요. 회상 생략.'); return; }
  const lessons = JSON.parse(fs.readFileSync(lessonsP, 'utf-8')).trades || [];

  // 1) 종결거래 피처 풀
  const pool = [];
  for (const t of lessons) {
    if (!t.entry_date) { console.log(`  · ${t.name}: 진입일 없음 → 풀 제외`); continue; }
    const fv = featureVector(t.code, t.entry_date.replace(/-/g, ''), dates);
    if (!fv) { console.log(`  · ${t.name}: 이력 부족 → 풀 제외`); continue; }
    const key = (t.lessons || []).find(l => /⚠️|📐|🔁/.test(l)) || (t.lessons || [])[0] || '';
    pool.push({
      name: t.name, code: t.code, type: t.type, result: t.result, entry_date: t.entry_date,
      realized_pct: t.realized_pct, hold_mfe_pct: t.trajectory?.hold_mfe_pct,
      exit_timing: t.trajectory?.exit_timing, capture_note: t.trajectory?.capture_note,
      key_lesson: key, features: fv,
    });
  }
  fs.writeFileSync(path.join(DATA_DIR, 'vector_store.json'),
    JSON.stringify({ generated_at: new Date().toISOString(), dims: DIMS, count: pool.length, pool }, null, 2), 'utf-8');
  console.log(`\n📦 피처 풀 구축: ${pool.length}건 (성공 ${pool.filter(p => p.result === '성공').length} / 손절 ${pool.filter(p => p.result === '손절').length}) → vector_store.json`);

  if (pool.length < 2) { console.log('풀이 너무 작아 회상 생략'); return; }
  const st = zstats(pool.map(p => p.features));

  // 2) 현재 라인업 회상
  const stocks = JSON.parse(fs.readFileSync(path.join(ROOT, 'stocks.json'), 'utf-8')).stocks;
  const latest = dates[dates.length - 1];
  const nWin = pool.filter(p => p.result === '성공').length, nLoss = pool.length - nWin;
  const recall = {
    generated_at: new Date().toISOString(), as_of: latest, k: K, pool_size: pool.length,
    caveat: `풀 ${pool.length}건(성공 ${nWin}/손절 ${nLoss}) — 표본 작고 손절 사례 부족으로 승률은 과대 추정됨. 유사도 30%↓는 '약한 선례'로 참고만. 신뢰도는 풀 확대(목표 47건)·채점 누적 후 의미.`,
    stocks: [],
  };

  console.log(`\n🔍 현재 라인업 유사 패턴 회상 (기준일 ${latest}, Top-${K})`);
  for (const s of stocks) {
    const fv = featureVector(s.code, latest, dates);
    if (!fv) { recall.stocks.push({ code: s.code, name: s.name, tag: s.tag, note: 'KRX 데이터/이력 부족' }); console.log(`  ${s.name.padEnd(14)} — KRX 데이터/이력 부족`); continue; }
    const ranked = pool.map(p => ({ p, d: dist(fv, p.features, st) })).sort((a, b) => a.d - b.d).slice(0, K);
    const matches = ranked.map(({ p, d }) => ({
      name: p.name, code: p.code, result: p.result, realized_pct: p.realized_pct,
      exit_timing: p.exit_timing, key_lesson: p.key_lesson, similarity: +(100 / (1 + d)).toFixed(0),
    }));
    const wins = ranked.filter(({ p }) => p.result === '성공').length;
    const winRate = Math.round(wins / ranked.length * 100);
    const avgRet = +mean(ranked.map(({ p }) => p.realized_pct)).toFixed(1);
    const avgSim = Math.round(mean(matches.map(m => m.similarity)));
    let conf;  // 유사도 낮으면(약한 선례) 승률 무관하게 '참고용'으로 강등
    if (avgSim < 30) conf = '❔ 유사 선례 약함 (참고용)';
    else if (winRate >= 80 && avgRet > 0) conf = '⭐⭐⭐ 강 신뢰';
    else if (winRate >= 60) conf = '⭐⭐ 중 신뢰';
    else if (winRate >= 40) conf = '⭐ 약 신뢰';
    else conf = '🚫 위험';
    recall.stocks.push({ code: s.code, name: s.name, tag: s.tag, avg_sim: avgSim, win_rate: winRate, avg_ret: avgRet, confidence: conf, matches });
    console.log(`  ${s.name.padEnd(14)} [${conf}] 유사도 ${avgSim}% · 승률 ${winRate}% · 평균 ${avgRet >= 0 ? '+' : ''}${avgRet}%`);
    console.log(`     ${matches.map(m => `${m.name}(${m.similarity}%·${m.result})`).join(' / ')}`);
  }

  fs.writeFileSync(path.join(DATA_DIR, 'recall_latest.json'), JSON.stringify(recall, null, 2), 'utf-8');
  fs.writeFileSync(path.join(DATA_DIR, `recall_${latest}.json`), JSON.stringify(recall, null, 2), 'utf-8');
  console.log(`\n💾 회상 결과: data/recall_latest.json (+ recall_${latest}.json)\n`);
}

main();
