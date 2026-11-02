/**
 * quality_rescan_reminder.js — 우량주 전체 재스캔 1회성 알림 (Slack Webhook)
 *
 * 분기보고서 마감 후 권장 재스캔일에 Task Scheduler가 1회 실행 → Slack 알림.
 * 단테 자동 알림과 동일하게 SLACK_WEBHOOK_URL(.env) 사용.
 *
 *   node quality_rescan_reminder.js            # 발송
 *   node quality_rescan_reminder.js --dry-run  # 콘솔만
 */
const path = require('path');
const https = require('https');
require('dotenv').config({ path: path.join(__dirname, '..', '..', '.env') });  // tools/ → test/.env

const DRY = process.argv.includes('--dry-run');
const WEBHOOK_URL = process.env.SLACK_WEBHOOK_URL;
const DASHBOARD_URL = process.env.DASHBOARD_URL || 'https://jichang999-dotcom.github.io/tes2/financial/분석_대시보드.html';

const text = [
  '🔔 *우량주 전체 재스캔 알림* (반기보고서 반영일)',
  '',
  '오늘은 *반기보고서 마감(8/14) 후 첫 권장 재스캔일*입니다.',
  '실행: `cd stock && python screen_quality_value.py --scan --grade A --quarterly --date <최신영업일>`',
  '',
  '⚠️ `--quarterly` = 최신 반기보고서 순익(TTM)·부채/자본/자산 스냅샷 반영 (매출·자산 3개년 추세는 연간 유지).',
  '• 결과: `data/quality_screen/scan_<날짜>.json` · A등급 자동 슬랙 카드 `📋 우량주 심사` 줄 반영',
  '',
  '※ 다음 재스캔: 3분기보고서 후 11월 셋째 주.',
].join('\n');

function post(payload) {
  return new Promise((resolve, reject) => {
    const url = new URL(WEBHOOK_URL);
    const body = JSON.stringify(payload);
    const req = https.request({
      hostname: url.hostname, path: url.pathname, method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(body) },
    }, res => {
      let d = ''; res.on('data', c => d += c);
      res.on('end', () => (res.statusCode === 200 ? resolve() : reject(new Error(`Slack ${res.statusCode}: ${d}`))));
    });
    req.on('error', reject); req.write(body); req.end();
  });
}

(async () => {
  if (DRY) { console.log(text); return; }
  if (!WEBHOOK_URL) { console.error('SLACK_WEBHOOK_URL 미설정'); process.exit(1); }
  await post({ username: '단테 우량주 재스캔 알림', icon_emoji: ':calendar:', text });
  console.log('✅ 재스캔 알림 발송');
})().catch(e => { console.error('발송 실패:', e.message); process.exit(1); });
