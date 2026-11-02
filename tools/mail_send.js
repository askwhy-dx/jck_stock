/**
 * mail_send.js — 분석 완료 후 jichang8590@gmail.com에 메일 발송
 *
 * 입력:
 *   - stock/data/dante_analysis_{YYYY-MM-DD}_{HHMM}.json (claude_dante_analyze.js 산출)
 *   - 또는 최신 dante_analysis_* 파일 자동 선택
 *
 * 의존 (.env):
 *   GMAIL_USER             발송자 Gmail 주소 (예: yourname@gmail.com)
 *   GMAIL_APP_PASSWORD     Google 계정 앱 비밀번호 (16자)
 *                          https://myaccount.google.com/apppasswords 에서 생성
 *   MAIL_TO                수신자 주소 (기본: jichang8590@gmail.com)
 *   DASHBOARD_URL          분석 대시보드 URL (기본: GitHub Pages)
 *
 * 사용:
 *   node mail_send.js                               # 가장 최신 분석 결과로 발송
 *   node mail_send.js --json dante_analysis_*.json  # 특정 분석 결과
 *   node mail_send.js --dry-run                     # 본문만 출력
 *
 * 사전 설치: npm install nodemailer (또는 npm i nodemailer --prefix stock)
 */
const fs = require('fs');
const path = require('path');
require('dotenv').config({ path: path.join(__dirname, '..', '..', '.env') });  // tools/ → test/.env

let nodemailer;
try {
  nodemailer = require('nodemailer');
} catch (e) {
  // 패키지 미설치 시 dry-run만 허용
  nodemailer = null;
}

const argv = process.argv.slice(2);
const DRY_RUN = argv.includes('--dry-run');
const JSON_ARG = (argv.find(a => a.startsWith('--json=')) || '').slice('--json='.length);

const ROOT = path.join(__dirname, '..');  // stock/
const DATA_DIR = path.join(ROOT, 'data');

const GMAIL_USER = process.env.GMAIL_USER;
const GMAIL_APP_PASSWORD = process.env.GMAIL_APP_PASSWORD;
const MAIL_TO = process.env.MAIL_TO || 'jichang8590@gmail.com';
const DASHBOARD_URL = process.env.DASHBOARD_URL || 'https://jichang999-dotcom.github.io/tes2/financial/분석_대시보드.html';

function log(msg) {
  const ts = new Date(Date.now() + 9 * 60 * 60 * 1000).toISOString().replace('T', ' ').slice(0, 19);
  console.log(`[${ts}] ${msg}`);
}

function findLatestAnalysisJson() {
  if (JSON_ARG) return path.join(DATA_DIR, JSON_ARG);
  const files = fs.readdirSync(DATA_DIR)
    .filter(f => /^dante_analysis_\d{4}-\d{2}-\d{2}_\d{4}\.json$/.test(f))
    .sort();
  if (files.length === 0) throw new Error('dante_analysis_*.json 파일 없음 — claude_dante_analyze.js 선실행 필요');
  return path.join(DATA_DIR, files[files.length - 1]);
}

function buildEmailBody(analysis) {
  const ts = analysis.analyzed_at || new Date().toISOString();
  const tsKst = ts.replace('T', ' ').slice(0, 16);
  const stocks = analysis.stocks || [];
  const active = stocks.filter(s => s.category === 'active');
  const candidate = stocks.filter(s => s.category === 'candidate');
  const summary = analysis.summary || {};

  const decisionColor = d => {
    if (!d) return '#8b949e';
    if (d.startsWith('🟢')) return '#3fb950';
    if (d.startsWith('💰')) return '#2ea043';   // §4.4-D D1 절반 익절 (2026-08-20 활성 6지)
    if (d.startsWith('🛡️')) return '#58a6ff';   // §4.4-D D2 트레일링 전환
    if (d.startsWith('🟡')) return '#f0883e';
    if (d.startsWith('⏸')) return '#d29922';
    if (d.startsWith('🚫')) return '#f85149';
    return '#8b949e';
  };

  const renderRow = s => `
    <tr>
      <td style="padding:6px;border:1px solid #ddd;"><strong>${s.name}</strong> (${s.code})</td>
      <td style="padding:6px;border:1px solid #ddd;">${s.type || '-'}</td>
      <td style="padding:6px;border:1px solid #ddd;color:${decisionColor(s.decision)};"><strong>${s.decision || '-'}</strong></td>
      <td style="padding:6px;border:1px solid #ddd;">${s.current_price || '-'} (${s.change_pct || '-'}%)</td>
      <td style="padding:6px;border:1px solid #ddd;">${s.eval_gain || '-'}</td>
      <td style="padding:6px;border:1px solid #ddd;font-size:0.9em;">${s.diagnosis || '-'}</td>
    </tr>`;

  const html = `<!DOCTYPE html>
<html><head><meta charset="UTF-8"></head>
<body style="font-family:'Apple SD Gothic Neo',Segoe UI,sans-serif;max-width:920px;margin:0 auto;padding:20px;background:#f6f8fa;">
  <div style="background:#fff;border:1px solid #e1e4e8;border-radius:8px;padding:24px;">
    <h2 style="margin-top:0;color:#1a1d21;">📊 단테 자동 분석 리포트 — ${analysis.date || tsKst.slice(0,10)} ${analysis.time ? analysis.time.slice(0,2)+':'+analysis.time.slice(2) : ''}</h2>
    <p style="color:#586069;font-size:14px;">
      🤖 <strong>AI 자동 판정 모드 (Claude ${analysis.model || ''})</strong> — 4지 판정은 차트 자동 분석 결과이며,
      사용자 직접 검토 후 최종 확정이 필요합니다 ([[feedback_buy_hold_decision_workflow]] §4.6.13).
    </p>

    <div style="background:#fff8e1;border-left:4px solid #f0883e;padding:12px 16px;margin:16px 0;border-radius:4px;">
      <strong>🚦 시장 충격도</strong>: ${summary.market_urgency || '-'}<br>
      <strong>⚡ 활성 평가익 합산</strong>: ${summary.active_total_eval_gain || '-'}<br>
      <strong>🟡 후보 현황</strong>: ${summary.candidate_status || '-'}
    </div>

    <h3 style="color:#3fb950;border-bottom:2px solid #3fb950;padding-bottom:4px;">✅ 활성 ${active.length}종</h3>
    <table style="width:100%;border-collapse:collapse;font-size:13px;">
      <thead style="background:#f0f0f0;">
        <tr>
          <th style="padding:8px;border:1px solid #ddd;text-align:left;">종목</th>
          <th style="padding:8px;border:1px solid #ddd;">분류</th>
          <th style="padding:8px;border:1px solid #ddd;">4지 판정 🤖</th>
          <th style="padding:8px;border:1px solid #ddd;">현재가/등락</th>
          <th style="padding:8px;border:1px solid #ddd;">평가익</th>
          <th style="padding:8px;border:1px solid #ddd;text-align:left;">핵심 진단</th>
        </tr>
      </thead>
      <tbody>${active.map(renderRow).join('')}</tbody>
    </table>

    <h3 style="color:#f0883e;border-bottom:2px solid #f0883e;padding-bottom:4px;margin-top:24px;">🟡 후보 ${candidate.length}종</h3>
    <table style="width:100%;border-collapse:collapse;font-size:13px;">
      <thead style="background:#f0f0f0;">
        <tr>
          <th style="padding:8px;border:1px solid #ddd;text-align:left;">종목</th>
          <th style="padding:8px;border:1px solid #ddd;">분류</th>
          <th style="padding:8px;border:1px solid #ddd;">4지 판정 🤖</th>
          <th style="padding:8px;border:1px solid #ddd;">현재가/등락</th>
          <th style="padding:8px;border:1px solid #ddd;">1차 진입 대비</th>
          <th style="padding:8px;border:1px solid #ddd;text-align:left;">핵심 진단</th>
        </tr>
      </thead>
      <tbody>${candidate.map(renderRow).join('')}</tbody>
    </table>

    <h3 style="margin-top:24px;">🔗 분석 대시보드</h3>
    <p>
      <a href="${DASHBOARD_URL}" style="display:inline-block;background:#6366f1;color:#fff;padding:10px 18px;text-decoration:none;border-radius:6px;font-weight:600;">
        분석 대시보드 열기 →
      </a>
    </p>
    <p style="color:#586069;font-size:12px;">${DASHBOARD_URL}</p>

    <hr style="margin:24px 0;border:none;border-top:1px solid #e1e4e8;">
    <p style="color:#8b949e;font-size:11px;">
      Generated by Claude Code 자동화 (toss_capture → claude_dante_analyze → upload_dashboard → mail_send) ·
      분석 시각: ${tsKst} KST · 모델: ${analysis.model || '-'}<br>
      <strong>⚠️ 면책</strong>: 본 메일은 학습용 참고 자료입니다. 4지 판정은 자동화된 분석 결과이며 투자 권유가 아닙니다.
      최종 매매 결정은 사용자가 차트를 직접 검토한 후 진행하세요.
    </p>
  </div>
</body></html>`;

  const text = `📊 단테 자동 분석 리포트 — ${analysis.date} ${analysis.time}

🚦 시장 충격도: ${summary.market_urgency || '-'}
⚡ 활성 평가익 합산: ${summary.active_total_eval_gain || '-'}
🟡 후보 현황: ${summary.candidate_status || '-'}

✅ 활성 ${active.length}종:
${active.map(s => `  ${s.name}(${s.code}) ${s.decision} | ${s.current_price}원 (${s.change_pct}%) | ${s.eval_gain} | ${s.diagnosis}`).join('\n')}

🟡 후보 ${candidate.length}종:
${candidate.map(s => `  ${s.name}(${s.code}) ${s.decision} | ${s.current_price}원 (${s.change_pct}%) | ${s.diagnosis}`).join('\n')}

🔗 대시보드: ${DASHBOARD_URL}

🤖 AI 자동 판정 모드 — 사용자 직접 검토 후 최종 확정 필요.`;

  return { html, text };
}

async function main() {
  const jsonPath = findLatestAnalysisJson();
  log(`분석 JSON 로드: ${jsonPath}`);
  const analysis = JSON.parse(fs.readFileSync(jsonPath, 'utf-8'));

  const { html, text } = buildEmailBody(analysis);
  const subject = `📊 단테 자동 분석 ${analysis.date} ${analysis.time?.slice(0,2)}:${analysis.time?.slice(2)} — 활성 ${(analysis.stocks||[]).filter(s=>s.category==='active').length}종 + 후보 ${(analysis.stocks||[]).filter(s=>s.category==='candidate').length}종`;

  if (DRY_RUN) {
    log(`--dry-run: 메일 본문 미리보기`);
    console.log('───────────────────────────');
    console.log('To:', MAIL_TO);
    console.log('Subject:', subject);
    console.log('───────────────────────────');
    console.log(text);
    console.log('───────────────────────────');
    log(`HTML 본문 ${html.length} bytes 생성 완료`);
    return;
  }

  if (!GMAIL_USER || !GMAIL_APP_PASSWORD) {
    log(`!!! GMAIL_USER / GMAIL_APP_PASSWORD 미설정 (.env). 메일 발송 불가.`);
    log(`  → https://myaccount.google.com/apppasswords 에서 앱 비밀번호 16자 생성 후 .env 등록`);
    process.exit(1);
  }
  if (!nodemailer) {
    log(`!!! nodemailer 미설치. 'npm install nodemailer --prefix C:/Users/User/Desktop/test' 실행 필요.`);
    process.exit(1);
  }

  const transporter = nodemailer.createTransport({
    service: 'gmail',
    auth: { user: GMAIL_USER, pass: GMAIL_APP_PASSWORD },
  });

  log(`Gmail SMTP 발송: ${GMAIL_USER} → ${MAIL_TO}`);
  const info = await transporter.sendMail({
    from: `"단테 자동 분석" <${GMAIL_USER}>`,
    to: MAIL_TO,
    subject,
    text,
    html,
  });
  log(`✅ 발송 완료: ${info.messageId}`);
}

main().catch(e => {
  console.error('치명적 오류:', e.message);
  process.exit(1);
});
