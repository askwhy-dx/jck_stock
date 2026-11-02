/**
 * slack_send_proposals.js — 주간 회고 결과(proposals.json)를 Slack Webhook으로 발송
 *
 * 루프 최종 산출물(회고 제안)만 Slack으로 전달. 판정/채점은 보내지 않는다.
 * Slack은 발신 전용 — 승인/거절은 터미널 게이트 유지 (feedback: Slack 발신 전용 룰).
 *
 * 입력:
 *   - stock/data/proposals.json (reflect_agent.js 산출)
 *
 * 의존 (.env, slack_send.js와 동일):
 *   SLACK_WEBHOOK_URL    Incoming Webhook URL
 *   SLACK_USERNAME       발신자명 (선택, 기본: "단테 주간 회고")
 *   SLACK_ICON_EMOJI     아이콘 이모지 (선택, 기본: :mag:)
 *
 * 사용:
 *   node slack_send_proposals.js            # pending 제안 발송
 *   node slack_send_proposals.js --dry-run  # 페이로드만 콘솔 출력
 */
const fs = require('fs');
const path = require('path');
const https = require('https');
require('dotenv').config({ path: path.join(__dirname, '..', '.env') });

const argv = process.argv.slice(2);
const DRY_RUN = argv.includes('--dry-run');

const ROOT = __dirname;
const DATA_DIR = path.join(ROOT, 'data');
const PROPOSALS_PATH = path.join(DATA_DIR, 'proposals.json');

const WEBHOOK_URL = process.env.SLACK_WEBHOOK_URL;
const USERNAME = process.env.SLACK_USERNAME_REFLECT || '단테 주간 회고';
const ICON_EMOJI = process.env.SLACK_ICON_REFLECT || ':mag:';

function log(msg) {
  const ts = new Date(Date.now() + 9 * 60 * 60 * 1000).toISOString().replace('T', ' ').slice(0, 19);
  console.log(`[${ts}] ${msg}`);
}

// generated_at(ISO UTC) → KST 'YYYY-MM-DD HH:mm'
function kstStamp(iso) {
  if (!iso) return '-';
  try {
    return new Date(new Date(iso).getTime() + 9 * 60 * 60 * 1000)
      .toISOString().replace('T', ' ').slice(0, 16);
  } catch { return iso; }
}

function buildBlocks(data) {
  const all = Array.isArray(data.proposals) ? data.proposals : [];
  // 승인 대기 제안만 발송: approved/rejected/resolved 제외
  const pending = all.filter(p => !['approved', 'rejected', 'resolved'].includes(p.status));
  const src = data.sources || {};
  const stamp = kstStamp(data.generated_at);

  const blocks = [
    { type: 'header', text: { type: 'plain_text', text: `🪞 단테 주간 회고 — 승인 대기 ${pending.length}건` } },
    {
      type: 'context',
      elements: [{
        type: 'mrkdwn',
        text: `생성 ${stamp} KST · 표본: 청산 ${src.closed_trades ?? 0}건 / 채점 ${src.scored_predictions ?? 0}건 · 자동 적용 ❌ (사람 승인 후 반영)`,
      }],
    },
  ];

  if (pending.length === 0) {
    blocks.push({
      type: 'section',
      text: { type: 'mrkdwn', text: '이번 주 신규 회고 제안 없음. (표본 부족 또는 모두 처리됨)' },
    });
    return { blocks, pending };
  }

  pending.forEach((p, i) => {
    const d = p.detail || {};
    const m = p.metrics || {};
    const evidence = Array.isArray(p.evidence) ? p.evidence.map(e => `• ${e}`).join('\n') : '';
    const newBadge = p.is_new ? ' 🆕' : '';
    // 우선순위 배지: 점수<70이라도 하드 트리거 발동 시 '도입(강제)'
    const dot = p.grade === '도입' ? '🟢' : p.grade === '검토' ? '🟠' : '🔴';
    const gradeLabel = p.grade === '도입' && p.forced ? '도입(강제)' : (p.grade || '미평가');
    const impact = m.impact_pct ? ` · 영향 ${m.impact_kind || ''} ${m.impact_pct.toFixed(1)}%` : '';
    const forced = p.forced_reason ? ` · 발동: ${p.forced_reason}` : '';
    blocks.push({ type: 'divider' });
    blocks.push({
      type: 'section',
      text: {
        type: 'mrkdwn',
        text: `${dot} *${gradeLabel} · ${p.score ?? '-'}점* — *#${i + 1} ${p.title}*${newBadge}\n\`${p.kind || '-'}\` · 신뢰도 ${p.confidence || '-'}${impact}${forced} · id \`${p.id}\``,
      },
    });
    if (evidence) {
      blocks.push({ type: 'section', text: { type: 'mrkdwn', text: `🔍 *근거*\n${evidence}` } });
    }
    if (d.action) {
      blocks.push({ type: 'section', text: { type: 'mrkdwn', text: `🛠 *제안 액션*\n${d.action}` } });
    }
    const tail = [];
    if (p.target) tail.push(`🎯 대상: ${p.target}`);
    if (d.caveat) tail.push(`⚠️ 주의: ${d.caveat}`);
    if (tail.length) {
      blocks.push({ type: 'context', elements: [{ type: 'mrkdwn', text: tail.join('\n') }] });
    }
  });

  // 승인 안내 (터미널 게이트)
  blocks.push({ type: 'divider' });
  blocks.push({
    type: 'section',
    text: {
      type: 'mrkdwn',
      text: `✅ *승인/거절은 터미널에서* — 예: \`${pending[0].id} 승인\` 또는 \`${pending[0].id} 거절\`\n거절건은 다음 회차부터 안 띄움 · 승인건만 룰/교재에 반영`,
    },
  });
  blocks.push({
    type: 'context',
    elements: [{ type: 'mrkdwn', text: 'AI는 제안만 — 결정은 사람. 모든 반영은 기록(롤백 가능). 학습용 참고 자료.' }],
  });

  return { blocks, pending };
}

function buildFallback(data, pending) {
  return [
    `🪞 단테 주간 회고 — 승인 대기 ${pending.length}건`,
    ...pending.map((p, i) => {
      const label = p.grade === '도입' && p.forced ? '도입(강제)' : (p.grade || '-');
      return `#${i + 1} [${label} ${p.score ?? '-'}점] ${p.title} [${p.id}]`;
    }),
    '승인/거절은 터미널에서.',
  ].join('\n');
}

function postToSlack(payload) {
  return new Promise((resolve, reject) => {
    const url = new URL(WEBHOOK_URL);
    const body = JSON.stringify(payload);
    const req = https.request({
      hostname: url.hostname,
      path: url.pathname,
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Content-Length': Buffer.byteLength(body),
      },
    }, res => {
      let resp = '';
      res.on('data', c => resp += c);
      res.on('end', () => {
        if (res.statusCode === 200 && resp === 'ok') resolve({ ok: true });
        else reject(new Error(`Slack ${res.statusCode}: ${resp}`));
      });
    });
    req.on('error', reject);
    req.write(body);
    req.end();
  });
}

async function main() {
  if (!fs.existsSync(PROPOSALS_PATH)) {
    log(`proposals.json 없음 (${PROPOSALS_PATH}) — reflect_agent.js 선실행 필요. 발송 생략.`);
    return;
  }
  const data = JSON.parse(fs.readFileSync(PROPOSALS_PATH, 'utf-8'));
  const { blocks, pending } = buildBlocks(data);
  const fallback = buildFallback(data, pending);

  const payload = { username: USERNAME, icon_emoji: ICON_EMOJI, text: fallback, blocks };

  if (DRY_RUN) {
    log(`--dry-run: Slack 발송 생략. 페이로드 미리보기 (승인 대기 ${pending.length}건)`);
    console.log('───────────────────────────');
    console.log(JSON.stringify(payload, null, 2).slice(0, 2500));
    console.log('───────────────────────────');
    return;
  }

  if (!WEBHOOK_URL) {
    log(`!!! SLACK_WEBHOOK_URL 미설정 (.env). 발송 불가.`);
    process.exit(1);
  }

  log(`Slack 발송: ${USERNAME} → Webhook (승인 대기 ${pending.length}건)`);
  await postToSlack(payload);
  log(`✅ 회고 발송 완료`);
}

main().catch(e => {
  console.error('치명적 오류:', e.message);
  process.exit(1);
});
