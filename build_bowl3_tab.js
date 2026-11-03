/**
 * build_bowl3_tab.js — 🍚 밥그릇3 탭 (2026-10-19 사용자 지시) · 08:30 [B3b]
 *
 *  data/bowl3_quality_{오늘}.json(3구간 눌림, 2026-10-21 개정) 통과 종목을 점수(수급 강도) 순으로 최대 5종 → 대시보드 #bowl3-tab-summary 표로 교체.
 *  가격은 🎯 손익비와 같은 산식(auto_intake.scenario, 앵커 = max(MA112, MA224)): 손절 = 돌파선 −1% 종가 · 1·2·3차 · 목표.
 *  종가가 두 선 아래면 scenario 가 가장 가까운 지지선으로 내려간다(근거 칸에 표기).
 *  표시 전용 — 스윙 탭·§6·stocks.json 에는 넣지 않는다.
 *  data/bowl3_watch.json = 16:30·19:00 토스 캡처·분석 대상에 더하는 관찰 목록(가격 계획·거래량 범위 포함, 2026-10-19)
 *
 *  사용: node build_bowl3_tab.js [--dry-run] [--date=YYYY-MM-DD]
 */
'use strict';
const fs = require('fs');
const path = require('path');
const { scenario, krxBars, lastClose, fmt, gateScores, gateCell, marketOf } = require('./auto_intake.js');

const ROOT = __dirname;
const HTML = path.join(ROOT, '분석_대시보드.html');
const TOPN = 5;
const argv = process.argv.slice(2);
const DRY = argv.includes('--dry-run');
const today = (argv.find(a => a.startsWith('--date=')) || '').split('=')[1] || new Date(Date.now() + 9 * 3600e3).toISOString().slice(0, 10);
const SEC_RE = /<section id="bowl3-tab-summary">[\s\S]*?<\/section>/;
const log = m => console.log(`[bowl3_tab] ${m}`);

function themeOf() {
  try {
    const t = JSON.parse(fs.readFileSync(path.join(ROOT, 'data', 'naver_theme_map.json'), 'utf-8')).themes;
    const out = {};
    for (const [name, codes] of Object.entries(t)) for (const c of codes) {
      const n = name.replace(/\s*\([^)]*\)$/, '');
      if (!(out[c] = out[c] || []).includes(n)) out[c].push(n);
    }
    return out;
  } catch { return {}; }
}

// 손절 고정 + 이탈 삭제 (2026-10-19 사용자 지시) — 처음 들어온 날의 가격 계획을 저장하고 바꾸지 않는다.
//   KRX 종가 < 저장 손절 → 탭에서 삭제 + Slack 🛑 + BAN_DAYS 거래일 재진입 금지. 스캔에서 빠진 종목은 조용히 정리.
const STATE = path.join(ROOT, 'data', 'bowl3_state.json');
const BAN_DAYS = 20;

function notifyExits(exits) {
  const text = `🛑 *손절·목표 이탈 — 밥그릇3에서 삭제* (${exits.length}종)\n${exits.map(e => `• *${e.name}* (${e.code}) — ${e.why}`).join('\n')}`;
  if (DRY) { log(`--dry-run: Slack 생략\n${text}`); return Promise.resolve(); }
  require('dotenv').config({ path: path.join(ROOT, '..', '.env') });
  const url = process.env.SLACK_WEBHOOK_URL;
  if (!url) { log('SLACK_WEBHOOK_URL 미설정 — 알림 생략'); return Promise.resolve(); }
  return fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username: process.env.SLACK_USERNAME || '단테 자동분석', icon_emoji: process.env.SLACK_ICON_EMOJI || ':chart_with_upwards_trend:', text }) })
    .then(r => log(`Slack 손절 알림 ${r.status}`));
}

function main() {
  const p = path.join(ROOT, 'data', `bowl3_quality_${today}.json`);
  if (!fs.existsSync(p)) { log(`${path.basename(p)} 없음 — 건너뜀`); return []; }
  const b3 = JSON.parse(fs.readFileSync(p, 'utf-8'));
  const passed = (b3.passed || []).slice().sort((a, b) => (b.score ?? 0) - (a.score ?? 0));
  const state = fs.existsSync(STATE) ? JSON.parse(fs.readFileSync(STATE, 'utf-8')) : { managed: {}, banned: {} };
  const bars = krxBars([...new Set([...passed.map(r => r.code), ...Object.keys(state.managed)])]);
  const since = (code, krxDate) => (bars[code] || []).filter(r => r.date > krxDate).length;
  const exits = [];

  // ① 관리 중 종목: 손절 이탈 → 삭제·금지 / 스캔에서 빠짐 → 정리 / 아니면 D+ 갱신
  for (const [code, m] of Object.entries(state.managed)) {
    const last = lastClose(code, bars[code]); // 매 회차 최신 종가 (2026-10-26)
    if (last && last.close < m.stop) {
      exits.push({ code, name: m.name, why: `종가 ${fmt(last.close)} < 손절 ${fmt(m.stop)} (${last.date.slice(4, 6)}-${last.date.slice(6)} 종가)` });
      state.banned[code] = { name: m.name, exit_krx_date: last.date };
      delete state.managed[code];
    } else if (last && m.target && last.close > m.target) { // 목표 이탈 삭제 (2026-10-28)
      exits.push({ code, name: m.name, why: `종가 ${fmt(last.close)} > 목표 ${fmt(m.target)} (${last.date.slice(4, 6)}-${last.date.slice(6)} 종가)` });
      state.banned[code] = { name: m.name, exit_krx_date: last.date };
      delete state.managed[code];
    } else if (!passed.some(r => r.code === code)) {
      log(`정리 ${m.name}(${code}) — 오늘 밥그릇3 스캔에서 빠짐`);
      delete state.managed[code];
    } else m.dplus = since(code, m.first_krx_date);
  }
  for (const [code, b] of Object.entries(state.banned)) if (since(code, b.exit_krx_date) >= BAN_DAYS) delete state.banned[code];

  // ② 점수 순으로 5자리 — 관리 중이면 저장 계획, 새 종목이면 오늘 산출해 저장
  const top = [];
  for (const r of passed) {
    if (top.length >= TOPN) break;
    if (state.banned[r.code]) continue;
    let m = state.managed[r.code];
    if (!m) {
      const sc = scenario(bars[r.code] || [], '112+224');
      if (!sc.ok) { log(`건너뜀 ${r.name}(${r.code}) — ${sc.reason}`); continue; }
      if (sc.x.C > sc.target) { log(`건너뜀 ${r.name}(${r.code}) — 종가 ${fmt(sc.x.C)} > 목표 ${fmt(sc.target)}`); continue; }
      m = state.managed[r.code] = { name: r.name, first_date: today, first_krx_date: sc.x.date, dplus: 0,
        p1: sc.p1, p2: sc.p2, p3: sc.p3, stop: sc.stop, target: sc.target, rr1: +sc.rr1.toFixed(2), vol: sc.vol };
    }
    top.push({ r, m, close: (bars[r.code] || []).at(-1)?.close });
  }
  // 5자리 밖으로 밀린 관리 종목은 정리 (다음에 다시 들어오면 새로 산출)
  for (const code of Object.keys(state.managed)) if (!top.some(t => t.r.code === code)) delete state.managed[code];

  const th = themeOf();
  const td = (l, v) => `<td data-label="${l}">${v}</td>`;
  const gs = gateScores();
  const rows = top.map(({ r, m, close }, i) => {
    const sw = 1 - m.stop / m.p1;
    return `      <tr>${td('순위', i + 1)}${td('충족', gateCell(gs[r.code]))}${td('종목', `<strong>${r.name}</strong> <span class="hd-note">${marketOf(r.code)}</span>`)}${td('테마', (th[r.code] || []).slice(0, 2).join('<br>') || '-')}${td('경과', `D+${m.dplus ?? 0}`)}${td('점수', r.score ?? '-')}${td('현재가', close ? fmt(close) : '-')}${td('1차', fmt(m.p1))}${td('2차', fmt(m.p2))}${td('3차', fmt(m.p3))}${td('손절', fmt(m.stop))}${td('목표', fmt(m.target))}${td('손절 폭', `−${(sw * 100).toFixed(1)}%`)}${td('손익비', m.rr1.toFixed(2))}${td('재무·공시', `${r.fin_ok ? '✅ 3년 성장' : `➖ ${r.fin || '-'}`}<br>✅ 공시 청정`)}</tr>`;
  });
  const sec = `<section id="bowl3-tab-summary">
  <h3 style="margin:8px 0;">🍚 밥그릇3 <span class="hd-note">(${b3.anchor || ''} KRX 확정봉 · 점수 순 최대 ${TOPN}종 · 08:30 갱신 · 손절 = 돌파선(max MA112·224) −1% 종가 = 🎯 손익비와 동일)</span></h3>
  <table class="stock-table" style="font-size:0.88em;">
    <thead><tr><th>순위</th><th>충족</th><th>종목</th><th>테마</th><th>경과</th><th>점수</th><th>현재가</th><th>1차</th><th>2차</th><th>3차</th><th>손절</th><th>목표</th><th>손절 폭</th><th>손익비</th><th>재무·공시</th></tr></thead>
    <tbody>
${rows.join('\n') || '      <tr><td colspan="15">없음</td></tr>'}
    </tbody>
  </table>
  <p class="hd-note">밥그릇3 = 3구간 눌림 — 1구간(−40% 하락) · 2구간(하락 기간 0.5배 이상 박스) 뒤 장기선(112·224) 동시 돌파 후 20거래일 안 장기선까지 첫 눌림 + 양봉 + 외국인·기관 5일 순매수 + 올해 악재 공시 0건 (재무 3년 성장은 표시만) · 점수 = 외국인+기관 5일 순매수 ÷ 20일 평균 거래량(%) · 눌림일부터 60거래일 노출 · 충족 = 최신 16:30·19:00 분석 기준 매수 판정(가격 + 거래량·외국인·프로그램 3개 중 3개 🟢 매수 · 2개 🟡 매수가능 · 그 외 ⚪ 대기, 양봉은 참고) + 조건별 판정(🟢 충족 · 🔴 미충족 · ⚪ 판정 불가) · 경과 D+N = 탭에 들어온 뒤 거래일 · 1차·2차·3차·손절·목표는 들어온 날 §4.4 기계 산식으로 고정 · 매 회차(08:30·16:30·19:00) 최신 종가 &lt; 손절 또는 &gt; 목표면 삭제(Slack 알림) 후 ${BAN_DAYS}거래일 재진입 금지 · 표시 전용(스윙 탭 자동 편입 아님)</p>
</section>`;
  let html = fs.readFileSync(HTML, 'utf-8');
  if (!SEC_RE.test(html)) throw new Error('#bowl3-tab-summary 없음 (밥그릇3 탭 마크업 확인)');
  const next = html.replace(SEC_RE, () => sec);
  const watch = top.map(({ r, m }) => ({ name: r.name, code: r.code, score: r.score, p1: m.p1, p2: m.p2, p3: m.p3, stop: m.stop, target: m.target, vol: m.vol }));
  for (const e of exits) log(`exit ${e.name}(${e.code}) ${e.why}`);
  log(`${top.length}종: ${top.map(t => `${t.r.name} D+${t.m.dplus ?? 0}`).join(' · ') || '없음'}`);
  if (DRY) { console.log(sec.replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ')); return exits; }
  fs.writeFileSync(path.join(ROOT, 'data', 'bowl3_watch.json'), JSON.stringify({ date: today, krx_date: b3.anchor, stocks: watch }, null, 2) + '\n', 'utf-8');
  fs.writeFileSync(STATE, JSON.stringify(state, null, 2) + '\n', 'utf-8');
  if (next !== html) fs.writeFileSync(HTML, next, 'utf-8');
  return exits;
}

let exits;
try { exits = main() || []; } catch (e) { console.error(`[bowl3_tab] 실패 — 아무것도 쓰지 않음: ${e.message}`); process.exit(1); }
if (exits.length) notifyExits(exits).catch(e => log(`Slack 알림 실패: ${e.message}`));
