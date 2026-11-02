/**
 * reorder_active_cards.js — 스윙 활성 카드 배치 3원칙 자동 적용 (§4.2 · [[feedback_active_card_order_buydate]])
 *
 *  배치 규칙 — AI단테.md §4.2가 SSOT (2026-08-20 진입조건 게이트 개정):
 *    ① 체결 카드가 상단        — 매수일 오름차순 (같은 날이면 종목코드 오름차순)
 *    ② 미체결 그룹 내부 정렬   — 최신 analysis JSON의 entry_conditions 전부 충족 → 충족 개수 → 티어(T1→T2→T3) → 도달 이격률
 *    ③ 도달 이격률 = |현재가 − 1차 진입가| ÷ **1차 진입가** × 100 (%)   ← 분모는 진입가지 현재가가 아니다
 *    ④ 티어  T1 🟢 이격률 ≤ 2.0% (당일 트리거 가능) / T2 🟡 > 2.0% / T3 ⚪ 계획 무효
 *            T3 = ① 현재가가 손절선 이탈 · ② 1차 미산출 · ③ 1차 근거 지지선 하회
 *            T3 내부는 복구 난이도 오름차순(③ → ① → ②)
 *
 *  ⚠️ 기계 판정 불가 항목: T3 조건 ③(1차 근거 지지선 하회)은 카드 텍스트에서 자동 판별할 수 없다.
 *     본 스크립트는 ①(손절 이탈)·②(1차 미산출)만 T3로 잡는다. ③은 사람이 판정해 카드에 반영해야 하고,
 *     동률 시 2차 키인 「손익비 내림차순」도 카드에서 안정적으로 파싱되지 않아 종목코드로 대체한다.
 *     → 이 2개는 자동화 사각지대이므로 회차 로그에 남긴다(무증상 오정렬 방지).
 *
 *  부가 기능: 카드 헤더 `badge-swing` 옆에 📍 임박도 배지를 삽입/갱신한다
 *            (2026-08-11 사용자 지시. §4.2 삭제리스트 #10 「존치 배지 1종」의 예외로 명시 승인됨)
 *
 *  ⚠️ 시세 의존: 임박도는 카드의 `현재가` 배너를 읽는다. 반드시 시세 패치(toss 캡처 반영) **뒤에**
 *     실행해야 한다. run_daily_dante.bat에서는 업로드 직전 단계에 배치되어 있다.
 *
 *  이동 단위 = [카드 바로 앞 주석 블록 + <div class="card" id="card-XXXXXX-active"> ... </div>]
 *  카드 사이의 hidden 보존 카드·기타 주석은 슬롯 사이 고정물로 취급해 원위치에 남긴다.
 *
 *  사용:
 *    node reorder_active_cards.js              재정렬 + 배지 갱신 후 저장
 *    node reorder_active_cards.js --dry-run    저장 없이 판정 결과만 출력
 *    node reorder_active_cards.js --no-badge   재정렬만 (배지 미삽입)
 *  종료코드: 0=정상(변경 여부 무관) / 1=파싱 실패 (fail-safe: 못 읽으면 건드리지 않는다)
 */
'use strict';
const fs = require('fs');
const path = require('path');

const FILE = path.join(__dirname, '분석_대시보드.html');
const DATA_DIR = path.join(__dirname, 'data');
const DRY = process.argv.includes('--dry-run');
const NO_BADGE = process.argv.includes('--no-badge');

const REQUIRED_ENTRY_CONDITIONS = [
  'price',
  'bullish_candle',
  'volume',
  'foreign_net_buy',
  'program_net_buy',
];

function normalizedStatus(value) {
  const raw = (value && typeof value === 'object') ? value.status : value;
  const s = String(raw || '').trim().toLowerCase();
  if (s === 'pass' || s === 'true' || s === '충족') return 'pass';
  if (s === 'fail' || s === 'false' || s === '미충족') return 'fail';
  if (s === 'not_required' || s === 'not-required' || s === '해당없음') return 'not_required';
  return 'unknown';
}

function signedNumber(value) {
  if (typeof value === 'number') return Number.isFinite(value) ? value : null;
  if (value === undefined || value === null) return null;
  const cleaned = String(value).replace(/,/g, '').match(/[+-]?\d+(?:\.\d+)?/);
  if (!cleaned) return null;
  const n = Number(cleaned[0]);
  return Number.isFinite(n) ? n : null;
}

/**
 * 분석 JSON의 구조화 진입조건을 fail-closed로 재검증한다.
 * 외국인·프로그램은 당일 숫자가 양수여야 pass다. 사용자가 직접 확인한 값은
 * 숫자를 주지 않았더라도 source=user_confirmed일 때만 명시 status를 허용한다.
 */
function entryConditionGate(stock) {
  const raw = stock && stock.entry_conditions && typeof stock.entry_conditions === 'object'
    ? stock.entry_conditions : {};
  const statuses = {};

  for (const key of REQUIRED_ENTRY_CONDITIONS) {
    const item = raw[key];
    let status = normalizedStatus(item);
    if (key === 'foreign_net_buy' || key === 'program_net_buy') {
      const value = signedNumber(item && typeof item === 'object' ? item.value_shares : null);
      const source = String((item && typeof item === 'object' ? item.source : '') || raw.source || '');
      if (value !== null) status = value > 0 ? 'pass' : 'fail';
      else if (!/user_confirmed/i.test(source)) status = 'unknown';
    }
    statuses[key] = status;
  }

  const strengthStatus = normalizedStatus(raw.execution_strength);
  const required = [...REQUIRED_ENTRY_CONDITIONS];
  if (strengthStatus !== 'not_required' && raw.execution_strength !== undefined) {
    required.push('execution_strength');
    statuses.execution_strength = strengthStatus;
  }

  const passCount = required.filter(k => statuses[k] === 'pass').length;
  const unknown = required.filter(k => statuses[k] === 'unknown');
  const failed = required.filter(k => statuses[k] === 'fail');
  return {
    met: passCount === required.length,
    known: unknown.length === 0,
    passCount,
    total: required.length,
    failed,
    unknown,
    source: String(raw.source || 'UNKNOWN'),
  };
}

function compareUnfilledInfo(A, B) {
  if (A.conditionMet !== B.conditionMet) return A.conditionMet ? -1 : 1;
  if (A.conditionPassCount !== B.conditionPassCount) return B.conditionPassCount - A.conditionPassCount;
  const TIER_RANK = { T1: 0, T2: 1, T3: 2 };
  if (TIER_RANK[A.tier] !== TIER_RANK[B.tier]) return TIER_RANK[A.tier] - TIER_RANK[B.tier];
  const ga = A.gap === null ? Infinity : A.gap;
  const gb = B.gap === null ? Infinity : B.gap;
  return ga - gb || A.code.localeCompare(B.code);
}

/** Slack과 동일한 최신 분석 JSON에서 decision과 구조화 진입조건을 읽는다. */
function loadDecisionMap() {
  let files;
  try {
    files = fs.readdirSync(DATA_DIR)
      .filter(name => /^dante_analysis_\d{4}-\d{2}-\d{2}_\d{4}\.json$/.test(name))
      .sort((a, b) => a.localeCompare(b));
  } catch (_) {
    return { file: null, map: new Map(), error: 'data 디렉터리 읽기 실패' };
  }
  if (!files.length) return { file: null, map: new Map(), error: '분석 JSON 없음' };

  const file = files[files.length - 1];
  try {
    const data = JSON.parse(fs.readFileSync(path.join(DATA_DIR, file), 'utf8'));
    if (!Array.isArray(data.stocks)) {
      return { file, map: new Map(), error: 'stocks 배열 없음' };
    }
    const map = new Map();
    for (const stock of data.stocks) {
      const code = String(stock.code || '').padStart(6, '0');
      if (!/^\d{6}$/.test(code)) continue;
      const decision = String(stock.decision || '');
      map.set(code, {
        decision,
        ready: /매수\s*준비/.test(decision),
        gate: entryConditionGate(stock),
      });
    }
    return { file, map, error: null };
  } catch (err) {
    return { file, map: new Map(), error: err.message };
  }
}

function log(msg) {
  const ts = new Date(Date.now() + 9 * 3600 * 1000).toISOString().replace('T', ' ').slice(0, 19);
  console.log(`[${ts}] ${msg}`);
}

function num(s) {
  if (s === undefined || s === null) return null;
  const v = parseInt(String(s).replace(/[^\d]/g, ''), 10);
  return Number.isFinite(v) ? v : null;
}

/** 카드 div의 짝 맞는 </div> 위치를 찾아 [start,end) 반환 */
function cardSpan(html, start) {
  const tag = /<div\b|<\/div>/g;
  tag.lastIndex = start;
  let depth = 0, m;
  while ((m = tag.exec(html))) {
    if (m[0] === '</div>') {
      depth--;
      if (depth === 0) return m.index + 6;
    } else depth++;
  }
  return -1;
}

/** 카드 바로 앞에 붙은 주석 블록의 시작 위치 (없으면 카드 시작) */
function unitStart(html, cardStart) {
  let i = cardStart;
  while (i > 0 && /\s/.test(html[i - 1])) i--;
  if (html.slice(i - 3, i) !== '-->') return cardStart;
  const open = html.lastIndexOf('<!--', i);
  return open === -1 ? cardStart : open;
}

function parseCard(seg, decisionMap) {
  const code = (seg.match(/id="card-(\d+)-active"/) || [])[1] || null;
  const h3 = (seg.match(/<h3[^>]*>([\s\S]*?)<\/h3>/) || [])[1] || '';
  const name = h3.replace(/<[^>]+>/g, '').replace(/\s+/g, ' ').trim().split('(')[0].trim();

  // 분류: badge-swing 칩 문구에 '미체결'이 있으면 미체결
  const chip = (seg.match(/badge badge-swing">[\s\S]*?<span class="chip-note">([\s\S]*?)<\/span>/) || [])[1] || '';
  const filled = !/미체결/.test(chip) && /체결/.test(chip);

  // 매수일: 체결 카드 h3의 (MM-DD ...) 표기
  let buyDate = null;
  const md = h3.match(/\((\d{2})-(\d{2})(?:\s|\))/);
  if (md) buyDate = `${md[1]}-${md[2]}`;

  // 현재가 배너. 일부 카드는 라벨이 '기준가'(4배너 표준 이탈)라 폴백을 둔다.
  let cur = num((seg.match(/현재가[\s\S]{0,300}?<div class="value[^"]*">\s*([\d,]+)\s*원/) || [])[1]);
  let curLabel = '현재가';
  if (cur === null) {
    const alt = seg.match(/<div class="summary-grid">[\s\S]{0,400}?<div class="label">([^<]*?)\s*(?:<span[\s\S]*?<\/span>)?\s*<\/div>\s*<div class="value[^"]*">\s*([\d,]+)\s*원/);
    if (alt) { curLabel = alt[1].trim(); cur = num(alt[2]); }
  }

  // 다음 대기 차수 + 손절선 (portfolio-table)
  let nextName = null, nextPrice = null, stop = null;
  const tbody = (seg.match(/<table class="portfolio-table">[\s\S]*?<tbody>([\s\S]*?)<\/tbody>/) || [])[1] || '';
  const rowRe = /<tr>\s*<td data-label="(?:차수|스윙 분할 계획)">([\s\S]*?)<\/td>\s*<td data-label="매수가">([\s\S]*?)<\/td>/g;
  let r;
  while ((r = rowRe.exec(tbody))) {
    const label = r[1].replace(/<[^>]+>/g, '').replace(/\s+/g, ' ').trim();
    const p = num(r[2]);
    if (/손절/.test(label)) { if (stop === null && p !== null) stop = p; continue; }
    if (/목표/.test(label)) continue;
    if (!/대기/.test(label)) continue;              // 체결·없음 행은 건너뜀
    if (p === null) continue;
    if (nextPrice === null) {
      nextName = label.replace(/[⏳✅]/g, '').replace(/대기[\s\S]*$/, '').trim();
      nextPrice = p;
    }
  }

  // §4.2 ③ 도달 이격률 — 분모는 **1차 진입가** (현재가 아님)
  const gap = (cur && nextPrice) ? Math.abs(cur - nextPrice) / nextPrice * 100 : null;
  const side = (cur && nextPrice) ? (cur >= nextPrice ? '위' : '아래') : null;

  // Slack과 동일한 analysis JSON decision이 매수준비 판정 SSOT다.
  const analysisDecision = decisionMap.get(code) || null;
  const ready = analysisDecision ? analysisDecision.ready : false;
  const readySource = analysisDecision ? 'analysis' : null;
  const gate = analysisDecision ? analysisDecision.gate : entryConditionGate(null);

  // §4.2 ④ 티어. ③(1차 근거 지지선 하회)은 기계 판정 불가 — 미구현으로 로그에 남긴다.
  let tier = 'T2', tierWhy = '이격률 > 2.0%';
  if (nextPrice === null) { tier = 'T3'; tierWhy = '② 1차 미산출'; }
  else if (stop !== null && cur !== null && cur < stop) { tier = 'T3'; tierWhy = '① 손절선 이탈'; }
  else if (gap !== null && gap <= 2.0) { tier = 'T1'; tierWhy = '이격률 ≤ 2.0%'; }

  return {
    code, name, filled, buyDate, cur, curLabel, nextName, nextPrice, stop, gap, side,
    ready, readySource, decision: analysisDecision?.decision || null,
    conditionMet: gate.met,
    conditionKnown: gate.known,
    conditionPassCount: gate.passCount,
    conditionTotal: gate.total,
    conditionFailed: gate.failed,
    conditionUnknown: gate.unknown,
    conditionSource: gate.source,
    tier, tierWhy,
  };
}

const TIER_STYLE = {
  T1: 'background:#12261a;color:#3fb950;',
  T2: 'background:#3a2e0d;color:#d29922;',
  T3: 'background:#21262d;color:#8b949e;',
};

function badgeHtml(info, rank, total) {
  if (info.gap === null || !info.nextPrice) {
    return `<span class="badge badge-proximity" style="background:#21262d;color:#8b949e;">📍 T3 임박도 산출 불가 <span class="chip-note">(${info.tierWhy})</span></span>`;
  }
  const pct = info.gap.toFixed(2);
  if (info.filled) {
    return `<span class="badge badge-proximity" style="background:#1f2d3d;color:#58a6ff;">📍 다음 ${info.nextName} <span class="chip-note">(${info.nextPrice.toLocaleString()}원 · 현재가 ${pct}% ${info.side})</span></span>`;
  }
  if (info.conditionMet) {
    return `<span class="badge badge-proximity" style="background:#12261a;color:#3fb950;">📍 🔔 조건충족 체결 임박 ${rank}/${total}위 · ${info.tier} <span class="chip-note">(${info.nextName} ${info.nextPrice.toLocaleString()}원 · 이격 ${pct}% · 조건 ${info.conditionPassCount}/${info.conditionTotal})</span></span>`;
  }
  return `<span class="badge badge-proximity" style="${TIER_STYLE[info.tier]}">📍 조건대기 ${rank}/${total}위 · ${info.tier} <span class="chip-note">(${info.nextName} ${info.nextPrice.toLocaleString()}원 · 이격 ${pct}% · 조건 ${info.conditionPassCount}/${info.conditionTotal})</span></span>`;
}

function patchBadge(seg, info, rank, total) {
  // 기존 임박도 배지 제거 (idempotent)
  let out = seg.replace(/\s*<span class="badge badge-proximity"[\s\S]*?<\/span>\s*<\/span>/g, '');
  const swing = out.match(/<span class="badge badge-swing">[\s\S]*?<\/span>\s*<\/span>/);
  if (!swing) return out;
  const ins = `${swing[0]}\n      ${badgeHtml(info, rank, total)}`;
  return out.replace(swing[0], ins);
}

function main() {
  let html = fs.readFileSync(FILE, 'utf8');
  const decisions = loadDecisionMap();
  if (decisions.error) {
    log(`매수준비 판정 JSON 오류 (${decisions.file || '-'}: ${decisions.error}) - 재정렬 중단, HTML 무수정`);
    process.exit(1);
  }

  // 1) 활성 카드 단위 수집
  const re = /<div class="card" id="card-\d+-active"/g;
  const units = [];
  let m;
  while ((m = re.exec(html))) {
    const cStart = m.index;
    const cEnd = cardSpan(html, cStart);
    if (cEnd === -1) { log(`🚨 카드 닫는 태그 파싱 실패 @${cStart} — 중단`); process.exit(1); }
    units.push({ start: unitStart(html, cStart), end: cEnd, cardStart: cStart });
  }
  if (units.length === 0) { log('🚨 활성 카드 0개 — 파싱 실패로 간주하고 중단'); process.exit(1); }

  // 2) 파싱
  units.forEach(u => {
    u.text = html.slice(u.start, u.end);
    u.info = parseCard(html.slice(u.cardStart, u.end), decisions.map);
  });

  const bad = units.filter(u => !u.info.code || u.info.cur === null);
  if (bad.length) {
    log(`🚨 현재가/코드 파싱 실패 ${bad.length}종 (${bad.map(b => b.info.code || '?').join(', ')}) — fail-safe 중단`);
    process.exit(1);
  }
  const missingDecisions = units.filter(u => !decisions.map.has(u.info.code));
  if (missingDecisions.length) {
    log(`매수준비 decision 누락 ${missingDecisions.length}종 (${missingDecisions.map(u => u.info.code).join(', ')}) - 재정렬 중단, HTML 무수정`);
    process.exit(1);
  }

  // 3) 정렬 — ① 체결 상단(매수일 오름차순) ② 미체결 임박도 오름차순
  const filled = units.filter(u => u.info.filled)
    .sort((a, b) => (a.info.buyDate || '99-99').localeCompare(b.info.buyDate || '99-99')
      || a.info.code.localeCompare(b.info.code));
  // 미체결: ① 진입조건 전부 충족 → ② 조건 충족 개수 → ③ 티어 → ④ 이격률 → 종목코드
  const unfilled = units.filter(u => !u.info.filled)
    .sort((a, b) => compareUnfilledInfo(a.info, b.info));
  const ordered = [...filled, ...unfilled];

  // 4) 배지 갱신
  if (!NO_BADGE) {
    const qualifiedTotal = unfilled.filter(u => u.info.conditionMet).length;
    const waitingTotal = unfilled.length - qualifiedTotal;
    let qualifiedRank = 0, waitingRank = 0;
    unfilled.forEach(u => {
      const rank = u.info.conditionMet ? ++qualifiedRank : ++waitingRank;
      const total = u.info.conditionMet ? qualifiedTotal : waitingTotal;
      u.text = patchBadge(u.text, u.info, rank, total);
    });
    filled.forEach(u => { u.text = patchBadge(u.text, u.info, 0, unfilled.length); });
  }

  // 5) 보고
  log(`활성 ${units.length}종 = 체결 ${filled.length} + 미체결 ${unfilled.length}`);
  const readyNames = unfilled.filter(u => u.info.ready).map(u => u.info.name);
  log(`매수준비 판정: ${decisions.file} · ${readyNames.length}종 (${readyNames.join(', ') || '없음'})`);
  const qualifiedNames = unfilled.filter(u => u.info.conditionMet).map(u => u.info.name);
  const unknownNames = unfilled.filter(u => !u.info.conditionKnown).map(u => u.info.name);
  log(`진입조건 게이트: 충족 ${qualifiedNames.length}종 (${qualifiedNames.join(', ') || '없음'}) · UNKNOWN ${unknownNames.length}종 (${unknownNames.join(', ') || '없음'})`);
  ordered.forEach((u, i) => {
    const o = u.info;
    const was = units.indexOf(u) + 1;
    const mark = was === i + 1 ? ' ' : (was > i + 1 ? '▲' : '▼');
    const g = o.gap === null ? '    n/a' : `${o.gap.toFixed(2)}%`.padStart(7);
    const tier = o.filled ? '  -' : `${o.conditionMet ? 'OK' : 'WAIT'}-${o.tier}`;
    console.log(`  ${String(i + 1).padStart(2)}${mark} ${o.filled ? '체결  ' : '미체결'} ${tier} ${o.name.padEnd(12)} 현재 ${String(o.cur).padStart(8)} | ${(o.nextName || '-').padEnd(4)} ${String(o.nextPrice || '-').padStart(8)} | 조건 ${o.conditionPassCount}/${o.conditionTotal} · 이격 ${g} (was #${was})`);
  });
  // 자동화 사각지대 고지 (무증상 오정렬 방지)
  const t3 = ordered.filter(u => !u.info.filled && u.info.tier === 'T3');
  log(`⚠️ 자동 판정 제외: T3 조건③(1차 근거 지지선 하회)은 기계 판별 불가 · 동률 2차키(손익비 내림차순)는 종목코드로 대체` +
      ` — 현재 T3 ${t3.length}종${t3.length ? ' (' + t3.map(u => `${u.info.name}:${u.info.tierWhy}`).join(', ') + ')' : ''}`);
  const odd = ordered.filter(u => u.info.curLabel !== '현재가');
  if (odd.length) log(`⚠️ 4배너 1번 라벨 이탈 ${odd.length}종 — ${odd.map(u => `${u.info.name}(${u.info.curLabel})`).join(', ')} · 표준은 '현재가'`);

  // 6) 슬롯 재조립 (카드 사이 고정물은 제자리 유지)
  let out = html.slice(0, units[0].start);
  for (let i = 0; i < units.length; i++) {
    out += ordered[i].text;
    const fixed = (i + 1 < units.length) ? html.slice(units[i].end, units[i + 1].start) : '';
    out += fixed;
  }
  out += html.slice(units[units.length - 1].end);

  const changed = out !== html;
  if (DRY) { log(`--dry-run: 저장 생략 (변경 ${changed ? '있음' : '없음'})`); return; }
  if (!changed) { log('변경 없음 — 저장 생략'); return; }
  fs.writeFileSync(FILE, out, 'utf8');
  log(`✅ 저장 완료 (${Buffer.byteLength(out, 'utf8').toLocaleString()} bytes)`);
}

if (require.main === module) main();

module.exports = { entryConditionGate, compareUnfilledInfo };
