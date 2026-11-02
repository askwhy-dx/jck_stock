/**
 * build_danta_tab.js — ⚡ 단타인기 탭 #danta-tab-summary (2026-10-28 개편 · 평일 09:10 run_danta.bat)
 *
 *  거래대금 20위 안 + 네이버 인기 순위가 거래대금 순위보다 높은 종목(danta_pop_{오늘}.json, screen_danta_popular.py).
 *  3거래일 보유 단타 — 진입·손절·목표 산식 없음. 표시 전용(스윙 탭 자동 편입 아님).
 *  (10-21~10-27 🌙 오버나잇·09:15 매수·돌파임박은 10-28 사용자 지시로 삭제)
 *
 *  사용: node build_danta_tab.js [--dry-run] [--date=YYYY-MM-DD]
 */
'use strict';
const fs = require('fs');
const path = require('path');
const { fmt } = require('./auto_intake.js');

const ROOT = __dirname;
const HTML = path.join(ROOT, '분석_대시보드.html');
const argv = process.argv.slice(2);
const DRY = argv.includes('--dry-run');
const today = (argv.find(a => a.startsWith('--date=')) || '').split('=')[1] || new Date(Date.now() + 9 * 3600e3).toISOString().slice(0, 10);
const SEC_RE = /<section id="danta-tab-summary">[\s\S]*?<\/section>/;
const log = m => console.log(`[danta_tab] ${m}`);

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

const td = (l, v) => `<td data-label="${l}">${v}</td>`;

function main() {
  const th = themeOf();
  const p = path.join(ROOT, 'data', `danta_pop_${today}.json`);
  const d = fs.existsSync(p) ? JSON.parse(fs.readFileSync(p, 'utf-8')) : null;
  const head = ['순위', '종목', '테마', '거래대금 순위', '인기 순위', '가격', '등락', '거래대금'];
  const rows = (d ? d.picks : []).map((r, i) => `      <tr>${td('순위', i + 1)}${td('종목', `<span class="hd-note">${r.market}</span> <strong>${r.name}</strong>`)}${td('테마', (th[r.code] || []).slice(0, 2).join('<br>') || '-')}${td('거래대금 순위', `${r.value_rank}위`)}${td('인기 순위', `${r.pop_rank}위`)}${td('가격', fmt(r.price))}${td('등락', `${r.chg_pct > 0 ? '+' : ''}${r.chg_pct}%`)}${td('거래대금', `${fmt(r.value_eok)}억`)}</tr>`);
  const sec = `<section id="danta-tab-summary">
  <h3 style="margin:8px 0;">⚡ 단타인기 <span class="hd-note">(${d ? `${d.date} ${d.time} 네이버 증권` : '오늘 없음'})</span></h3>
  <table class="stock-table" style="font-size:0.88em;">
    <thead><tr>${head.map(h => `<th>${h}</th>`).join('')}</tr></thead>
    <tbody>
${rows.join('\n') || `      <tr><td colspan="${head.length}">없음</td></tr>`}
    </tbody>
  </table>
  <p class="hd-note">평일 09:10 네이버 증권 거래대금 상위(코스피+코스닥 일반 주식, ETF·ETN 제외) 20위 안에서 인기 검색 순위가 거래대금 순위보다 높은 종목 · 거래대금 순 · 매매 = 3거래일 보유 단타 · ⚠️ 백테스트 없음(네이버 인기 순위 과거 이력 없음 — 매일 기록을 쌓아 성과 측정 예정) · 표시 전용</p>
</section>`;
  const html = fs.readFileSync(HTML, 'utf-8');
  if (!SEC_RE.test(html)) throw new Error('#danta-tab-summary 없음 (단타 탭 마크업 확인)');
  log(`거래대금×인기 ${d ? d.picks.length : '-'}종`);
  if (DRY) { console.log(sec.replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ')); return; }
  const next = html.replace(SEC_RE, () => sec);
  if (next !== html) fs.writeFileSync(HTML, next, 'utf-8');
}

try { main(); } catch (e) { console.error(`[danta_tab] 실패 — 아무것도 쓰지 않음: ${e.message}`); process.exit(1); }
