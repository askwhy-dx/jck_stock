/**
 * build_maejip.js — 📥 매집 탭 (16:30 장마감 후 보유 종목 수급 리포트)
 *
 *   node build_maejip.js                         체결 보유 종목(§6) 수급 리포트 → 대시보드 #maejip-section 교체
 *   node build_maejip.js --notify=ok|fail --reason="..."   Slack 으로 업데이트 유무 보고 (run_maejip.bat 마지막 단계)
 *
 * 대상: stocks.json(active) 중 AI단테.md §6 라벨이 체결/보유인 종목 (parsePositionMap — 분석기와 같은 판정)
 * 입력: images/{종목}_{오늘}.png · images/{종목}_투자자공매_{투자자|프로그램|공매도}_{오늘}.png (toss_capture.py)
 *       data/prices_{오늘}.json · data/krx_ohlcv_cache/ (이평·20일 평균 거래량 — 카드의 낡은 거래량 기준 대신 최신값)
 * 출력: data/maejip_{오늘}.json (Claude 가 Write) → 분석_대시보드.html #maejip-section
 */
const fs = require('fs');
const path = require('path');
require('dotenv').config({ path: path.join(__dirname, '..', '.env') });
const { runClaudeCli, parsePositionMap } = require('./claude_dante_analyze.js');

const ROOT = __dirname;
const DATA = path.join(ROOT, 'data');
const IMG = path.join(ROOT, 'images');
const HTML_PATH = path.join(ROOT, '분석_대시보드.html');
const KST = new Date(Date.now() + 9 * 3600 * 1000);
const TODAY = KST.toISOString().slice(0, 10);
const OUT = path.join(DATA, `maejip_${TODAY}.json`);
const DASHBOARD_URL = process.env.DASHBOARD_URL || 'https://jichang999-dotcom.github.io/tes2/financial/분석_대시보드.html';
const arg = k => (process.argv.find(a => a.startsWith(`--${k}=`)) || '').slice(k.length + 3);

const log = m => console.log(`[${new Date(Date.now() + 9 * 3600 * 1000).toISOString().replace('T', ' ').slice(0, 19)}] ${m}`);
const esc = s => String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
const fmt = n => Math.round(n).toLocaleString('en-US');

// KRX 캐시 → 종목별 최근 일봉 (휴장일 파일은 rows 0개라 자연히 빠짐)
function krxBars(codes) {
  const dir = path.join(DATA, 'krx_ohlcv_cache');
  const files = fs.readdirSync(dir).filter(f => /^\d{8}_(KOSPI|KOSDAQ)\.json$/.test(f)).sort().slice(-520);
  const bars = Object.fromEntries(codes.map(c => [c, []]));
  for (const f of files) {
    const d = JSON.parse(fs.readFileSync(path.join(dir, f), 'utf-8'));
    for (const r of (Array.isArray(d) ? d : d.rows || [])) if (bars[r.code]) bars[r.code].push(r);
  }
  return bars;
}

function metrics(b) {
  if (b.length < 20) return null;
  const avg = (arr, k) => arr.reduce((s, r) => s + r[k], 0) / arr.length;
  const ma = n => (b.length >= n ? fmt(avg(b.slice(-n), 'close')) : '—');
  const v20 = avg(b.slice(-20), 'volume');
  return {
    기준일: b.at(-1).date, 종가: fmt(b.at(-1).close),
    MA5: ma(5), MA20: ma(20), MA60: ma(60), MA120: ma(120), MA224: ma(224),
    거래량20일평균: fmt(v20), 눌림대역_0_7_1_5배: `${fmt(v20 * 0.7)}~${fmt(v20 * 1.5)}`,
  };
}

// §6 활성 줄에서 체결 보유 종목 + 그 종목의 원문 구간(평단·손절·목표)
function loadHoldings() {
  const active = JSON.parse(fs.readFileSync(path.join(ROOT, 'stocks.json'), 'utf-8')).stocks.filter(s => s.tag === 'active');
  const sec6 = fs.readFileSync(path.join(ROOT, 'AI단테.md'), 'utf-8').split(/\r?\n/).find(l => /^>\s*-\s*\*\*활성\*\*\s*:/.test(l)) || '';
  const pos = parsePositionMap(sec6, active);
  return active.filter(s => pos.get(s.code)?.status === 'holding').map(s => {
    const i = sec6.indexOf(`(${s.code})`);
    const next = Math.min(...active.map(o => sec6.indexOf(`(${o.code})`)).filter(j => j > i), sec6.length);
    return { ...s, segment: sec6.slice(i, next).replace(/·\s*[^·]*$/, '').trim() };
  });
}

function buildPrompt(stocks) {
  const prices = (() => { try { return JSON.parse(fs.readFileSync(path.join(DATA, `prices_${TODAY}.json`), 'utf-8')).stocks; } catch { return {}; } })();
  const bars = krxBars(stocks.map(s => s.code));
  const blocks = stocks.map(s => {
    const p = prices[s.name] || {};
    const img = k => path.join(IMG, `${s.name}_투자자공매_${k}_${TODAY}.png`).replace(/\\/g, '/');
    return `### ${s.name} (${s.code})
- 보유 현황(AI단테.md §6 원문): ${s.segment}
- 오늘 현재가 ${p.price || 'UNKNOWN'} (${p.change_pct ?? '?'}%)
- KRX 확정봉(전일까지) 지표: ${JSON.stringify(metrics(bars[s.code]) || 'UNKNOWN')}
- 이미지: [차트] ${path.join(IMG, `${s.name}_${TODAY}.png`).replace(/\\/g, '/')}  [투자자] ${img('투자자')}  [프로그램] ${img('프로그램')}  [공매도] ${img('공매도')}`;
  }).join('\n\n');

  return `당신은 주식단테 매매기법 수급 분석가입니다. 아래는 이미 매수해 보유 중인 종목입니다. 오늘(${TODAY}) 장마감 후 캡처한 이미지를 Read tool로 모두 읽고 수급을 판정한 뒤 종목마다 리포트를 쓰세요.

## 수급 판별 룰
- 🟢 매집: 외국인(+프로그램) 순매수 + 개인 매도 흡수 + 공매도 비율 감소 → 바닥 다지기 신호
- 🔴 이탈/분배: 외국인 순매도·지분율 하락 + 공매도 급증(>10%) + 프로그램 매도
- ⚪ 중립/혼조: 방향 불명확 또는 차익성(숏커버·패시브)
- 외국인 순매수만으로 매집 판정 금지 — 프로그램·공매도·지분율 추이를 교차 검증. 외국인 순매수 ≈ 프로그램 순매수면 바스켓(지수·ETF) 매수 가능성을 반드시 짚는다(토스 외국인 수치는 장외거래 포함).
- 숫자는 이미지에서 읽은 값만 쓴다. 못 읽으면 "UNKNOWN". 추정·창작 금지.

## 종목
${blocks}

## 리포트 (보유 종목 전부 reports 에 작성 — 판정과 무관)
각 리포트는 "수급은 들어오는가, 매도 압력은 어디에 있는가"를 풀어 설명한다:
1) 누가 사고 팔았나 — 오늘·전일·전전일 외국인/기관/개인/프로그램/공매도비율, 주체 교대 해석
2) 외국인 매수의 진정성 — 프로그램과 일치 여부·지분율·장외 포함 여부
3) 매도 압력의 정체 — 오늘 매도(거래량 배수=오늘 거래량 ÷ 위 20일 평균, 공매도, 개인)와 위쪽 매물대(이평 묶음)를 구분
4) 가격대 지도 — 위 저항(이평)·평단·현재가·아래 지지·손절선 (평단·손절·목표는 §6 원문 값만)
5) 종합 — 바닥/수급/위쪽 3줄 + 결론 한 문장
6) 앞으로 볼 신호 — 신호→해석 3~4개
매매 지시(사라/팔아라)는 쓰지 않는다. 손절·목표가 등 가격 수치는 새로 만들지 않는다.

## 출력 — Write tool로 ${OUT.replace(/\\/g, '/')} 에 아래 JSON 저장 (다른 파일 쓰기 금지)
{
  "date": "${TODAY}",
  "judged": [{"code": "", "name": "", "verdict": "🟢 매집|⚪ 중립|🔴 이탈", "reason": "한 줄 근거"}],
  "reports": [{
    "code": "", "name": "", "verdict": "🟢 매집|⚪ 중립|🔴 이탈", "price": "70,400", "change_pct": "+2.32",
    "headline": "한 문장 요약",
    "flow": {"columns": ["주체", "오늘", "전일", "전전일"], "rows": [["외국인", "+165,819", "-23,307", "-54,257"]], "note": "주체 교대 해석"},
    "authenticity": [["관찰", "해석"]],
    "pressure": {"rows": [["지표", "오늘", "의미"]], "note": "오늘 매도 vs 위쪽 매물대 결론"},
    "levels": [["가격대", "무엇이 있나"]],
    "summary": [["바닥", "🟢|🟡|🔴 상태"], ["수급", ""], ["위쪽", ""]],
    "conclusion": "결론 한 문장",
    "watch": [["신호", "해석"]]
  }]
}`;
}

const table = (head, rows) => `<table style="width:100%;border-collapse:collapse;margin:6px 0 10px;font-size:0.9em;">` +
  (head ? `<tr>${head.map(h => `<th style="text-align:left;border-bottom:1px solid #30363d;padding:4px 6px;color:#8b949e;">${esc(h)}</th>`).join('')}</tr>` : '') +
  (rows || []).map(r => `<tr>${r.map(c => `<td style="border-bottom:1px solid #21262d;padding:4px 6px;">${esc(c)}</td>`).join('')}</tr>`).join('') + `</table>`;

function render(d) {
  const color = v => (/매집/.test(v) ? '#3fb950' : /이탈/.test(v) ? '#f85149' : '#d29922');
  const h4 = t => `<h4 style="color:#58a6ff;margin:12px 0 4px;">${t}</h4>`;
  const note = t => (t ? `<p style="margin:2px 0 8px;color:#c9d1d9;">${esc(t)}</p>` : '');
  const cards = (d.reports || []).map(r => `
  <details class="highlight maejip-card" style="background:#161b22;border-left:4px solid ${color(r.verdict)};margin-bottom:16px;">
    <summary style="cursor:pointer;">
    <h3 style="display:inline;color:${color(r.verdict)};margin:0;">${esc(r.verdict)} · ${esc(r.name)} <span class="hd-note">(${esc(r.code)} · ${esc(r.price)}원 ${esc(r.change_pct)}%)</span></h3>
    <p style="margin:4px 0 0;"><strong>${esc(r.headline)}</strong></p>
    </summary>
    ${h4('1. 누가 사고 팔았나')}${table(r.flow?.columns, r.flow?.rows)}${note(r.flow?.note)}
    ${h4('2. 외국인 매수의 진정성')}${table(['관찰', '해석'], r.authenticity)}
    ${h4('3. 매도 압력의 정체')}${table(['지표', '오늘', '의미'], r.pressure?.rows)}${note(r.pressure?.note)}
    ${h4('4. 가격대 지도')}${table(['가격대', '무엇이 있나'], r.levels)}
    ${h4('5. 종합')}${table(['구분', '상태'], r.summary)}${note(r.conclusion)}
    ${h4('6. 앞으로 볼 신호')}${table(['신호', '해석'], r.watch)}
  </details>`).join('');
  return `<section id="maejip-section">
  <h2 style="color:#3fb950;">📥 매집 리포트 <span class="hd-note">(${esc(d.date)} 장마감 후 · 매수 체결 보유 종목)</span></h2>
  <p style="font-size:0.9em;color:#8b949e;margin:0 0 12px;">개장일 16:30 토스 수급(투자자·프로그램·공매도) 캡처 → 보유 종목마다 수급(매집·중립·이탈)과 매도 압력을 풀어쓴 리포트. 매매 지시가 아닌 수급 해석이며, 가격 룰(진입·손절·목표)은 스윙 카드가 기준입니다.</p>
  ${cards || '<p style="color:#8b949e;">보유(체결) 종목 없음</p>'}
</section>`;
}

async function notify(status, reason) {
  const url = process.env.SLACK_WEBHOOK_URL;
  if (!url) throw new Error('SLACK_WEBHOOK_URL 없음');
  let text;
  if (status === 'ok') {
    const d = JSON.parse(fs.readFileSync(OUT, 'utf-8'));
    const names = (d.reports || []).map(r => `${r.name} ${r.verdict}`);
    text = `📥 매집 리포트 ${TODAY} 16:30 — ✅ 업데이트 완료\n보유 ${names.length}종${names.length ? ': ' + names.join(', ') : ' (없음)'}\n<${DASHBOARD_URL}|대시보드 › 📥 매집 탭>`;
  } else {
    text = `📥 매집 리포트 ${TODAY} 16:30 — ❌ 업데이트 실패\n사유: ${reason || 'UNKNOWN'} · 로그 stock/logs/maejip_${TODAY}.log`;
  }
  const res = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ text, username: process.env.SLACK_USERNAME || '단테 자동분석', icon_emoji: process.env.SLACK_ICON_EMOJI || ':chart_with_upwards_trend:' }) });
  if (!res.ok) throw new Error(`Slack ${res.status}`);
  log(`Slack 발송: ${status}`);
}

async function main() {
  const status = arg('notify');
  if (status) return notify(status, arg('reason'));

  const holdings = loadHoldings();
  log(`체결 보유 ${holdings.length}종: ${holdings.map(s => s.name).join(', ') || '없음'}`);

  let d = { date: TODAY, judged: [], reports: [] };
  if (holdings.length) {
    const missing = holdings.filter(s => !fs.existsSync(path.join(IMG, `${s.name}_투자자공매_투자자_${TODAY}.png`)));
    if (missing.length) throw new Error(`오늘(${TODAY}) 수급 캡처 없음: ${missing.map(s => s.name).join(', ')}`);
    if (fs.existsSync(OUT)) fs.unlinkSync(OUT);
    const r = runClaudeCli(buildPrompt(holdings), 'maejip', 'Read,Write', 40);
    if (r.timedOut) throw new Error('Claude CLI 타임아웃');
    d = JSON.parse(fs.readFileSync(OUT, 'utf-8'));  // 없거나 깨지면 throw → 대시보드 미변경
  } else {
    fs.writeFileSync(OUT, JSON.stringify(d), 'utf-8');
  }
  log(`판정: ${(d.reports || []).map(r => `${r.name} ${r.verdict}`).join(' / ') || '보유 없음'}`);

  const html = fs.readFileSync(HTML_PATH, 'utf-8');
  const re = /<section id="maejip-section">[\s\S]*?<\/section>/;
  if (!re.test(html)) throw new Error('대시보드에 #maejip-section 없음');
  fs.writeFileSync(HTML_PATH, html.replace(re, () => render(d)), 'utf-8');
  log(`매집 탭 갱신: 리포트 ${(d.reports || []).length}종`);
}

if (require.main === module) {
  main().catch(e => { console.error('[build_maejip] 실패:', e.message); process.exit(1); });
}
module.exports = { render, metrics, loadHoldings };
