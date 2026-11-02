/**
 * auto_intake.js — 📋 노출 종목 → 스윙 활성(미체결) 자동 편입 + 자동 퇴출 (2026-10-12 사용자 지시)
 *
 *  편입: 08:30 노출 종목(눌림·돌파·밥그릇3·테마, exposure.js) 중 라인업에 없는 종목을
 *        §6 「**활성**:」 줄 + stocks.json + 대시보드 스윙 카드에 동시에 넣는다.
 *        시나리오(1·2·3차·손절·목표·주수)는 아래 scenario() 가 §4.3/§4.4 를 기계적으로 근사한다.
 *  퇴출: 이 스크립트가 관리하는 종목(data/auto_intake_state.json)이 아직 「미체결 🤖자동」 라벨이면
 *        ① 추세 이탈 = 종가 < MA20 AND 종가 < MA60  ② 편입 후 20거래일 경과  ③ T3-U(종가 > 목표)
 *        중 하나로 라인업·stocks.json·카드에서 뺀다. 사용자가 §6 라벨을 체결 등으로 바꾸면 관리 대상에서 빠진다.
 *
 *  scenario() — 앵커 지지선 S 하나로 사다리를 만든다 (§4.4 「지지선 종가 이탈 = 손절」):
 *    S    = 돌파·밥그릇3: 스캐너 line 의 이평(112+224 → 둘 중 큰 값) / 그 외: 종가 아래 가장 가까운 MA20·60·112·224
 *    손절 = S × 0.99 (종가 이탈 -1% 버퍼) · 3차 = 손절 ÷ 0.98 (§4.4 버퍼 하한 · 2026-09-10 조항)
 *    1차  = min(종가, S × 1.04) · 2차 = 1차 −2% 이내(§4.3 ±2%)이면서 3차 위
 *    목표 = 평단 위 스윙 고점(±5봉 극대) 중 평단 +8~15% 첫 값, 없으면 +5% 이상 첫 값(D1 생략 표기)
 *    주수 = 1:1:2 정수, 100만 캡 (§4.11.1) · 전량 손익비 < 1.5 면 「⏸ 보류」 표기(진입 금지)
 *  ⚠️ 이는 근사다 — 박스·기준봉·매물대 같은 차트 판독은 하지 않는다. 카드에 「🤖 자동 산출」로 표기.
 *
 *  2026-10-19 변경: 편입 원천 = 🎯 손익비 돌파(exposure 'rr', screen_rr_breakout.py)만 · 라인업 최대 5종(빈자리만 채움)
 *        · 퇴출 ④ 종가 < 손절(KRX 확정봉) 추가
 *        · 손절 이탈 퇴출은 Slack 「🛑 손절 이탈」 알림
 *  2026-10-26 변경: 손절 판정 = 매 회차(08:30 · 16:30·19:00 --summary-only) 최신 종가(lastClose — 15:30 뒤엔 당일 KRX 종가, 네이버 일봉)
 *        · D+N = 편입 후 경과 거래일. 매 회차 state.managed[].dplus 와 카드 h3 `<span class="dplus">` 갱신(19:00 Slack 이 읽음)
 *
 *  사용: node auto_intake.js [--dry-run] [--rebuild=084370,062040] [--date=YYYY-MM-DD]
 *        --rebuild : 지정 종목을 같은 산식으로 재구축해 자동 관리로 편입. 라인업에 있으면 옛 항목·카드를 걷어내고(카드 hidden 보존)
 *                    없으면 「✋ 수동 지정」으로 새로 넣는다. 건너뜀(추세 이탈 등)이면 옛 항목·카드는 그대로 둔다
 *  종료코드: 0 정상(변경 없음 포함) / 1 실패(아무것도 쓰지 않음)
 */
'use strict';
const fs = require('fs');
const path = require('path');
const { collectExposure } = require('./exposure.js');

const ROOT = __dirname;
const DATA = path.join(ROOT, 'data');
const MD = path.join(ROOT, 'AI단테.md');
const STOCKS = path.join(ROOT, 'stocks.json');
const HTML = path.join(ROOT, '분석_대시보드.html');
const STATE = path.join(DATA, 'auto_intake_state.json');
const LOG = path.join(DATA, 'auto_intake_log.jsonl');
const CAP = 1_000_000;
const EXPIRE_DAYS = 20;
const AUTO_LABEL = '미체결 🤖자동'; // §6 라벨 접두 — 이 라벨이 남아 있는 동안만 자동 퇴출 대상

const argv = process.argv.slice(2);
const DRY = argv.includes('--dry-run');
const REBUILD = ((argv.find(a => a.startsWith('--rebuild=')) || '').split('=')[1] || '').split(',').filter(Boolean);
const DATE_ARG = (argv.find(a => a.startsWith('--date=')) || '').split('=')[1];
const MAX_LINEUP = 5;
const INTAKE_SECTIONS = ['rr'];
const SUMMARY_ONLY = argv.includes('--summary-only'); // 16:30·19:00 분석 직후 요약표(충족 점수)만 다시 그림
const ACTIVE_ANCHOR = '<!-- ===== 🎯 스윙 활성 카드 삽입 위치 (auto_intake.js · 지우지 말 것) ===== -->';

const log = m => console.log(`[auto_intake] ${m}`);
const fmt = n => Math.round(n).toLocaleString('en-US');
const pct = (a, b) => `${a >= b ? '+' : '−'}${Math.abs((a / b - 1) * 100).toFixed(2)}%`;

// ── KRX 호가 단위
function tick(p) {
  return p < 2000 ? 1 : p < 5000 ? 5 : p < 20000 ? 10 : p < 50000 ? 50 : p < 200000 ? 100 : p < 500000 ? 500 : 1000;
}
const down = p => Math.floor(p / tick(p)) * tick(p);
const up = p => Math.ceil(p / tick(p)) * tick(p);

// ── KRX 캐시 → 종목별 일봉 (휴장일 파일은 rows 0개라 자연히 빠짐)
function krxBars(codes) {
  const dir = path.join(DATA, 'krx_ohlcv_cache');
  const files = fs.readdirSync(dir).filter(f => /^\d{8}_(KOSPI|KOSDAQ)\.json$/.test(f)).sort().slice(-560);
  const bars = Object.fromEntries(codes.map(c => [c, []]));
  for (const f of files) {
    const d = JSON.parse(fs.readFileSync(path.join(dir, f), 'utf-8'));
    for (const r of (Array.isArray(d) ? d : d.rows || [])) if (bars[r.code] && r.close) bars[r.code].push(r);
  }
  for (const c of codes) bars[c].sort((a, b) => a.date.localeCompare(b.date));
  return bars;
}

// ── 손절 판정용 최신 종가 (2026-10-26 사용자 지시: 매 회차 손절 이탈이면 무조건 삭제)
//   KRX 캐시 마지막 봉, 캐시에 오늘 봉이 없고 15:31 이후면 네이버 15:30 분봉(종가 단일가 = KRX 공식 종가)으로 대체.
//   ⚠️ 네이버 일봉·closePrice 는 15:30 뒤에도 시간외 단일가·NXT 로 계속 바뀐다 — 쓰지 말 것. 장중가로 손절 금지.
//   네이버 실패 시 KRX 캐시만.
function lastClose(code, b) {
  const k = (b || []).at(-1);
  const best = k ? { close: k.close, date: k.date } : null;
  const kst = new Date(Date.now() + 9 * 3600e3).toISOString(), today = kst.slice(0, 10).replace(/-/g, '');
  if ((best && best.date >= today) || kst.slice(11, 16) < '15:31') return best;
  try {
    const txt = require('child_process').execFileSync('curl', ['-s', '-m', '10',
      `https://api.finance.naver.com/siseJson.naver?symbol=${code}&requestType=1&startTime=${today}1530&endTime=${today}1530&timeframe=minute`], { encoding: 'utf-8' });
    const m = txt.match(/\["\d{8}1530",[^\]]*?,\s*([\d.]+),\s*\d+,[^\]]*\]/);
    if (m) return { close: +m[1], date: today };
  } catch (e) { log(`네이버 15:30 종가 조회 실패 ${code} — KRX 캐시만 (${e.message})`); }
  return best;
}

function indicators(b) {
  const closes = b.map(r => r.close);
  const ma = n => (closes.length >= n ? closes.slice(-n).reduce((s, x) => s + x, 0) / n : null);
  const v20 = b.slice(-20).reduce((s, r) => s + r.volume, 0) / Math.min(20, b.length);
  return { C: closes.at(-1), date: b.at(-1).date, MA20: ma(20), MA60: ma(60), MA112: ma(112), MA224: ma(224), v20 };
}

// 스윙 고점: high[i] 가 ±5봉 최대 (최근 240봉) + 최근 20봉 최고가
function resistances(b) {
  const w = b.slice(-240), out = new Set();
  for (let i = 5; i < w.length - 1; i++) {
    const seg = w.slice(i - 5, i + 6).map(r => r.high);
    if (w[i].high === Math.max(...seg)) out.add(w[i].high);
  }
  out.add(Math.max(...b.slice(-20).map(r => r.high)));
  return [...out].sort((x, y) => x - y);
}

function scenario(b, line) {
  if (b.length < 230) return { ok: false, reason: `일봉 ${b.length}개 (MA224 산출 불가)` };
  const x = indicators(b);
  // 지지 후보: 스캐너 line 이평(최우선) → 종가 아래 이평·직전 저점(±5봉 극소, 최근 120봉)을 가까운 순으로.
  // 사다리(1차 > 2차 > 3차)가 서지 않으면 다음 지지선으로 내려간다 (§4.4 「1차 지지선 → 2차 지지선」).
  const named = String(line || '').match(/\d+/g)?.map(n => x[`MA${n}`]).filter(v => v && v <= x.C) || [];
  const w = b.slice(-120), lows = [];
  for (let i = 5; i < w.length - 1; i++) {
    if (w[i].low === Math.min(...w.slice(i - 5, i + 6).map(r => r.low))) lows.push({ S: w[i].low, basis: `저점 ${w[i].date.slice(4, 6)}-${w[i].date.slice(6)}` });
  }
  const cands = [
    ...(named.length ? [{ S: Math.max(...named), basis: `MA${line}` }] : []),
    ...[...['MA20', 'MA60', 'MA112', 'MA224'].filter(k => x[k]).map(k => ({ S: x[k], basis: k })), ...lows]
      .filter(c => c.S <= x.C * 0.995).sort((a, c) => c.S - a.S),
  ];
  let S, basis, stop, p1, p2, p3;
  for (const c of cands) {
    stop = down(c.S * 0.99); p3 = up(stop / 0.98); p1 = down(Math.min(x.C, c.S * 1.04));
    if (p1 - p3 < 2 * tick(p1)) continue;
    p2 = Math.max(down(p1 - (p1 - p3) * 0.4), up(p1 * 0.98));
    if (p2 >= p1) p2 = down(p1 - tick(p1));
    if (p2 <= p3) p2 = up(p3 + tick(p3));
    if (p2 >= p1) continue;
    S = c.S; basis = c.basis; break;
  }
  if (!S) return { ok: false, reason: cands.length ? '지지선 위 1·2·3차 간격 부족' : '종가 아래 지지선(이평·직전 저점) 없음', x };
  const unit = Math.floor(CAP / (p1 + p2 + 2 * p3));
  const avg = (p1 + p2 + 2 * p3) / 4;
  const res = resistances(b).filter(h => h > avg * 1.05);
  let target = res.find(h => h >= avg * 1.08 && h <= avg * 1.15);
  const inGate = !!target;
  if (!target) target = res[0];
  if (!target) return { ok: false, reason: '평단 위 저항대 없음 (목표 산출 불가)', x };
  const rr = (target - avg) / (avg - stop);
  const rr1 = (target - p1) / (p1 - stop);
  return {
    ok: true, x, S, basis, stop, p1, p2, p3, unit, avg, target, inGate, rr, rr1,
    hold: rr < 1.5 || unit < 1,
    holdReason: unit < 1 ? '고가주 1:1:2 정수 불가(§4.11.3 소수점 수동)' : rr < 1.5 ? `전량 손익비 ${rr.toFixed(2)} < 1.5` : '',
    vol: [down(x.v20 * 0.7), up(x.v20 * 1.5)].map(v => Math.round(v)),
  };
}

// ── §6 라인업 줄
function readLineup(md) {
  const lines = md.split('\n');
  const i = lines.findIndex(l => /^>\s*-\s*\*\*활성\*\*\s*:/.test(l));
  if (i < 0) throw new Error('§6 「**활성**:」 줄 없음');
  return { lines, i };
}
function segmentRange(line, name, code, allMarkers) {
  const start = line.indexOf(`${name} (${code})`);
  if (start < 0) return null;
  const after = allMarkers.map(m => line.indexOf(m)).filter(p => p > start).sort((a, b) => a - b)[0];
  let end = after ?? line.length;
  return { start, end };
}
function removeFromLineup(line, stocks, code) {
  const s = stocks.find(t => t.code === code);
  const markers = stocks.filter(t => t.code !== code).map(t => `${t.name} (${t.code})`);
  const r = s && segmentRange(line, s.name, code, markers);
  if (!r) return line;
  return (line.slice(0, r.start) + line.slice(r.end)).replace(/\s·\s*$/, '').replace(/:\s*·\s*/, ': ').replace(/·\s+·/g, '·');
}

// ── 대시보드 카드
function cardBounds(html, id) {
  const at = html.indexOf(`id="${id}"`);
  if (at < 0) return null;
  const start = html.lastIndexOf('<div class="card"', at);
  let depth = 0, i = start;
  const re = /<div\b|<\/div>/g;
  re.lastIndex = start;
  let m;
  while ((m = re.exec(html))) {
    depth += m[0] === '</div>' ? -1 : 1;
    if (depth === 0) { i = m.index + 6; break; }
  }
  // 카드 바로 앞 주석 블록까지 포함
  const before = html.slice(0, start);
  const cm = before.match(/<!--(?:(?!-->)[\s\S])*-->\s*$/);
  return { start: cm ? start - cm[0].length : start, cardStart: start, end: i };
}

const SECTION_LABEL = { rr: '🎯 손익비', pullback: '🌊 눌림', breakout: '💥 돌파', bowl3: '🍚 밥그릇3', theme: '🧭 테마', rebuild: '🔁 재구축', manual: '✋ 수동 지정' };

function renderCard(e, sc, mmdd) {
  const { x } = sc;
  const n1 = sc.unit, n3 = sc.unit * 2, total = sc.unit * 4;
  const avg2 = (sc.p1 + sc.p2) / 2;
  const row = (label, price, qty, avg, gain, rr, cond) =>
    `      <tr><td data-label="스윙 분할 계획">${label}</td><td data-label="매수가">${fmt(price)}</td><td data-label="수량">${qty}주</td><td data-label="누적 평단">${fmt(avg)}</td><td data-label="목표가">${fmt(sc.target)}</td><td data-label="예상 이익">${fmt(gain)}</td><td data-label="손익비">${rr.toFixed(2)}</td><td data-label="조건">${cond}</td></tr>`;
  const cond = price => `<div class="condition-list"><span>1) 양봉: 종가 &gt; 시가</span><span>2) 가격: 종가 ${fmt(price)}원 이하, ${sc.basis} ${fmt(sc.S)} 위</span><span>3) 거래량: ${fmt(sc.vol[0])}~${fmt(sc.vol[1])}주 <span class="hd-note">(20일 평균 ${fmt(x.v20)}의 0.7~1.5배)</span></span><span>4) 수급: 당일 외국인·프로그램 모두 순매수, UNKNOWN이면 보류</span></div>`;
  const gate = sc.inGate ? `평단 ${pct(sc.target, sc.avg)} · §4.4-D 범위 내` : `평단 ${pct(sc.target, sc.avg)} · §4.4-D 범위 밖(D1 생략·D2 직행)`;
  const mode = sc.hold
    ? `<span class="weight-chip" style="background:#3a1d1d;color:#f85149;">⏸ 보류 — ${sc.holdReason} <span class="chip-note">(진입 금지)</span></span>`
    : `<span class="weight-chip" style="background:#3a2e0d;color:#d29922;">⏸ 미체결 — 1차 ${fmt(sc.p1)} 지정가 대기 <span class="chip-note">(추격 금지)</span></span>`;
  return `<!-- ===== ${e.name} (${e.code}) 스윙 활성·미체결 🤖 자동 편입 ${e.date} · ${SECTION_LABEL[e.section]}${e.score != null ? ' ' + e.score + '점' : ''} (auto_intake.js)
     앵커 ${sc.basis} ${fmt(sc.S)} · 1차 ${fmt(sc.p1)} / 2차 ${fmt(sc.p2)} / 3차 ${fmt(sc.p3)} · 손절 ${fmt(sc.stop)} · 목표 ${fmt(sc.target)} · ${total}주 · 평단 ${fmt(sc.avg)} ===== -->
<div class="card" id="card-${e.code}-active" style="border-left:4px solid #d29922;">
  <div class="card-header">
    <div>
      <h3 style="margin:0;">${e.name} <span class="hd-note">(${e.code})</span> ⏸ 미체결 · 🤖 자동 편입 <span class="hd-note">(${mmdd(e.date)} ${SECTION_LABEL[e.section]}${e.score != null ? ' ' + e.score + '점' : ''})</span></h3>
      <span class="badge badge-swing">스윙 <span class="chip-note">(활성 · 미체결)</span></span>
    </div>
    <div class="weight-info">
      비중: <span class="weight-chip">1 : 1 : 2</span>
      예산: <span class="weight-chip">${(total * sc.avg / 10000).toFixed(2)}만 <span class="chip-note">(${total}주 · 100만 한도)</span></span>
      모드: ${mode}
    </div>
    <div class="phase-strip">
      <span class="phase-cell"><span class="ph-label"><span class="ph-no">1차</span> ⏳ 대기 <span class="chip-note">(${sc.basis} ${fmt(sc.S)} ${pct(sc.p1, sc.S)})</span></span><span class="ph-price">${fmt(sc.p1)}</span></span>
      <span class="phase-cell"><span class="ph-label"><span class="ph-no">2차</span> ⏳ 대기 <span class="chip-note">(1차 ${pct(sc.p2, sc.p1)})</span></span><span class="ph-price">${fmt(sc.p2)}</span></span>
      <span class="phase-cell"><span class="ph-label"><span class="ph-no">3차</span> ⏳ 대기 <span class="chip-note">(손절 ÷ 0.98 · ${sc.basis} 직상)</span></span><span class="ph-price">${fmt(sc.p3)}</span></span>
    </div>
  </div>
  <div class="summary-grid">
    <div class="summary-item"><div class="label">현재가 <span class="hd-note">(${mmdd(x.date)})</span></div><div class="value">${fmt(x.C)}원</div></div>
    <div class="summary-item"><div class="label">현재 위치</div><div class="value" style="color:#d29922;">${sc.basis} ${pct(x.C, sc.S)} 위 <span class="value-note">(🤖 자동 산출 · 차트 판독 없음)</span></div></div>
    <div class="summary-item"><div class="label">공구리</div><div class="value" style="color:#f0883e;">${fmt(sc.S)}원 <span class="value-note">(${sc.basis} · ${x.date.slice(4, 6)}-${x.date.slice(6)} 확정봉)</span></div></div>
    <div class="summary-item"><div class="label">손절라인</div><div class="value stop">${fmt(sc.stop)}원 <span class="value-note">(${sc.basis} 종가 이탈 −1%, 후퇴 금지)</span></div></div>
  </div>
  <table class="portfolio-table">
    <thead><tr><th>스윙 분할 계획</th><th>매수가</th><th>수량</th><th>누적 평단</th><th>목표가</th><th>예상 이익</th><th>손익비</th><th>조건</th></tr></thead>
    <tbody>
${row('1차 대기', sc.p1, n1, sc.p1, (sc.target - sc.p1) * n1, sc.rr1, cond(sc.p1))}
${row('2차 대기', sc.p2, n1, avg2, (sc.target - avg2) * n1 * 2, (sc.target - avg2) / (avg2 - sc.stop), cond(sc.p2))}
${row('3차 대기', sc.p3, n3, sc.avg, (sc.target - sc.avg) * total, sc.rr, cond(sc.p3))}
      <tr><td data-label="스윙 분할 계획">손절</td><td data-label="매수가">${fmt(sc.stop)}</td><td data-label="수량">전량</td><td data-label="누적 평단">-</td><td data-label="목표가">-</td><td data-label="예상 이익">${fmt((sc.stop - sc.avg) * total)}</td><td data-label="손익비">-</td><td data-label="조건">${sc.basis} ${fmt(sc.S)} 종가 이탈 −1% (3차 ${pct(sc.stop, sc.p3)}) · 기계적 전량 손절, 후퇴 금지</td></tr>
    </tbody>
  </table>
  <div class="warn-box" style="background:#1a2838;border-left:4px solid #58a6ff;margin-bottom:12px;">
    🤖 <strong>자동 편입 근거</strong> — 목표 ${fmt(sc.target)} (${gate}) · 퇴출: 종가 &lt; MA20·MA60 동시 하회 / 편입 후 ${EXPIRE_DAYS}거래일 미체결 / 종가 &gt; 목표(매 회차)
  </div>
</div>

`;
}

function lineupEntry(e, sc) {
  const tag = `${SECTION_LABEL[e.section]}${e.score != null ? ` ${e.score}점` : ''}`;
  const mid = `1차 ${fmt(sc.p1)} / 2차 ${fmt(sc.p2)} / 3차 ${fmt(sc.p3)} · 손절 ${fmt(sc.stop)} · ${sc.unit * 4}주 · 목표 ${fmt(sc.target)}`;
  return `${e.name} (${e.code}) **${AUTO_LABEL} ${e.date.slice(5)} ${tag}${sc.hold ? ' ⏸보류' : ''}** (${mid}${sc.hold ? ' · ' + sc.holdReason : ''} · 앵커 ${sc.basis})`;
}

// 19:00·15:30 분석(Slack 「충족 N/M」)의 진입조건 충족 점수 — 최신 full 분석 JSON 기준 (2026-10-19)
function gateScores() {
  const f = fs.readdirSync(DATA).filter(n => /^dante_analysis_\d{4}-\d{2}-\d{2}_\d{4}\.json$/.test(n)).sort().reverse()
    .find(n => { try { return JSON.parse(fs.readFileSync(path.join(DATA, n), 'utf-8')).mode !== 'recommend-only'; } catch { return false; } });
  const out = {};
  if (f) for (const s of JSON.parse(fs.readFileSync(path.join(DATA, f), 'utf-8')).stocks || []) {
    const ec = s.entry_conditions;
    if (ec && ec.required_total) out[s.code] = ec;
  }
  return out;
}
// 종목명 옆 시장 표기 (최신 KRX 캐시 파일 기준)
let _mk = null;
function marketOf(code) {
  if (!_mk) {
    _mk = {};
    const dir = path.join(DATA, 'krx_ohlcv_cache');
    for (const [m, label] of [['KOSPI', '코스피'], ['KOSDAQ', '코스닥']]) {
      const f = fs.readdirSync(dir).filter(n => n.endsWith(`_${m}.json`)).sort().reverse()
        .find(n => (JSON.parse(fs.readFileSync(path.join(dir, n), 'utf-8')).rows || []).length);
      if (f) for (const r of JSON.parse(fs.readFileSync(path.join(dir, f), 'utf-8')).rows) _mk[r.code] = label;
    }
  }
  return _mk[code] || '';
}
// 충족 칸 = 조건 이름마다 한 줄 + 색 (🟢 충족 · 🔴 미충족 · ⚪ 판정 불가) — 2026-10-19 사용자 지시 (숫자 N/M 대신)
const GATE_LABELS = [['price', '가격'], ['bullish_candle', '양봉'], ['volume', '거래량'], ['foreign_net_buy', '외국인'], ['program_net_buy', '프로그램'], ['execution_strength', '체결강도']];
// 매수 판정 (2026-10-23 사용자 지시 · 근거 backtest_buy_gate_4of5.py) — 대시보드 충족 칸·Slack 표 공용 SSOT
//   가격 pass + 거래량·외국인·프로그램 중 3개 = 🟢 매수 · 2개 = 🟡 매수가능 · 그 외 = ⚪ 대기
//   양봉은 판정에서 빼고 양봉/음봉 참고 표시만 (양봉 조건을 넣은 조합이 16개 중 하위 8개 · 음봉일 진입 승률 28.6% > 양봉 25.0%)
//   entry_conditions.all_required_met(5/5) 판정 자체는 그대로 — 화면 표시 규칙이다
const BUY_ITEMS = [['volume', '거래량'], ['foreign_net_buy', '외국인'], ['program_net_buy', '프로그램']];
const ecSt = (ec, k) => (ec[k] && typeof ec[k] === 'object' ? ec[k].status : ec[k]);
function buyVerdict(ec) {
  if (!ec || !ec.required_total) return null;
  const miss = BUY_ITEMS.filter(([k]) => ecSt(ec, k) !== 'pass').map(([, l]) => l);
  const n = 3 - miss.length;
  const candle = { pass: '양봉', fail: '음봉' }[ecSt(ec, 'bullish_candle')] || '봉 미확정';
  const level = ecSt(ec, 'price') !== 'pass' || n < 2 ? 'wait' : n === 3 ? 'buy' : 'maybe';
  const text = level === 'buy' ? `🟢 3/3 매수 · ${candle}`
    : level === 'maybe' ? `🟡 2/3 매수가능 (${miss[0]}❌) · ${candle}`
    : `⚪ 대기 (${[...(ecSt(ec, 'price') !== 'pass' ? ['가격'] : []), ...miss].join('·')}❌)`;
  return { level, n, miss, candle, text };
}
const gateCell = ec => {
  if (!ec) return '<span class="hd-note">-</span>';
  const dot = st => st === 'pass' ? '🟢' : st === 'fail' ? '🔴' : '⚪';
  const v = buyVerdict(ec);
  return (v ? `<strong style="white-space:nowrap;">${v.text}</strong><br>` : '') + GATE_LABELS.filter(([k]) => ec[k] && ec[k].status && ec[k].status !== 'not_required')
    .map(([k, l]) => `<span style="white-space:nowrap;">${dot(ec[k].status)} ${l}${k === 'bullish_candle' ? '(참고)' : ''}</span>`).join('<br>').replace(/^/, '<div style="display:inline-block;text-align:left;">') + '</div>';
};

const rrStats = r => ({ signal_date: r.signal_date, themes: r.themes, n: r.n, win: r.win, ev_pct: r.ev_pct, fin_ok: r.fin_ok, fin: r.fin, disclosure: r.disclosure });
// 재무·공시 칸 — 악재 공시 > 재무 배지 순
const finCell = r => !r ? '-' : r.disclosure && r.disclosure.startsWith('악재') ? `⚠️ ${r.disclosure}` : r.fin_ok === true ? '✅ 3년 성장' : r.fin_ok === false ? `❌ ${r.fin}` : (r.fin || '-');

// 스윙 탭 맨 위 🎯 손익비 요약표 (2026-10-19 · 스윙/중장기 탭 개편) — 매 회차 state 로 다시 그린다
const SUMMARY_RE = /<section id="rr-swing-summary">[\s\S]*?<\/section>\n?/;
function renderSwingSummary(state) {
  const rows = Object.entries(state.managed).filter(([, m]) => m.p1)
    .sort((a, b) => (b[1].rr?.ev_pct ?? -99) - (a[1].rr?.ev_pct ?? -99));
  const td = (l, v) => `<td data-label="${l}">${v}</td>`;
  const gs = gateScores();
  const body = rows.map(([code, m], i) => `      <tr>${td('순위', i + 1)}${td('충족', gateCell(gs[code]))}${td('종목', `<strong>${m.name}</strong> <span class="hd-note">${marketOf(code)}</span>`)}${td('테마', (m.rr?.themes || []).slice(0, 3).join('<br>') || '-')}${td('경과', `D+${m.dplus ?? 0}`)}${td('신호일', m.rr ? `${m.rr.signal_date.slice(4, 6)}-${m.rr.signal_date.slice(6)}` : '-')}${td('1차', fmt(m.p1))}${td('2차', m.p2 ? fmt(m.p2) : '-')}${td('3차', m.p3 ? fmt(m.p3) : '-')}${td('손절', fmt(m.stop))}${td('목표', fmt(m.target))}${td('손절 폭', `−${((1 - m.stop / m.p1) * 100).toFixed(1)}%`)}${td('손익비', m.rr1 ?? '-')}${td('승률', m.rr ? `${Math.round(m.rr.win * 100)}%` : '-')}${td('재무·공시', finCell(m.rr))}</tr>`).join('\n');
  // 종목 카드는 화면에서 숨김(2026-10-19 사용자 지시) — 19:00 분석이 카드의 진입 조건을 SSOT 로 읽으므로 DOM 에는 남긴다
  return `<section id="rr-swing-summary">
  <style>#tab-swing .card[id$="-active"],#stock-filter-panel{display:none!important}</style>
  <h3 style="margin:8px 0;">🎯 손익비 돌파 <span class="hd-note">(최대 5종 · 08:30 KRX 확정봉 · 손절 = 돌파선 −1% 종가 · 손절 폭 2~5% · 매 회차 종가 &lt; 손절이면 삭제)</span></h3>
  <table class="stock-table" style="font-size:0.88em;">
    <thead><tr><th>순위</th><th>충족</th><th>종목</th><th>테마</th><th>경과</th><th>신호일</th><th>1차</th><th>2차</th><th>3차</th><th>손절</th><th>목표</th><th>손절 폭</th><th>손익비</th><th>승률</th><th>재무·공시</th></tr></thead>
    <tbody>
${body || '      <tr><td colspan="16">없음</td></tr>'}
    </tbody>
  </table>
  <p class="hd-note">충족 = 최신 16:30·19:00 분석 기준 매수 판정(가격 + 거래량·외국인·프로그램 3개 중 3개 🟢 매수 · 2개 🟡 매수가능 · 그 외 ⚪ 대기, 양봉은 참고) + 조건별 판정(🟢 충족 · 🔴 미충족 · ⚪ 판정 불가) · 경과 D+N = 편입 후 거래일 · 승률 = 신호 봉이 비슷한 과거 사례 중 60일 안 +10% 선도달 비율 · 1차·2차·3차(1:1:2)·손절·목표는 §4.4 기계 산식 · 재무 = 3개년 매출·순이익 연속 증가(표시만) · 올해 악재 공시 종목은 편입 전 제외</p>
</section>
`;
}
function patchSwingSummary(html, state) {
  const sec = renderSwingSummary(state);
  if (SUMMARY_RE.test(html)) return html.replace(SUMMARY_RE, () => sec);
  const tab = '<div class="tab-content active" id="tab-swing">';
  const at = html.indexOf(tab);
  if (at < 0) { log('스윙 탭 없음 — 요약표 생략'); return html; }
  return html.slice(0, at + tab.length) + '\n' + sec + html.slice(at + tab.length);
}

function latestAnalysis(dateIso) {
  const files = fs.readdirSync(DATA).filter(f => /^dante_analysis_\d{4}-\d{2}-\d{2}_\d{4}\.json$/.test(f)).sort();
  for (const f of files.reverse()) {
    if (dateIso && !f.includes(dateIso)) continue;
    const a = JSON.parse(fs.readFileSync(path.join(DATA, f), 'utf-8'));
    if (a.mode === 'recommend-only') return a;
  }
  return null;
}

function main() {
  // --summary-only(16:30·19:00): 편입 없이 손절·목표 이탈 퇴출 + 요약표 갱신만 (2026-10-26 · 목표 2026-10-28)
  const md0 = fs.readFileSync(MD, 'utf-8');
  const stocksJson = JSON.parse(fs.readFileSync(STOCKS, 'utf-8'));
  let html = fs.readFileSync(HTML, 'utf-8');
  const state = fs.existsSync(STATE) ? JSON.parse(fs.readFileSync(STATE, 'utf-8')) : { managed: {} };
  const { lines, i: li } = readLineup(md0);
  let line = lines[li];
  let stocks = stocksJson.stocks.slice();
  const today = DATE_ARG || new Date(Date.now() + 9 * 3600e3).toISOString().slice(0, 10);
  const mmdd = d => d.slice(5);
  const events = [];

  const analysis = REBUILD.length || SUMMARY_ONLY ? null : latestAnalysis(DATE_ARG);
  const exposure = analysis ? collectExposure(analysis) : [];
  const exposed = [];
  for (const sec of exposure.filter(s => INTAKE_SECTIONS.includes(s.key))) for (const r of sec.rows || []) {
    if (r.code && !exposed.some(e => e.code === r.code)) exposed.push({ ...r, section: sec.key });
  }
  const managedCodes = Object.keys(state.managed);
  const bars = krxBars([...new Set([...exposed.map(e => e.code), ...managedCodes, ...REBUILD])]);

  // ① 퇴출 — 자동 관리 중이고 라벨이 여전히 AUTO_LABEL 인 종목만
  for (const code of managedCodes) {
    const m = state.managed[code];
    const s = stocks.find(t => t.code === code);
    const seg = s && line.slice(line.indexOf(`(${code})`), line.indexOf(`(${code})`) + 80);
    if (!s || !(seg || '').includes(`**${AUTO_LABEL}`)) { delete state.managed[code]; events.push({ type: 'release', code, why: s ? '라벨 변경(사용자 관리로 이관)' : '라인업에 없음' }); continue; }
    const b = bars[code];
    if (!b || b.length < 60) continue;
    const x = indicators(b);
    const days = b.filter(r => r.date > m.intake_krx_date).length;
    m.dplus = days;
    const lc = lastClose(code, b);
    const why = m.stop && lc.close < m.stop ? `손절 이탈 (종가 ${fmt(lc.close)} < 손절 ${fmt(m.stop)} · ${lc.date.slice(4, 6)}-${lc.date.slice(6)})`
      : m.target && lc.close > m.target ? `목표 이탈 (종가 ${fmt(lc.close)} > 목표 ${fmt(m.target)} · ${lc.date.slice(4, 6)}-${lc.date.slice(6)})` // 매 회차 (2026-10-28)
      : SUMMARY_ONLY ? null
      : x.C < x.MA20 && x.C < x.MA60 ? `추세 이탈 (종가 ${fmt(x.C)} < MA20 ${fmt(x.MA20)}·MA60 ${fmt(x.MA60)})`
      : days >= EXPIRE_DAYS ? `만료 (편입 후 ${days}거래일 미체결)` : null;
    const xdate = x.date;
    if (!why) continue;
    line = removeFromLineup(line, stocks, code);
    stocks = stocks.filter(t => t.code !== code);
    const cb = cardBounds(html, `card-${code}-active`);
    if (cb) html = html.slice(0, cb.start) + html.slice(cb.end).replace(/^\r?\n/, '');
    delete state.managed[code];
    events.push({ type: 'exit', code, name: s.name, why, krx_date: xdate });
  }

  // 이미 관리 중인 종목이 오늘 🎯 손익비에 또 나오면 요약표 통계를 최신 스캔 값으로 갱신
  for (const e of exposed) {
    const m = state.managed[e.code];
    if (m && e.rr) m.rr = rrStats(e.rr);
  }

  // ② 재구축 대상은 기존 항목·카드를 먼저 걷어낸다 (옛 카드는 hidden 보존)
  // 이번 회차에 퇴출한 종목은 같은 회차에 다시 넣지 않는다 (다음 회차에 아직 노출이면 새 시나리오로 재편입)
  const exitedNow = new Set(events.filter(ev => ev.type === 'exit').map(ev => ev.code));
  // 라인업 최대 MAX_LINEUP 종 — 빈자리만 채운다 (exposed 는 기대값 순)
  const queue = exposed.filter(e => !stocks.some(t => t.code === e.code) && !exitedNow.has(e.code));
  let room = MAX_LINEUP - stocks.length;
  for (const code of REBUILD) {
    const s = stocks.find(t => t.code === code);
    const name = s?.name || bars[code]?.at(-1)?.name;
    if (!name) throw new Error(`--rebuild ${code}: 라인업·KRX 캐시 어디에도 없음`);
    queue.push({ code, name, score: null, line: null, section: s ? 'rebuild' : 'manual', replace: !!s });
  }

  // ③ 편입
  let newCards = '';
  for (const e of queue) {
    if (!e.replace && e.section !== 'manual' && room <= 0) { events.push({ type: 'skip', code: e.code, name: e.name, section: e.section, why: `라인업 ${MAX_LINEUP}종 가득` }); continue; }
    const sc = scenario(bars[e.code] || [], e.line);
    // 이미 퇴출 조건①(MA20·MA60 동시 하회)이면 편입하자마자 다음 회차에 빠지므로 편입하지 않는다
    if (sc.ok && sc.x.C < sc.x.MA20 && sc.x.C < sc.x.MA60) { events.push({ type: 'skip', code: e.code, name: e.name, section: e.section, why: `추세 이탈 상태 (종가 ${fmt(sc.x.C)} < MA20 ${fmt(sc.x.MA20)}·MA60 ${fmt(sc.x.MA60)})` }); continue; }
    if (sc.ok && sc.x.C > sc.target) { events.push({ type: 'skip', code: e.code, name: e.name, section: e.section, why: `종가 ${fmt(sc.x.C)} > 산출 목표 ${fmt(sc.target)} (T3-U 상태)` }); continue; }
    if (!sc.ok) { events.push({ type: 'skip', code: e.code, name: e.name, section: e.section, why: sc.reason }); continue; }
    e.date = today;
    if (e.replace) { // 시나리오가 선 뒤에만 옛 항목·카드를 걷어낸다 (옛 카드는 hidden 보존)
      line = removeFromLineup(line, stocks, e.code);
      stocks = stocks.filter(t => t.code !== e.code);
      const cb = cardBounds(html, `card-${e.code}-active`);
      if (cb) {
        const old = html.slice(cb.cardStart, cb.end)
          .replace(`id="card-${e.code}-active"`, `id="card-${e.code}-old_${today.replace(/-/g, '')}"`)
          .replace(/style="border-left:([^"]*)"/, 'style="border-left:$1;display:none;"');
        html = html.slice(0, cb.start) + `<!-- ===== 🗑️ ${e.name} (${e.code}) 옛 카드 hidden 보존 — ${today} 재구축(auto_intake.js --rebuild) ===== -->\n` + old + html.slice(cb.end);
      }
    }
    line = line.replace(/\s*$/, '') + ' · ' + lineupEntry(e, sc);
    stocks.push({ name: e.name, code: e.code, tag: 'active' });
    if (!e.replace) room--;
    newCards += renderCard(e, sc, mmdd);
    state.managed[e.code] = { name: e.name, section: e.section, score: e.score, intake_date: today, intake_krx_date: sc.x.date, target: sc.target, stop: sc.stop, dplus: 0, p1: sc.p1, p2: sc.p2, p3: sc.p3, rr1: +sc.rr1.toFixed(2),
      ...(e.rr ? { rr: rrStats(e.rr) } : {}) };
    events.push({ type: 'intake', code: e.code, name: e.name, section: e.section, hold: sc.hold,
      plan: `${sc.basis} ${fmt(sc.S)} · 1차 ${fmt(sc.p1)} / 2차 ${fmt(sc.p2)} / 3차 ${fmt(sc.p3)} · 손절 ${fmt(sc.stop)} · 목표 ${fmt(sc.target)} · ${sc.unit * 4}주 · 손익비 ${sc.rr.toFixed(2)}${sc.hold ? ' ⏸' + sc.holdReason : ''}` });
  }
  if (newCards) {
    const firstActive = html.search(/<div class="card" id="card-\d{6}-active"/);
    const anchor = html.indexOf(ACTIVE_ANCHOR); // 활성 카드가 0장일 때의 삽입 기준
    if (firstActive < 0 && anchor < 0) throw new Error('활성 카드 삽입 위치 없음');
    const insertAt = firstActive >= 0 ? cardBounds(html, html.slice(firstActive).match(/id="(card-\d{6}-active)"/)[1]).start : anchor + ACTIVE_ANCHOR.length + 1;
    html = html.slice(0, insertAt) + newCards + html.slice(insertAt);
  }

  // D+N 배지 갱신 (카드 h3 끝) — 변경이 D+ 뿐이어도 저장한다
  const html0 = html;
  for (const [code, m] of Object.entries(state.managed)) {
    const cb = cardBounds(html, `card-${code}-active`);
    if (!cb || m.dplus == null) continue;
    const badge = `<span class="dplus" style="margin-left:6px;padding:1px 6px;border-radius:8px;background:#1f2937;color:#58a6ff;font-size:0.8em;">D+${m.dplus}</span>`;
    const card = html.slice(cb.cardStart, cb.end);
    const next = /<span class="dplus"[^>]*>D\+\d+<\/span>/.test(card) ? card.replace(/<span class="dplus"[^>]*>D\+\d+<\/span>/, badge) : card.replace(/<\/h3>/, `${badge}</h3>`);
    html = html.slice(0, cb.cardStart) + next + html.slice(cb.end);
  }
  html = patchSwingSummary(html, state);
  for (const ev of events) log(`${ev.type} ${ev.name || ''}(${ev.code}) ${ev.why || ev.plan || ''}`);
  if (!events.length && html === html0) { log("변경 없음"); return events; }
  if (DRY) { log('--dry-run: 저장 생략'); return events; }

  lines[li] = line;
  // §6 헤더 종목 수 (체결 수는 그대로 · 미체결 = 활성 − 체결)
  const nActive = stocks.filter(s => s.tag === 'active').length;
  const hi = lines.findIndex(l => l.startsWith('## 6. 현재 종목 현황'));
  if (hi >= 0) lines[hi] = lines[hi].replace(/활성 \*\*\d+종\*\* = 체결 \*\*(\d+)종\*\* \+ 미체결 \*\*\d+종\*\*/, (m, filled) => `활성 **${nActive}종** = 체결 **${filled}종** + 미체결 **${nActive - filled}종**`);
  stocksJson.stocks = stocks;
  stocksJson._comment = String(stocksJson._comment || '').replace(/활성 \d+종/, `활성 ${stocks.filter(s => s.tag === 'active').length}종`);
  fs.writeFileSync(MD, lines.join('\n'), 'utf-8');
  fs.writeFileSync(STOCKS, JSON.stringify(stocksJson, null, 2) + '\n', 'utf-8');
  fs.writeFileSync(HTML, html, 'utf-8');
  fs.writeFileSync(STATE, JSON.stringify(state, null, 2) + '\n', 'utf-8');
  fs.appendFileSync(LOG, events.map(e => JSON.stringify({ at: today, ...e })).join('\n') + (events.length ? '\n' : ''), 'utf-8');
  log(`저장: 편입 ${events.filter(e => e.type === 'intake').length} · 퇴출 ${events.filter(e => e.type === 'exit').length} · 건너뜀 ${events.filter(e => e.type === 'skip').length}`);
  return events;
}

// 손절 이탈 퇴출 Slack 알림 (slack_send.js 는 require 금지 — 실발송되므로 웹훅 직접 호출)
function notifyStop(exits) {
  const text = `🛑 *손절·목표 이탈 — 스윙 탭에서 삭제* (${exits.length}종)\n${exits.map(e => `• *${e.name}* (${e.code}) — ${e.why}`).join('\n')}`;
  if (DRY) { log(`--dry-run: Slack 생략\n${text}`); return Promise.resolve(); }
  require('dotenv').config({ path: path.join(ROOT, '..', '.env') });
  const url = process.env.SLACK_WEBHOOK_URL;
  if (!url) { log('SLACK_WEBHOOK_URL 미설정 — 알림 생략'); return Promise.resolve(); }
  return fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username: process.env.SLACK_USERNAME || '단테 자동분석', icon_emoji: process.env.SLACK_ICON_EMOJI || ':chart_with_upwards_trend:', text }) })
    .then(r => log(`Slack 손절 알림 ${r.status}`));
}

if (require.main === module) {
  let events;
  try { events = main() || []; } catch (e) { console.error(`[auto_intake] 실패 — 아무것도 쓰지 않음: ${e.message}`); process.exit(1); }
  const exits = events.filter(e => e.type === 'exit' && /^(손절|목표) 이탈/.test(e.why));
  if (exits.length) notifyStop(exits).catch(e => log(`Slack 알림 실패: ${e.message}`));
}
module.exports = { scenario, tick, removeFromLineup, cardBounds, krxBars, lastClose, fmt, gateScores, gateCell, buyVerdict, marketOf };
