/**
 * check_t3u.js — §4.2 T3-U 「미체결 목표가 초과 = 삭제」 점검 (2026-08-20 신설)
 *
 * 왜 필요한가: §4.2 3항 정렬키는 **도달 이격률의 절대값**이라, 미체결 종목이 위로
 * 42% 벌어져도 "최하단 표시"만 될 뿐 폐기 트리거가 걸리지 않는다. 2026-08-04
 * LIG디펜스 초과는 사용자가 물어봐서야 발견됐다. 그래서 매 회차 기계적으로 훑는다.
 *
 * 판정: **미체결(평단 없음)** 종목의 **종가**가 카드 **최종 목표가**를 초과하면 T3-U.
 *   - 장중 일시 초과는 대상 아님(§4.4 종가 기준 원칙)
 *   - 목표 1차(= §4.4-D 절반 익절선)만 넘긴 것은 대상 아님 — 최종 목표가 기준
 *   - 체결 종목은 §4.4-D 담당이라 제외
 *
 * 🚫 삭제를 자동 집행하지 않는다. §4.6.13 「임의 업데이트 금지」에 따라 **보고만** 한다.
 *
 * 사용: node check_t3u.js [--json]
 * 종료코드: 해당 0종이면 0, 1종 이상이면 0(정상 종료 · 파이프라인 중단 금지) — 발견은 stdout으로.
 */
const fs = require('fs');
const path = require('path');

const ROOT = __dirname;
const CACHE = path.join(ROOT, 'data', 'krx_ohlcv_cache');
const asJson = process.argv.includes('--json');

function log(m) { if (!asJson) console.log(m); }

// ── §6 라인업 SSOT 파싱 (종목 경계 = "이름(6자리)" 출현 지점)
function parseLineup() {
  const txt = fs.readFileSync(path.join(ROOT, 'AI단테.md'), 'utf-8');
  const m = txt.match(/^> - \*\*활성\*\*:(.+)$/m);
  if (!m) throw new Error('§6 라인업 SSOT 「> - **활성**:」 줄을 찾지 못했습니다');
  const line = m[1];
  const hits = [...line.matchAll(/([가-힣A-Za-z][가-힣A-Za-z0-9&\s]*?)\s*\((\d{6})\)/g)];
  return hits.map((h, i) => {
    const end = i + 1 < hits.length ? hits[i + 1].index : line.length;
    const body = line.slice(h.index + h[0].length, end);
    const tg = body.match(/목표\s*([0-9,·\s]+)/);
    const targets = tg ? [...tg[1].matchAll(/[0-9][0-9,]{2,}/g)].map(x => +x[0].replace(/,/g, '')) : [];
    return {
      name: h[1].trim(), code: h[2], targets,
      // 「평단」이 있으면 체결 계획 기술이지만, 미체결도 「전량 평단」을 적어두므로
      // 체결 여부는 §6 헤더의 체결/미체결 표기를 신뢰한다(아래 filled 판정).
      filled: /체결(?!\s*임박)/.test(body) && !/미체결/.test(body),
    };
  });
}

// ── rows 가 비어있지 않은 최신 캐시일의 종가 (§4.12.6 ② 빈 캐시 함정 회피)
function latestCloses() {
  const days = [...new Set(fs.readdirSync(CACHE)
    .filter(f => /^\d{8}_(KOSPI|KOSDAQ)\.json$/.test(f))
    .map(f => f.slice(0, 8)))].sort().reverse();
  for (const d of days) {
    const closes = {};
    for (const mk of ['KOSPI', 'KOSDAQ']) {
      const p = path.join(CACHE, `${d}_${mk}.json`);
      if (!fs.existsSync(p)) continue;
      for (const r of (JSON.parse(fs.readFileSync(p, 'utf-8')).rows || [])) {
        if (r.code && r.close != null) closes[r.code] = r.close;
      }
    }
    if (Object.keys(closes).length) return { date: d, closes };
  }
  return { date: null, closes: {} };
}

const lineup = parseLineup();
const { date, closes } = latestCloses();
if (!date) { console.error('종가 캐시를 찾지 못했습니다'); process.exit(1); }

const rows = [], unknown = [];
for (const s of lineup) {
  const close = closes[s.code];
  if (!s.targets.length || close == null) { unknown.push(s.name); continue; }
  const top = Math.max(...s.targets);
  rows.push({ ...s, close, top, overPct: +((close / top - 1) * 100).toFixed(2), t3u: close > top });
}
const hits = rows.filter(r => r.t3u && !r.filled);

if (asJson) {
  console.log(JSON.stringify({ date, checked: rows.length, unknown, hits }, null, 2));
} else {
  log(`[T3-U 점검] 기준 종가일 ${date} · 라인업 ${lineup.length}종 (판정 ${rows.length} / 미확인 ${unknown.length})`);
  for (const r of rows.sort((a, b) => b.overPct - a.overPct)) {
    log(`  ${r.t3u && !r.filled ? '🚨' : '  '} ${r.name}(${r.code}) 종가 ${r.close.toLocaleString()} / 최종목표 ${r.top.toLocaleString()} = ${r.overPct > 0 ? '+' : ''}${r.overPct}%`);
  }
  if (unknown.length) log(`  ⚠️ 목표/종가 미확인: ${unknown.join(', ')}`);
  log(hits.length
    ? `\n🚨 T3-U 해당 ${hits.length}종 — §4.2 T3-U에 따라 삭제 대상. 사용자 확인 후 정리할 것 (자동 삭제 안 함)`
    : `\n✅ T3-U 해당 0종`);
}
