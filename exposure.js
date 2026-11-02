/**
 * exposure.js — 아침(08:30) 「📋 노출 종목」 목록 (slack_send.js · auto_intake.js 공용)
 * slack_send.js 는 require() 시 실발송되므로 목록 수집만 여기로 분리했다 (2026-10-12).
 * 자동 편입은 'rr'(🎯 손익비) 섹션만 — 나머지 섹션은 Slack 표시 전용 (2026-10-19).
 * 반환: [{ key, title, rows: [{code, name, score, line, label}] | null(데이터 없음) }]
 */
'use strict';
const fs = require('fs');
const path = require('path');

const DATA_DIR = path.join(__dirname, 'data');

function loadDataJson(prefix, dateIso) {
  const p = path.join(DATA_DIR, `${prefix}_${dateIso}.json`);
  if (!fs.existsSync(p)) return null;
  try { return JSON.parse(fs.readFileSync(p, 'utf-8')); }
  catch (e) { console.log(`${prefix} 로드 실패: ${e.message}`); return null; }
}

const score = r => r.dante_pullback_score ?? r.dante_bowl_score ?? r.dante_score ?? r.score ?? 0;
const scored = r => ({ code: r.code, name: r.name, score: score(r), line: r.line || null, label: `${r.name} ${score(r)}점` });

function collectExposure(analysis) {
  const b3 = loadDataJson('bowl3_quality', analysis.date);
  const rr = loadDataJson('rr_breakout', analysis.date);
  return [
    // 🎯 손익비 돌파 (2026-10-19) — auto_intake.js 는 이 섹션만 스윙 탭에 편입한다
    { key: 'rr', title: '🎯 손익비', rows: rr ? rr.picks.map(p => ({ code: p.code, name: p.name, score: null, line: p.line_, rr: p,
      label: `${p.name}${p.themes && p.themes.length ? `(${p.themes[0]})` : ''} 승${Math.round(p.win * 100)}%·기대${p.ev_pct >= 0 ? '+' : ''}${p.ev_pct}%` })) : null },
    // 🌊 눌림 · 💥 돌파 · 🧭 테마 줄은 2026-10-19 사용자 지시로 삭제 (스캐너는 밥그릇3 원천이라 계속 돈다)
    { key: 'bowl3', title: '🍚 밥그릇3', rows: b3 ? (b3.passed || []).map(scored) : null },
    // ⚡ 단타 돌파임박 줄은 2026-10-28 사용자 지시로 삭제 (단타 = 09:10 run_danta.bat 별도 Slack)
  ];
}

module.exports = { collectExposure, loadDataJson };
