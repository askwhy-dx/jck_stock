/**
 * [일회성] 투자자공매 모듈 시뮬레이션 결과를 Slack(단테 자동화 채널)으로 발송.
 * .env(SLACK_WEBHOOK_URL) 재사용. 검증용 수동 발송.
 */
const path = require('path');
const https = require('https');
require('dotenv').config({ path: path.join(__dirname, '..', '..', '.env') });  // tools/ → test/.env

const WEBHOOK_URL = process.env.SLACK_WEBHOOK_URL;
if (!WEBHOOK_URL) { console.error('!!! SLACK_WEBHOOK_URL 미설정 (.env)'); process.exit(1); }

const text = [
  '🧪 *투자자공매 모듈 시뮬레이션* (2026-06-19 · 수동 검증)',
  '_수급 캡처 15장(활성 5종) 분석 결과 — 다음 회차부터 자동 분석(supply_demand)에 반영됩니다._',
  '',
  '🔴 *비에이치아이* — 이탈/분배',
  '   • 외국인 지분율 16→12% 급감 · 공매도 6/16 *23.4%* 급증',
  '🟢 *미래에셋벤처투자* — 매집',
  '   • 외국인 순매수 가속(+18~59만주) · 공매도 1.56→0.73% 감소',
  '🟢 *한화오션* — 약매집/중립',
  '   • 외국인 순매수 우위(6/17 +175만 · 6/16 +48만)',
  '⚠️ *키움증권* — 공매도 과다',
  '   • 공매도 10~26% 지속(6/17 25.78%) → 후보 진입 주의',
  '⚪ *TIGER 반도체TOP10* — ETF(수급 의미 낮음)',
  '',
  '📡 *신규: SK텔레콤(017670) 스윙 추가* — 공매도 1~3% 양호 + 외국인 매집',
  '   • 1차a 86~88K 반등양봉 대기 / 손절 80K / 목표 100K·110K',
].join('\n');

const payload = JSON.stringify({
  username: '단테 투자자공매 시뮬',
  icon_emoji: ':mag:',
  text,
});

const url = new URL(WEBHOOK_URL);
const req = https.request({
  hostname: url.hostname, path: url.pathname + url.search, method: 'POST',
  headers: { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(payload) },
}, res => {
  let body = '';
  res.on('data', d => body += d);
  res.on('end', () => console.log(`[Slack] status=${res.statusCode} body=${body}`));
});
req.on('error', e => { console.error('[Slack] error', e.message); process.exit(1); });
req.write(payload);
req.end();
