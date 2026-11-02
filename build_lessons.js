/**
 * build_lessons.js — 1단계: 교훈 회상 풀 빌더
 *
 * AI단테_매매일지.md §6.9 종결 거래(성공+손절)를 파싱하고,
 * KRX OHLCV 캐시로 [진입→청산 보유 궤적 + 매도 후 20거래일]을 재구성하여
 * 정량 지표 + 매도 타이밍 라벨 + 교훈 텍스트가 합쳐진 lessons.json 을 만든다.
 *
 *   입력 : AI단테_매매일지.md (종결 거래 + 🎓 거래 회고)
 *          data/krx_ohlcv_cache/{YYYYMMDD}_{KOSPI|KOSDAQ}.json
 *   출력 : data/lessons.json (1·3단계 회상 풀 / 4단계 집계 입력)
 *
 * 채점 엔진(score_predictions.js)의 거래일 시퀀스·시장 자동판별 로직 재사용.
 *
 * 사용: node build_lessons.js
 */
const fs = require('fs');
const path = require('path');

const ROOT = __dirname;
const CACHE_DIR = path.join(ROOT, 'data', 'krx_ohlcv_cache');
const JOURNAL = path.join(ROOT, 'AI단테_매매일지.md');
const OUT = path.join(ROOT, 'data', 'lessons.json');

const POST_WINDOW = 20;       // 매도 후 거래일 (보유궤적·post_close 라벨)
const EXIT_EVAL_WINDOW = 10;  // +10거래일 청산 평가 (손절 정당 / 더 보유 여부, 사용자 요청)

// ── 거래일 시퀀스 (휴장일 자동 제외) ──
function loadTradingDates() {
  const set = new Set();
  for (const f of fs.readdirSync(CACHE_DIR)) {
    const m = f.match(/^(\d{8})_(KOSPI|KOSDAQ)\.json$/);
    if (m) set.add(m[1]);
  }
  return [...set].sort();
}

// ── 캐시 1일 로드 (KOSPI+KOSDAQ 병합, 시장 자동판별) ──
const _dayCache = new Map();
function loadDay(date) {
  if (_dayCache.has(date)) return _dayCache.get(date);
  const map = new Map();
  for (const mkt of ['KOSPI', 'KOSDAQ']) {
    const fp = path.join(CACHE_DIR, `${date}_${mkt}.json`);
    if (!fs.existsSync(fp)) continue;
    for (const r of JSON.parse(fs.readFileSync(fp, 'utf-8')).rows) {
      map.set(r.code, { code: r.code, name: r.name, market: mkt, close: r.close, high: r.high, low: r.low });
    }
  }
  _dayCache.set(date, map);
  return map;
}

const toCompact = (d) => d ? d.replace(/-/g, '') : null;
// 셀의 '첫 번째' 숫자 토큰만 추출 (천단위 쉼표 허용). 48,608원 (…13주…) → 48608
const num = (s) => { const m = String(s || '').match(/[\d,]*\d/); return m ? parseInt(m[0].replace(/,/g, ''), 10) : null; };
// 가격 추출: '원' 접미 숫자 우선 (\"초기 11주 40,925원\"의 주 수 11 오인 방지). 없으면 num() 폴백.
const priceNum = (s) => { const m = String(s || '').match(/([\d,]{2,})\s*원/); return m ? parseInt(m[1].replace(/,/g, ''), 10) : num(s); };
const pct = (a, b) => (b ? +(((a - b) / b) * 100).toFixed(1) : null);
const sgn = (v) => v == null ? '—' : `${v >= 0 ? '+' : ''}${v}%`;

// ── 일지 블록 파싱 ──
function parseJournal() {
  const text = fs.readFileSync(JOURNAL, 'utf-8');
  const blocks = text.split(/\n(?=### )/).filter(b => b.startsWith('### ') && /\(\d{6}\)/.test(b));
  const trades = [];

  for (const b of blocks) {
    const head = b.split('\n')[0];
    const hm = head.match(/^###\s*(?:🆕\s*)?(.+?)\s*\((\d{6})\)\s*—\s*(스윙|중장기)/);
    if (!hm) continue;
    const [, name, code, type] = hm;
    const result = /🛑|손절/.test(head) ? '손절' : '성공';

    const cell = (label) => {  // 굵게(**) 표기된 라벨도 허용
      const m = b.match(new RegExp(`\\|\\s*\\**\\s*${label}\\s*\\**\\s*\\|\\s*([^|]+)\\|`));
      return m ? m[1].trim() : '';
    };

    // 거래 기간 → 진입일 / 청산일
    const period = cell('거래 기간');
    const [lhs, rhs = ''] = period.split('~');
    const entryFull = lhs.match(/\d{4}-\d{2}-\d{2}/);
    const entry = entryFull ? entryFull[0] : null;  // 성우하이텍 = "초중순" → null
    let exit = null;
    const rhsFull = rhs.match(/\d{4}-\d{2}-\d{2}/);
    const rhsMd = rhs.match(/(?<!\d)(\d{2})-(\d{2})(?!\d)/);
    if (rhsFull) exit = rhsFull[0];
    else if (rhsMd) exit = `${(entry || '2026').slice(0, 4)}-${rhsMd[1]}-${rhsMd[2]}`;
    else if (entry) exit = entry;  // 당일 매매 (~ 없음)

    const avgPrice = priceNum(cell('진입 평단'));
    let exitPrice = priceNum(cell('평균 매도가'));   // 일지 평균 매도가 = 사용자 체결 SSOT
    let exitSource = exitPrice ? 'journal_avg_sell' : null;
    let realizedPct = exitPrice ? pct(exitPrice, avgPrice) : null;
    if (!exitPrice) {
      // 평균 매도가 행 없음 → 최종 실현손익 %에서 역산 (시세 종가로 날조 금지 = SSOT 준수)
      const pm = cell('최종 실현손익').match(/([+-]?\d+(?:\.\d+)?)\s*%/);
      if (pm && avgPrice) { realizedPct = parseFloat(pm[1]); exitPrice = Math.round(avgPrice * (1 + realizedPct / 100)); exitSource = 'derived_from_realized'; }
    }

    // 🎓 거래 회고 불릿
    const lessonsBlock = b.split(/🎓\s*거래 회고/)[1] || '';
    const lessons = (lessonsBlock.match(/^- .+$/gm) || [])
      .map(l => l.replace(/^- /, '').replace(/\*\*/g, '').trim())
      .filter(l => !l.startsWith('📊 누적 실현') && !l.startsWith('📌'));  // 누적합계·부수 메모 제외

    trades.push({ name: name.trim(), code, type, result, entry, exit, avgPrice, exitPrice, exitSource, realizedPct, lessons });
  }
  return trades;
}

// ── KRX 궤적 재구성 ──
function reconstruct(t, dates) {
  const traj = { post_exit_days: 0, complete: false };
  const exitC = toCompact(t.exit);
  const xi = exitC ? dates.indexOf(exitC) : -1;

  const ref = t.exitPrice || t.avgPrice;  // 매도 타이밍 기준가 (일지 SSOT, 시세 날조 X)

  // 보유 궤적 (진입→청산)
  const entryC = toCompact(t.entry);
  const ei = entryC ? dates.indexOf(entryC) : -1;
  if (ei >= 0 && xi >= ei) {
    let hi = 0, lo = Infinity;
    for (let i = ei; i <= xi; i++) {
      const r = loadDay(dates[i]).get(t.code);
      if (r) { hi = Math.max(hi, r.high); lo = Math.min(lo, r.low); }
    }
    traj.hold_mfe = hi; traj.hold_mfe_pct = pct(hi, t.avgPrice);
    traj.hold_mae = lo === Infinity ? null : lo; traj.hold_mae_pct = lo === Infinity ? null : pct(lo, t.avgPrice);
    // 익절 효율: 보유 중 최대 미실현(MFE) 대비 실제 실현 (성공 거래만 — 손절은 익절 효율 대상 아님)
    if (traj.hold_mfe_pct != null && t.realizedPct != null) {
      traj.left_on_table_pp = +(traj.hold_mfe_pct - t.realizedPct).toFixed(1);
      if (traj.left_on_table_pp >= 10 && t.result === '성공')
        traj.capture_note = `📉 익절 효율 낮음 — 보유 중 최대 +${traj.hold_mfe_pct}% 중 +${t.realizedPct}%만 실현 (미실현 ${traj.left_on_table_pp}%p)`;
    }
  }

  // 매도 후 N거래일
  if (xi >= 0 && ref) {
    let hi = 0, lo = Infinity, lastClose = null, days = 0;
    for (let k = 1; k <= POST_WINDOW && xi + k < dates.length; k++) {
      const r = loadDay(dates[xi + k]).get(t.code);
      if (!r) continue;
      hi = Math.max(hi, r.high); lo = Math.min(lo, r.low); lastClose = r.close; days = k;
    }
    traj.post_exit_days = days;
    traj.complete = days >= POST_WINDOW;
    if (days > 0) {
      traj.post_high = hi; traj.post_high_pct = pct(hi, ref);
      traj.post_low = lo; traj.post_low_pct = pct(lo, ref);
      traj.post_close = lastClose; traj.post_close_pct = pct(lastClose, ref);

      // ── 매도 타이밍 라벨 (단테 룰 맥락에서 해석) ──
      const c = traj.post_close_pct;
      if (t.result === '성공') {
        if (c <= -2) traj.exit_timing = `✅ 적기 청산 (매도 후 ${c}% 하락 회피)`;
        else if (c >= 5) traj.exit_timing = `⚠️ 조기 청산 가능성 (매도 후 +${c}% · 단, 룰 준수면 정상)`;
        else traj.exit_timing = `〰️ 중립 (매도 후 ${c >= 0 ? '+' : ''}${c}% 보합)`;
      } else {
        if (c <= -2) traj.exit_timing = `✅ 손절 적기 (매도 후 ${c}% 추가 하락 회피)`;
        else if (c >= 5) traj.exit_timing = `⚠️ 흔들기 손절 가능성 (매도 후 +${c}% 반등 → 재진입 룰 점검)`;
        else traj.exit_timing = `〰️ 손절 중립 (매도 후 ${c >= 0 ? '+' : ''}${c}%)`;
      }
    }
  }

  // ── +10거래일 청산 평가 (종결종목: 손절이 정당했나 / 더 보유했어야 했나) ──
  // 사용자 요청: 매도 후 +10거래일 KRX 종가로 청산 의사결정을 채점.
  if (xi >= 0 && ref) {
    const i10 = xi + EXIT_EVAL_WINDOW;
    if (i10 < dates.length) {
      const r10 = loadDay(dates[i10]).get(t.code);
      if (r10) {
        const c10 = pct(r10.close, ref);
        traj.post_close_10d = r10.close;
        traj.post_close_10d_pct = c10;
        if (t.result === '손절') {
          traj.exit_verdict_10d = c10 <= -3 ? { tag: '✅', label: `손절 정당 (+10일 ${c10}% 추가 하락 → 더 보유했으면 손실 확대)`, good: true }
            : c10 >= 5 ? { tag: '⚠️', label: `흔들기 손절 (+10일 +${c10}% 반등 → 더 보유했어야)`, good: false }
            : { tag: '〰️', label: `손절 중립 (+10일 ${c10 >= 0 ? '+' : ''}${c10}%)`, good: null };
        } else {
          traj.exit_verdict_10d = c10 >= 5 ? { tag: '⚠️', label: `조기 청산 (+10일 +${c10}% 추가 상승 → 더 보유 가능)`, good: false }
            : c10 <= -3 ? { tag: '✅', label: `적기 청산 (+10일 ${c10}% → 하락 회피)`, good: true }
            : { tag: '〰️', label: `적정 청산 (+10일 ${c10 >= 0 ? '+' : ''}${c10}%)`, good: null };
        }
      }
    }
  }
  return traj;
}

function main() {
  const dates = loadTradingDates();
  const trades = parseJournal();
  console.log(`📒 종결 거래 파싱: ${trades.length}건 (성공 ${trades.filter(t => t.result === '성공').length} / 손절 ${trades.filter(t => t.result === '손절').length})`);

  const records = trades.map(t => {
    const trajectory = reconstruct(t, dates);
    return {
      name: t.name, code: t.code, type: t.type, result: t.result,
      entry_date: t.entry, exit_date: t.exit,
      avg_price: t.avgPrice, exit_price: t.exitPrice, exit_price_source: t.exitSource,
      realized_pct: t.realizedPct,
      trajectory,
      lessons: t.lessons,
    };
  });

  fs.writeFileSync(OUT, JSON.stringify({ generated_at: new Date().toISOString(), window: POST_WINDOW, count: records.length, trades: records }, null, 2), 'utf-8');

  // 콘솔 요약
  console.log(`\n${'종목'.padEnd(14)}${'결과'.padEnd(5)} ${'평단→매도가'.padStart(14)}  실현%   보유MFE   매도후20일   매도 타이밍`);
  for (const r of records) {
    const tj = r.trajectory;
    const px = `${r.avg_price || '?'}→${r.exit_price || '?'}`;
    const post = tj.post_close_pct != null ? `${sgn(tj.post_close_pct)}${tj.complete ? '' : `(${tj.post_exit_days}일)`}` : '—';
    console.log(`${r.name.padEnd(14)}${r.result.padEnd(5)} ${px.padStart(14)}  ${sgn(r.realized_pct).padStart(6)}  ${sgn(tj.hold_mfe_pct).padStart(7)}   ${post.padStart(11)}   ${tj.exit_timing || '—'}`);
  }
  const flags = records.filter(r => r.trajectory.capture_note || (r.trajectory.exit_timing || '').includes('⚠️'));
  if (flags.length) {
    console.log(`\n🔎 회상 플래그 (4단계 회고 후보):`);
    for (const r of flags) console.log(`  · ${r.name}: ${r.trajectory.capture_note || r.trajectory.exit_timing}`);
  }
  const exitVerdicts = records.filter(r => r.trajectory.exit_verdict_10d);
  if (exitVerdicts.length) {
    console.log(`\n⏱️ +${EXIT_EVAL_WINDOW}거래일 청산 평가 (손절 정당 / 더 보유 여부):`);
    for (const r of exitVerdicts) console.log(`  ${r.trajectory.exit_verdict_10d.tag} ${r.name} (${r.result}): ${r.trajectory.exit_verdict_10d.label}`);
  }
  console.log(`\n💾 회상 풀 저장: ${OUT} (${records.length}건)`);
}

main();
