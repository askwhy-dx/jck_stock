/**
 * write_summary.js — 단테 자동화 실행 요약을 logs/dante_summary_YYYY-MM-DD.log에 append
 *
 * 호출 시점: run_daily_dante.bat 5단계 종료 직후 (성공·실패 무관)
 *
 * 입력 (환경변수 또는 인자):
 *   --status=success|fail|skip       파이프라인 최종 상태
 *   --reason="..."                   skip/fail 사유 (선택)
 *   --start=YYYY-MM-DD HH:MM:SS      시작 시각
 *   --mail=sent|failed|skipped       메일 발송 결과
 *
 * 자동 추출:
 *   - 가장 최신 stock/data/dante_analysis_{TODAY}_{HHMM}.json → 4지 판정 분포 + 평가익
 *
 * 출력:
 *   - stock/logs/dante_summary_{YYYY-MM-DD}.log (append)
 *
 * 사용:
 *   node write_summary.js --status=success --start="2026-05-19 14:30:00" --mail=sent
 *   node write_summary.js --status=skip --reason="휴장일"
 */
const fs = require('fs');
const path = require('path');

const argv = process.argv.slice(2);
function getArg(name) {
  const direct = argv.find(a => a.startsWith(`--${name}=`));
  if (direct) return direct.slice(name.length + 3);
  const idx = argv.indexOf(`--${name}`);
  return idx >= 0 && idx + 1 < argv.length && !argv[idx + 1].startsWith('--') ? argv[idx + 1] : null;
}

const STATUS = getArg('status') || 'unknown';
const REASON = getArg('reason') || '';
const START = getArg('start') || '';
// 알림 채널: --notify (Slack) 우선, 하위 호환으로 --mail도 인식
const NOTIFY = getArg('notify') || getArg('mail') || 'unknown';

const ROOT = __dirname;
const DATA_DIR = path.join(ROOT, 'data');
const LOGS_DIR = path.join(ROOT, 'logs');

function kstNow() {
  // 표시용: 실제 시각에 9hr 가산된 "가짜 UTC ms" (toISOString이 KST 시각을 반환)
  return new Date(Date.now() + 9 * 60 * 60 * 1000);
}
function kstStr(d) {
  return d.toISOString().replace('T', ' ').slice(0, 19);
}
function diffSeconds(startStrKst, realEndDate) {
  if (!startStrKst) return null;
  // KST 'YYYY-MM-DD HH:MM:SS' 형식 → 실제 UTC ms
  const m = startStrKst.match(/^(\d{4})-(\d{2})-(\d{2})\s+(\d{2}):(\d{2}):(\d{2})$/);
  if (!m) return null;
  const [, y, mo, d, h, mi, s] = m;
  // KST 시각 → 실제 UTC ms (KST를 UTC로 해석한 ms에서 9시간 차감)
  const startUtcMs = Date.UTC(+y, +mo - 1, +d, +h, +mi, +s) - 9 * 3600 * 1000;
  return Math.round((realEndDate.getTime() - startUtcMs) / 1000);
}

const now = kstNow();          // 표시용 KST 시각
const realNow = new Date();    // 소요시간 계산용 실제 UTC ms
const today = now.toISOString().slice(0, 10);
const summaryPath = path.join(LOGS_DIR, `dante_summary_${today}.log`);

if (!fs.existsSync(LOGS_DIR)) fs.mkdirSync(LOGS_DIR, { recursive: true });

// 최신 분석 JSON 추출
let analysis = null;
let analysisFile = null;
try {
  if (fs.existsSync(DATA_DIR)) {
    const files = fs.readdirSync(DATA_DIR)
      .filter(f => new RegExp(`^dante_analysis_${today}_\\d{4}\\.json$`).test(f))
      .sort();
    if (files.length > 0) {
      analysisFile = files[files.length - 1];
      analysis = JSON.parse(fs.readFileSync(path.join(DATA_DIR, analysisFile), 'utf-8'));
    }
  }
} catch (e) {
  // analysis 파싱 실패는 요약에서만 표시
}

// 판정 분포 카운트 (활성 6지 — 2026-08-20 §4.4-D 신설로 💰·🛡️ 추가)
// ⚠️ 새 판정을 여기 빠뜨리면 에러 없이 other 로 흘러 분포가 조용히 틀어진다.
function countDecisions(stocks) {
  const c = { '🟢': 0, '💰': 0, '🛡️': 0, '🟡': 0, '⏸': 0, '🚫': 0, other: 0 };
  for (const s of (stocks || [])) {
    const d = s.decision || '';
    if (d.startsWith('🟢')) c['🟢']++;
    else if (d.startsWith('💰')) c['💰']++;
    else if (d.startsWith('🛡️')) c['🛡️']++;
    else if (d.startsWith('🟡')) c['🟡']++;
    else if (d.startsWith('⏸')) c['⏸']++;
    else if (d.startsWith('🚫')) c['🚫']++;
    else c.other++;
  }
  return c;
}

const stocks = analysis?.stocks || [];
const activeStocks = stocks.filter(s => s.category === 'active');
const activeCounts = countDecisions(activeStocks);

const elapsedSec = diffSeconds(START, realNow);
const elapsedStr = elapsedSec != null ? `${Math.floor(elapsedSec / 60)}분 ${elapsedSec % 60}초` : '?';

const statusIcon = STATUS === 'success' ? '✅'
  : STATUS === 'fail' ? '❌'
  : STATUS === 'skip' ? '⏭️'
  : '❓';

const notifyIcon = NOTIFY === 'sent' ? '📣✅'
  : NOTIFY === 'failed' ? '📣❌'
  : NOTIFY === 'skipped' ? '📣⏭️'
  : '📣❓';

// 요약 블록 구성
const lines = [
  '',
  '═══════════════════════════════════════════════════════',
  `${statusIcon} [${kstStr(now)}] 단테 자동화 실행 종료 — ${STATUS.toUpperCase()}`,
  '═══════════════════════════════════════════════════════',
  START ? `🕐 시작: ${START} → 종료: ${kstStr(now)} (소요 ${elapsedStr})` : `🕐 종료: ${kstStr(now)}`,
];

if (REASON) lines.push(`📝 사유: ${REASON}`);
lines.push(`${notifyIcon} 알림(Slack): ${NOTIFY}`);

if (analysis) {
  lines.push(`📊 분석 JSON: ${analysisFile}`);
  lines.push(`   활성 ${activeStocks.length}종 — 🟢${activeCounts['🟢']} / 🟡${activeCounts['🟡']} / ⏸${activeCounts['⏸']} / 🚫${activeCounts['🚫']}` +
             // §4.4-D 신설분은 발생 시에만 표기 (평소 0이라 상시 노출하면 줄만 길어짐)
             (activeCounts['💰'] ? ` / 💰${activeCounts['💰']}` : '') +
             (activeCounts['🛡️'] ? ` / 🛡️${activeCounts['🛡️']}` : '') +
             (activeCounts.other ? ` / 기타${activeCounts.other}` : ''));

  if (analysis.summary) {
    if (analysis.summary.active_total_eval_gain) lines.push(`⚡ 활성 평가익 합산: ${analysis.summary.active_total_eval_gain}`);
    if (analysis.summary.market_urgency) lines.push(`🚦 시장 충격도: ${analysis.summary.market_urgency}`);
  }

  // 종목별 4지 판정 한 줄 요약 (이모지+한글 전체 표시)
  const top = stocks.map(s => `${s.name}=${(s.decision || '?').replace(/\s*\(.*?\)\s*/, '').trim()}`).join(' / ');
  lines.push(`📋 ${top}`);

  // 위험 종목 (손절 검토 / critical)
  const critical = stocks.filter(s => s.risk_level === 'critical' || (s.decision || '').includes('손절'));
  if (critical.length > 0) {
    lines.push(`🚨 위험 종목: ${critical.map(s => s.name + '(' + s.current_price + ')').join(', ')}`);
  }
} else {
  lines.push('📊 분석 결과 없음 (캡처 단계 실패 또는 분석 미실행)');
}

lines.push('');

fs.appendFileSync(summaryPath, lines.join('\n'), 'utf-8');
console.log(`요약 로그 append: ${summaryPath}`);
