/**
 * reflect_agent.js — 4단계: 회고 에이전트 (v1)
 *
 * 청산 거래(lessons.json)와 AI 판정 채점(scorecard.json)을 읽고,
 * 반복되는 약점 패턴을 찾아 "개선 제안"을 근거와 함께 산출한다.
 *
 *   ⚠️ AI는 제안만 한다. 자동 적용 금지 — proposals.json(status=pending_review) 작성 후
 *      사람이 터미널에서 검토·승인하면 그때 lessons.json/룰/파라미터에 반영.
 *
 *   입력 : data/lessons.json (청산 거래 + 궤적 + 교훈)  ← 지금 가능
 *          data/scorecard.json (AI 판정 적중률)         ← 채점 누적(~6/2) 후 활성
 *   출력 : data/proposals.json (개선 제안 목록)
 *
 * 사용: node reflect_agent.js
 */
const fs = require('fs');
const path = require('path');

const ROOT = __dirname;
const DATA = path.join(ROOT, 'data');
const OUT = path.join(DATA, 'proposals.json');
const load = (p) => { try { return JSON.parse(fs.readFileSync(p, 'utf-8')); } catch { return null; } };

function main() {
  const trades = (load(path.join(DATA, 'lessons.json'))?.trades) || [];
  const card = load(path.join(DATA, 'scorecard.json'));
  const closed = trades.length, scored = card?.n_scored || 0;
  const proposals = [];
  const num = (v) => { const n = parseFloat(v); return Number.isFinite(n) ? Math.abs(n) : 0; };
  const avg = (arr) => arr.length ? arr.reduce((s, x) => s + x, 0) / arr.length : 0;
  const allLoss = trades.filter(t => t.result === '손절');
  const allWin = trades.filter(t => t.result === '성공');
  // 빈도 수치: 발생빈도(전체 종결 대비) + 집중도(해당군 대비) — "어떤 보완안이 자주 발생하나" 정렬용
  const pctOf = (n, d) => d ? +(n / d * 100).toFixed(0) : 0;

  // D1 — 익절 효율 낮음 (성공인데 보유 MFE 대비 실현 격차 큼)
  const lowEff = trades.filter(t => t.trajectory?.capture_note);
  if (lowEff.length >= 2) proposals.push({
    id: 'eff-capture', kind: '룰/파라미터', title: '익절 효율 개선 — 추세 강세 종목 조기 청산 자제',
    evidence: lowEff.map(t => `${t.name}: 실현 +${t.realized_pct}% vs 보유 MFE +${t.trajectory.hold_mfe_pct}% (미실현 ${t.trajectory.left_on_table_pp}%p)`),
    detail: {
      problem: '성공한 거래인데도 벌 수 있던 폭의 일부만 먹고 나왔다 (익절을 너무 일찍).',
      cause: '목표가 도달 시 분할로 빨리 청산하는 룰 때문에 더 갈 추세 종목도 일찍 매도. DS단석은 +4%에 청산했으나 당일 +29% 폭등(최대 +31.5% 가능), LG전자는 +36%에 청산 후에도 +41.5% 더 상승(최대 +56.8% 가능).',
      action: '5중 이평선 정배열이 유지되는 "추세 강세" 종목은 목표가에서 전량 분할 청산하지 말고, 일부(예: 30~50%)를 트레일링 손절(예: 신고가 -7~8%)로 추세 끝까지 보유. 추세 이탈(이평선 종가 이탈) 시엔 기존대로 즉시 전량 청산.',
      effect: 'LG전자 같은 추세 종목에서 +36% 대신 더 높은 구간까지 익절 추적 → 평균 실현 수익 상향.',
      caveat: '표본 2건. 추세 약한 종목엔 적용 금지(기존 분할 익절 유지). 전 종목 일괄 적용 ❌, 정배열 강세에만.',
    },
    target: 'strategy_params.json(트레일링 버퍼) 또는 §단테 익절 룰',
    confidence: `낮음 (n=${lowEff.length} / 청산 ${closed}건)`, status: 'pending_review',
    // 점수 원시값: 영향=평균 미실현(기회비용) · 일관성 0.7(부분: 추세강도 종목별 차이)
    metrics: { impact_pct: avg(lowEff.map(t => num(t.trajectory.left_on_table_pp))), impact_kind: '기회비용(미실현)', n: lowEff.length, consistency: 0.7,
      freq_pct: pctOf(lowEff.length, closed), freq_label: `${lowEff.length}/${closed} 종결`,
      conc_pct: pctOf(lowEff.length, allWin.length), conc_label: `성공 ${lowEff.length}/${allWin.length}` },
  });

  // D2 — 1차 단독 체결 후 손절 (분할 미발효)
  const soloLoss = trades.filter(t => t.result === '손절' && (t.lessons || []).some(l => /단독|단일\s*(진입|체결)/.test(l)));
  if (soloLoss.length >= 2) proposals.push({
    id: 'solo-entry-loss', kind: '룰', title: '1차 단독 체결 리스크 — 분할 안착 룰 강화',
    evidence: soloLoss.map(t => `${t.name}: 실현 ${t.realized_pct}% (1차 단독 체결 후 1차b·2차 미발효 → 손절)`),
    detail: {
      problem: '손절 2건이 똑같은 방식으로 깨졌다.',
      cause: '단테 분할매수(1:1:2 등)는 1차→1차b→2차로 나눠 들어가며 평단을 낮추는 전략인데, 이 2건은 1차만 체결되고 1차b·2차가 안 들어간 채 하락 → 평단 낮출 기회 없이 1차 진입가 기준 손실이 그대로 노출. 클래시스 -10.6%, 성우 -12.9%.',
      action: '1차 체결 후 1차b가 일정 기간(예: 3거래일) 내 미체결이면 → (a) 포지션 비중을 절반으로 축소하거나 (b) 추가 진입 보류 + 손절선을 1차 진입가 기준으로 타이트하게. "1차 단독 보유" 상태를 오래 두지 않는다.',
      effect: '1차 단독(손익비 0.85·불리) 노출 시간을 줄여 -10%대 손실을 -5% 이내로 제한.',
      caveat: '손절 표본 2건이지만 둘 다 동일 원인(손절 전수) → 패턴 선명. 3건 중 신뢰도 가장 높음.',
    },
    target: '§단테 분할매수 룰 / feedback_risk_reward_rule',
    confidence: `중 (n=${soloLoss.length} / 손절 전수)`, status: 'pending_review',
    // 점수 원시값: 영향=평균 실현 손실 · 일관성 1.0 · solo_sweep=손절 100%가 동일 원인이면 강제 도입 트리거(①)
    metrics: { impact_pct: avg(soloLoss.map(t => num(t.realized_pct))), impact_kind: '실현 손실', n: soloLoss.length, consistency: 1.0, solo_sweep: soloLoss.length === allLoss.length && allLoss.length >= 2,
      freq_pct: pctOf(soloLoss.length, closed), freq_label: `${soloLoss.length}/${closed} 종결`,
      conc_pct: pctOf(soloLoss.length, allLoss.length), conc_label: `손절 ${soloLoss.length}/${allLoss.length}` },
  });

  // D3 — 유동성(거래대금) 반복 약점
  const liq = trades.filter(t => (t.lessons || []).some(l => /거래대금\s*100위/.test(l)));
  if (liq.length >= 2) proposals.push({
    id: 'liquidity-filter', kind: '필터', title: '유동성 필터 — 후보 선정 시 거래대금 상위 우선',
    evidence: liq.map(t => `${t.name}: "거래대금 100위 밖 → 익절/추가매수 어려움" 교훈`),
    detail: {
      problem: '거래대금 적은 종목이 익절·추가매수에서 발목을 잡았다.',
      cause: 'DS단석·지앤씨에너지 모두 "거래대금 100위 밖" → 매수세 약해 목표가 도달이 어렵고(지앤씨는 +1.2%로 겨우 익절), 분할 추가매수 시 체결·슬리피지 불리.',
      action: '신규 후보 선정 단계(collect_theme_flow.py 정량 필터)에 "거래대금 50위 이내" 또는 "일평균 거래대금 일정액 이상" 조건 추가.',
      effect: '진입 후 안 오르거나 못 빠져나오는 저유동성 종목을 사전 차단.',
      caveat: '필터가 너무 빡세면 좋은 소형주 후보를 놓칠 수 있음. "50위"는 예시값 — 운영하며 조정.',
    },
    target: 'collect_theme_flow.py 후보 필터 / §후보 선정 룰',
    confidence: `낮음 (n=${liq.length})`, status: 'pending_review',
    // 점수 원시값: 영향=정성적(기회 손실, 수치화 불가) → 0 처리 · 일관성 0.6
    metrics: { impact_pct: 0, impact_kind: '정성(유동성)', n: liq.length, consistency: 0.6,
      freq_pct: pctOf(liq.length, closed), freq_label: `${liq.length}/${closed} 종결`,
      conc_pct: null, conc_label: null },
  });

  // D4 — (폐기 2026-06-15) AI 판정 적중률 채점 기반 제안 제거.
  //   사유: 매일 판정 채점이 미진입 '후보 풀 유지/관망'을 진입가 -3% 방어로 채점 →
  //   안 산 종목까지 오답 처리(후보 0% 왜곡). 주간 회고는 '실제 종결 거래(lessons.json)'만
  //   대상으로 하는 오답노트 체계로 전환. 종결종목 청산 평가는 trajectory.exit_verdict_10d 사용.

  // ── 이력 병합 (id 기준 누적 · 신규/기존/반영/거절/해소 구분) ──
  const now = new Date().toISOString();
  const today = now.slice(0, 10);
  const prev = load(OUT);
  const prevById = {};
  for (const p of (prev?.proposals || [])) prevById[p.id] = p;
  const detectedIds = new Set(proposals.map(p => p.id));

  const merged = [];
  for (const d of proposals) {
    const p = prevById[d.id];
    if (p && (p.status === 'approved' || p.status === 'rejected')) {
      // 사람이 내린 결정은 유지 (다시 잔소리 안 함), 근거만 최신화
      merged.push({ ...d, status: p.status, first_seen: p.first_seen || today, last_seen: today, decided_at: p.decided_at, still_detected: true });
    } else {
      merged.push({ ...d, status: 'pending', first_seen: p?.first_seen || today, last_seen: today, is_new: !p, still_detected: true });
    }
  }
  // 이번에 감지 안 된 과거 제안 → 패턴 사라짐
  for (const p of (prev?.proposals || [])) {
    if (detectedIds.has(p.id)) continue;
    merged.push({ ...p, still_detected: false, status: p.status === 'pending' ? 'resolved' : p.status, resolved_at: p.resolved_at || today });
  }

  // ── P-Score 산정 + 등급/강제 트리거 ──
  // 4축(영향40·근거30·재발15·검증15). 점수<70이라도 하드 트리거 발동 시 '도입(강제)'로 승격.
  const weeksBetween = (a, b) => Math.max(1, Math.floor((new Date(b) - new Date(a)) / (7 * 864e5)) + 1);
  const scoreOf = (p) => {
    const m = p.metrics || {};
    const impact = Math.min(40, (m.impact_pct || 0) * 1.5);          // 영향도 0~40
    const evidence = Math.min(15, (m.n || 0) * 5) + (m.consistency || 0) * 15; // 근거 0~30
    const weeks = weeksBetween(p.first_seen || today, p.last_seen || today);
    const recur = Math.min(15, 3 + (weeks - 1) * 4);                  // 재발성 0~15
    const valid = m.validated ? 15 : 0;                              // 검증도 0~15
    const score = Math.round(impact + evidence + recur + valid);
    const reasons = [];
    if (m.solo_sweep) reasons.push('손절 전수');                      // 트리거 ①
    if (m.validated) reasons.push(`AI 적중률 검증(n=${m.n})`);        // 트리거 ③
    let grade = score >= 70 ? '도입' : score >= 40 ? '검토' : '고려';
    let forced = false;
    if (reasons.length && grade !== '도입') { grade = '도입'; forced = true; }
    return { score, grade, forced, forced_reason: reasons.join(' · ') || null, weeks };
  };
  for (const p of merged) Object.assign(p, scoreOf(p));
  // 우선순위 정렬: 등급(도입>검토>고려) → 점수 내림차순
  const rank = { '도입': 0, '검토': 1, '고려': 2 };
  merged.sort((a, b) => (rank[a.grade] - rank[b.grade]) || (b.score - a.score));

  const pending = merged.filter(x => x.status === 'pending');
  const newOnes = pending.filter(x => x.is_new);
  const carry = pending.filter(x => !x.is_new);
  const approved = merged.filter(x => x.status === 'approved');
  const rejected = merged.filter(x => x.status === 'rejected');
  const resolved = merged.filter(x => x.status === 'resolved');

  const out = {
    generated_at: now,
    sources: { closed_trades: closed, scored_predictions: scored },
    counts: { pending: pending.length, new: newOnes.length, approved: approved.length, rejected: rejected.length, resolved: resolved.length },
    note: `청산거래 회고는 지금 가능(표본 ${closed}건). AI 적중률 회고는 채점 누적(~6/2) 후. 자동 적용 금지. 승인/거절: 터미널에서 'id 승인/거절' 요청 → 해당 status 기록(다음 실행부터 유지). 거절건은 다시 안 띄움.`,
    proposals: merged,
  };
  fs.writeFileSync(OUT, JSON.stringify(out, null, 2), 'utf-8');

  const badge = (p) => {
    const dot = p.grade === '도입' ? '🟢' : p.grade === '검토' ? '🟠' : '🔴';
    const label = p.grade === '도입' && p.forced ? '도입(강제)' : p.grade;
    return `${dot} ${label} · ${p.score}점${p.forced_reason ? ` · 발동: ${p.forced_reason}` : ''}`;
  };
  const printFull = (p, tag) => {
    console.log(`${tag} [${badge(p)}] ${p.title}   〈${p.kind} · 신뢰도 ${p.confidence}〉  [id:${p.id}]`);
    console.log(`     📊 근거: ${p.evidence.join(' / ')}`);
    if (p.detail) {
      console.log(`     ❓ 무엇이 문제: ${p.detail.problem}`);
      console.log(`     🔍 왜 그런가: ${p.detail.cause}`);
      console.log(`     🔧 어떻게 바꾸나: ${p.detail.action}`);
      console.log(`     ✅ 기대효과: ${p.detail.effect}`);
      console.log(`     ⚠️ 주의: ${p.detail.caveat}`);
    } else if (p.suggestion) {
      console.log(`     → 제안: ${p.suggestion}`);
    }
    console.log(`     → 대상: ${p.target}\n`);
  };

  console.log(`\n🧠 회고 에이전트 — 입력: 청산 ${closed}건 / AI 채점 ${scored}건`);
  console.log(`📋 미조치 ${pending.length} (🆕신규 ${newOnes.length} / ⏳기존 ${carry.length}) · ✅반영 ${approved.length} · 🚫거절 ${rejected.length} · ☑️해소 ${resolved.length}\n`);

  if (newOnes.length) { console.log(`── 🆕 신규 제안`); newOnes.forEach(p => printFull(p, '🆕')); }
  if (carry.length) { console.log(`── ⏳ 기존 미조치`); carry.forEach(p => printFull(p, '⏳')); }
  if (approved.length) console.log(`── ✅ 반영됨: ${approved.map(p => p.id + (p.still_detected ? '' : '(패턴 해소)')).join(', ')}`);
  if (rejected.length) console.log(`── 🚫 거절(숨김): ${rejected.map(p => p.id).join(', ')}`);
  if (resolved.length) console.log(`── ☑️ 해소(패턴 사라짐·미조치였음): ${resolved.map(p => p.id).join(', ')}`);
  if (scored < 5) console.log(`\nℹ AI 적중률 기반 제안은 채점 ${scored}/5건 — ~6/2 이후 활성화.`);
  console.log(`\n💾 ${OUT}\n`);
}
main();
