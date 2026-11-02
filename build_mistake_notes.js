/**
 * build_mistake_notes.js — 주간 오답노트 빌더 (2026-06-15 신설)
 *
 * "실제로 거래한 종결 종목"만 대상으로 오답노트를 만든다. (후보·관망 채점 노이즈 제거)
 *
 *   입력 : data/lessons.json   (build_lessons.js — 종결 거래 + 보유궤적 + +10일 청산평가)
 *          data/proposals.json (reflect_agent.js — 전략 보완 제안 + 빈도 수치)
 *   출력 : 오답노트.md            (주차별 누적 마크다운)
 *          분석_대시보드.html      (🏁 종료 탭의 마커 사이 카드 자동 교체)
 *
 * 3계층:
 *   [1] 거래별 오답노트 카드 (❌오답 / ⭕정답 / ⏱️+10일 청산평가 / 🔁재진입)
 *   [2] 반복 오답·정답 집계 (빈도 수치)
 *   [3] 전략 보완 제안 (발견 → 현재 전략 어디를 → 어떻게, 발생빈도 정렬)
 *
 * 사용: node build_mistake_notes.js
 */
const fs = require('fs');
const path = require('path');

const ROOT = __dirname;
const DATA = path.join(ROOT, 'data');
const HTML = path.join(ROOT, '분석_대시보드.html');
const MD = path.join(ROOT, '오답노트.md');
const MARK_START = '<!-- MISTAKE_NOTES_START -->';
const MARK_END = '<!-- MISTAKE_NOTES_END -->';

const load = (p) => { try { return JSON.parse(fs.readFileSync(p, 'utf-8')); } catch { return null; } };
const esc = (s) => String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
const clip = (s, n = 240) => { s = String(s || ''); return s.length > n ? s.slice(0, n - 1) + '…' : s; };
const sgn = (v) => v == null ? '—' : `${v >= 0 ? '+' : ''}${+(+v).toFixed(1)}%`;
const kstToday = () => new Date(Date.now() + 9 * 3600 * 1000).toISOString().slice(0, 10);
const weekday = (d) => ['일', '월', '화', '수', '목', '금', '토'][new Date(d + 'T00:00:00+09:00').getDay()];

// ── 거래 분류: 🛑 명백한 오답 / ⚠️ 부분 오답 / 📐 아쉬움 / 🏆 모범 ──
function classify(t) {
  const tj = t.trajectory || {};
  if (t.result === '손절') return { tag: '🛑', label: '명백한 오답 (손절)', color: '#f85149', isMistake: true };
  if (tj.capture_note) return { tag: '⚠️', label: '부분 오답 (저효율 익절)', color: '#d29922', isMistake: true };
  if ((t.realized_pct || 0) < 3) return { tag: '📐', label: '아쉬움 (목표 미달)', color: '#d29922', isMistake: true };
  return { tag: '🏆', label: '모범 (정답)', color: '#3fb950', isMistake: false };
}

const byMark = (lessons, marks) => (lessons || []).find(l => marks.some(m => l.startsWith(m)));

// 거래에서 ❌오답 / ⭕정답 / 🔁재진입 텍스트 추출
function extract(t, cls) {
  const L = t.lessons || [];
  const tj = t.trajectory || {};
  let wrong, right;
  if (!cls.isMistake) {
    // 🏆 모범 → "잘된 점" 강조
    right = byMark(L, ['🏆', '✅']) || L[0] || '';
    wrong = null;
  } else if (cls.tag === '⚠️') {
    wrong = tj.capture_note || byMark(L, ['⚠️', '📉']) || '';
    right = byMark(L, ['📐', '⚠️']) || byMark(L, ['✅']) || '';
  } else {
    wrong = byMark(L, ['🛑', '⚠️', '📐']) || '';
    right = (L.find(l => /(📐|⚠️)/.test(l) && l !== wrong)) || byMark(L, ['✅']) || '';
  }
  const reentry = byMark(L, ['🔁', '🔄']) || '';
  return { wrong, right, reentry };
}

function main() {
  const lessons = load(path.join(DATA, 'lessons.json'));
  if (!lessons || !Array.isArray(lessons.trades)) {
    console.log('⚠ lessons.json 없음 — 먼저 node build_lessons.js 실행 필요.');
    return;
  }
  const trades = lessons.trades;
  const closed = trades.length;
  const wins = trades.filter(t => t.result === '성공').length;
  const losses = trades.filter(t => t.result === '손절').length;
  const today = kstToday();

  // 활성 제안만 (거절 제외). 빈도 정렬: 발생빈도 desc → 점수 desc
  const props = ((load(path.join(DATA, 'proposals.json')) || {}).proposals || [])
    .filter(p => p.status !== 'rejected' && p.still_detected !== false)
    .sort((a, b) => ((b.metrics?.freq_pct || 0) - (a.metrics?.freq_pct || 0)) || (b.score - a.score));

  const cards = trades.map(t => ({ t, cls: classify(t), ex: extract(t, classify(t)) }));
  const mistakes = cards.filter(c => c.cls.isMistake);
  const models = cards.filter(c => !c.cls.isMistake);

  // ───────── 마크다운 ─────────
  const mdLines = [];
  mdLines.push(`## ${today} (${weekday(today)}) 주간 회고`);
  mdLines.push(`누적 **${closed}건 종결** · 🏆 성공 ${wins} / 🛑 손절 ${losses}\n`);

  mdLines.push(`### 🔧 전략 보완 제안 (발생빈도순)`);
  if (props.length) {
    mdLines.push(`| 순위 | 보완안 | 발생빈도 | 집중도 | 재발 | 영향 | 등급 |`);
    mdLines.push(`|---|---|---|---|---|---|---|`);
    props.forEach((p, i) => {
      const m = p.metrics || {};
      const grade = `${p.grade}${p.forced ? '(강제)' : ''}·${p.score}점`;
      const impact = m.impact_pct ? `${m.impact_kind} ${sgn(m.impact_kind?.includes('손실') ? -m.impact_pct : m.impact_pct)}` : (m.impact_kind || '—');
      mdLines.push(`| ${i + 1} | ${p.title} | ${m.freq_label || '—'} (${m.freq_pct || 0}%) | ${m.conc_label || '—'}${m.conc_pct != null ? ` (${m.conc_pct}%)` : ''} | ${p.weeks}주 | ${impact} | ${grade} |`);
    });
  } else mdLines.push(`_감지된 반복 보완안 없음._`);
  mdLines.push('');

  mdLines.push(`### 📋 거래별 오답노트`);
  for (const c of [...mistakes, ...models]) {
    const { t, cls, ex } = c;
    const tj = t.trajectory || {};
    mdLines.push(`#### ${cls.tag} ${t.name} (${t.code}) · ${t.type} · ${sgn(t.realized_pct)} — ${cls.label}`);
    mdLines.push(`- 거래: ${t.entry_date || '?'} ~ ${t.exit_date || '?'} · 평단 ${t.avg_price || '?'} → 매도 ${t.exit_price || '?'}`);
    if (ex.wrong) mdLines.push(`- ❌ 무엇을 틀렸나: ${clip(ex.wrong)}`);
    if (ex.right) mdLines.push(`- ⭕ ${cls.isMistake ? '정답은' : '잘된 점'}: ${clip(ex.right)}`);
    if (tj.exit_verdict_10d) mdLines.push(`- ⏱️ +10일 청산 평가: ${tj.exit_verdict_10d.tag} ${tj.exit_verdict_10d.label}`);
    if (ex.reentry) mdLines.push(`- 🔁 재진입: ${clip(ex.reentry, 160)}`);
    mdLines.push('');
  }
  const section = mdLines.join('\n').trim() + '\n';

  // 누적 병합: 같은 날짜 섹션 있으면 교체, 없으면 최신이 위로
  const HEADER = `# 📓 단테 오답노트\n\n_실제 종결 거래(매매일지 §6.9)만 대상으로 한 주간 오답노트. build_mistake_notes.js 자동 생성._\n`;
  let prevBody = '';
  if (fs.existsSync(MD)) {
    const raw = fs.readFileSync(MD, 'utf-8').replace(/^# 📓[\s\S]*?\n(?=## )/, '');
    prevBody = raw.split(/\n(?=## \d{4}-\d{2}-\d{2})/).filter(Boolean)
      .filter(s => !s.startsWith(`## ${today} `)).join('\n\n');
  }
  fs.writeFileSync(MD, `${HEADER}\n${section}${prevBody ? '\n' + prevBody.trim() + '\n' : ''}`, 'utf-8');

  // ───────── HTML 카드 ─────────
  const html = injectHtml(closed, wins, losses, today, props, [...mistakes, ...models]);

  console.log(`\n📓 오답노트 생성 — 종결 ${closed}건 (오답 ${mistakes.length} / 모범 ${models.length}) · 보완안 ${props.length}건`);
  if (props.length) {
    console.log(`   ── 전략 보완 제안 (빈도순)`);
    props.forEach((p, i) => console.log(`   ${i + 1}. [${p.grade}${p.forced ? '·강제' : ''} ${p.score}점] ${p.title} — 발생 ${p.metrics?.freq_label}(${p.metrics?.freq_pct}%)${p.metrics?.conc_label ? ` · 집중 ${p.metrics.conc_label}` : ''}`));
  }
  console.log(`💾 ${MD}`);
  console.log(html ? `💾 ${HTML} (종료 탭 카드 주입 완료)\n` : `⚠ HTML 마커 없음 — 분석_대시보드.html 종료 탭에 ${MARK_START} … ${MARK_END} 추가 필요\n`);
}

function injectHtml(closed, wins, losses, today, props, cards) {
  if (!fs.existsSync(HTML)) return false;
  let doc = fs.readFileSync(HTML, 'utf-8');
  const si = doc.indexOf(MARK_START), ei = doc.indexOf(MARK_END);
  if (si < 0 || ei < 0) return false;

  // [3] 전략 보완 제안 테이블
  let rows = '';
  props.forEach((p, i) => {
    const m = p.metrics || {};
    const dot = p.grade === '도입' ? '#3fb950' : p.grade === '검토' ? '#d29922' : '#8b949e';
    const impact = m.impact_pct ? `${esc(m.impact_kind)} ${sgn(m.impact_kind && m.impact_kind.includes('손실') ? -m.impact_pct : m.impact_pct)}` : esc(m.impact_kind || '—');
    rows += `<tr>
      <td style="text-align:center;color:#8b949e;">${i + 1}</td>
      <td><strong>${esc(p.title)}</strong><div style="font-size:0.82em;color:#8b949e;">→ ${esc(p.target)}</div></td>
      <td style="text-align:center;"><strong style="color:#58a6ff;">${esc(m.freq_label || '—')}</strong><br><span style="font-size:0.8em;color:#8b949e;">${m.freq_pct || 0}%</span></td>
      <td style="text-align:center;">${esc(m.conc_label || '—')}${m.conc_pct != null ? `<br><span style="font-size:0.8em;color:#8b949e;">${m.conc_pct}%</span>` : ''}</td>
      <td style="text-align:center;">${p.weeks}주${p.weeks >= 2 ? ' 🔁' : ''}</td>
      <td style="font-size:0.85em;">${impact}</td>
      <td style="text-align:center;"><span class="badge" style="background:${dot}22;color:${dot};border:1px solid ${dot};">${esc(p.grade)}${p.forced ? '(강제)' : ''}·${p.score}</span></td>
    </tr>`;
  });
  const propTable = props.length ? `
  <h4 style="color:#f0883e;margin-top:18px;">🔧 전략 보완 제안 <span style="font-size:0.75em;color:#8b949e;">(발생빈도순 · 승인 시 전략 반영)</span></h4>
  <table class="stock-table" style="font-size:0.9em;">
    <thead><tr><th>#</th><th>보완안 → 대상</th><th>발생빈도</th><th>집중도</th><th>재발</th><th>영향</th><th>등급</th></tr></thead>
    <tbody>${rows}</tbody>
  </table>` : `<p style="color:#8b949e;">감지된 반복 보완안 없음.</p>`;

  // [1] 거래별 카드는 HTML 미노출 (2026-06-15 사용자 확정: 오답노트.md에만 보존, HTML은 전략 보완 제안 표만)
  const block = `${MARK_START}
<div class="card" style="margin-bottom:16px;border-left:4px solid #bc8cff;background:#1c162b;">
  <div class="card-header"><div>
    <h3 style="margin:0;">📓 오답노트 <span style="color:#8b949e;font-size:0.7em;">(${today} 주간 회고 · 실제 종결 거래 대상 · 자동 생성)</span></h3>
    <span class="badge" style="background:#bc8cff22;color:#bc8cff;border:1px solid #bc8cff;">종결 ${closed}건 · 🏆${wins} / 🛑${losses}</span>
  </div></div>
  ${propTable}
  <div style="font-size:0.78em;color:#8b949e;margin-top:10px;">⚠️ 전략 보완 제안은 <em>제안</em>까지만 자동 — 실제 룰/파라미터 반영은 터미널 승인 후. 거래별 상세 오답노트는 <code>오답노트.md</code> 참조.</div>
</div>
${MARK_END}`;

  doc = doc.slice(0, si) + block + doc.slice(ei + MARK_END.length);
  fs.writeFileSync(HTML, doc, 'utf-8');
  return true;
}

main();
