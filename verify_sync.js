/**
 * verify_sync.js — 라인업 SSOT 정합성 게이트 (발행 직전 차단)
 *
 *  목적: stocks.json(파이프라인 0순위 SSOT) ↔ AI단테.md §6 활성 라인 ↔
 *        최신 dante_analysis_*.json(슬랙이 실제로 읽는 산출물) 의 종목코드 집합이
 *        일치하는지 기계적으로 검사. 하나라도 어긋나면 exit 1 → run_daily_dante.bat이
 *        GitHub 업로드·Slack 발송을 차단(fail-closed). 과거 stocks.json 미동기화로
 *        Slack에 엉뚱한 종목이 나간 사고(2026-06-04 활성·06-10 후보) 재발 방지.
 *
 *  검사 대상(신뢰 가능한 3종만):
 *    A) stocks.json         — stocks[].code / stocks[].tag(전부 active · 후보 분류 폐지)
 *    B) AI단테.md §6        — **활성**: 라인. 인라인 <!-- 주석 --> 제거 후 (코드) 추출
 *    C) 최신 analysis.json  — stocks[].code / stocks[].category (있을 때만 · 없으면 스킵)
 *  HTML(분석_대시보드.html)은 종료탭 코드가 섞여 탭 파싱이 취약 → 의도적 제외.
 *
 *  설계 원칙(fail-safe): §6 섹션/라인 파싱에 실패하면 "통과"가 아니라 "실패"로 처리한다.
 *  ("확인 못 했으면 발행 보류" > "확인 못 했지만 발행")
 *
 *  부가 검사(2026-08-05 신설 · 비차단 경고):
 *    D) §6 헤더 비대화 감시 — claude_dante_analyze.js:337이 분석 프롬프트에 넣는 것은
 *       `section6_text.slice(0, 5000)` 뿐이다. §6 헤더 한 줄이 커지면 그 5,000자를 헤더가
 *       잠식해 「📋 라인업 SSOT」 불릿(차수 진입가·손절·주수)이 AI에게 도달하지 못하고,
 *       AI가 이미 폐기된 옛 진입가를 계속 인용한다(2026-08-05 실제 사고 · 헤더 40,935자).
 *       ⚠️ 이 유형은 에러가 아니라 "그럴듯한 오답"이라 exit 0·정합성 OK 로그로는 안 잡힌다.
 *       → 라인업 SSOT가 슬라이스 밖으로 밀리면 🚨 경고 + Slack 알림, 헤더가 임계치를 넘으면 ⚠️ 경고.
 *       비차단(exit code 불변)이다 — 발행을 막을 결함은 아니고 문서 정리를 재촉하는 신호다.
 *       조치: §6 헤더의 이력을 AI단테_아카이브.md §6.H로 이관 (AI단테.md §6 2026-08-05 블록 참조)
 *
 *  사용:
 *    node verify_sync.js                 stocks↔§6 (+ 오늘 analysis 있으면) 검사
 *    node verify_sync.js --analysis=경로  특정 analysis.json 지정
 *    node verify_sync.js --no-slack       불일치 Slack 경보 생략(콘솔만)
 *  종료코드: 0=일치 / 1=불일치 또는 파싱 실패 (§6 헤더 경고는 종료코드에 영향 없음)
 *  테스트용 env: DANTE_PROMPT_SLICE / DANTE_HEADER_WARN 으로 임계치 조정 가능
 */
'use strict';
const fs = require('fs');
const path = require('path');
const https = require('https');
require('dotenv').config({ path: path.join(__dirname, '..', '.env') });

const ROOT = __dirname;
const AI_DANTE_PATH = path.join(ROOT, 'AI단테.md');
const STOCKS_PATH = path.join(ROOT, 'stocks.json');
const DATA_DIR = path.join(ROOT, 'data');
const WEBHOOK_URL = process.env.SLACK_WEBHOOK_URL;

const argv = process.argv.slice(2);
const NO_SLACK = argv.includes('--no-slack');
const analysisArg = (argv.find(a => a.startsWith('--analysis=')) || '').split('=')[1];

function log(msg) {
  const ts = new Date(Date.now() + 9 * 60 * 60 * 1000).toISOString().replace('T', ' ').slice(0, 19);
  console.log(`[${ts}] ${msg}`);
}

const set = arr => new Set(arr);
const sortedList = s => [...s].sort();
const diff = (a, b) => [...a].filter(x => !b.has(x)).sort(); // a - b
const eqSet = (a, b) => a.size === b.size && [...a].every(x => b.has(x));

// ── A) stocks.json ───────────────────────────────────────────────
function loadStocks() {
  const j = JSON.parse(fs.readFileSync(STOCKS_PATH, 'utf-8'));
  const stocks = j.stocks || [];
  return {
    active: set(stocks.filter(s => s.tag === 'active').map(s => s.code)),
    nonActive: stocks.filter(s => s.tag !== 'active').map(s => `${s.name}(${s.code}) tag=${s.tag}`),
  };
}

// ── B) AI단테.md §6 활성/후보 라인 ───────────────────────────────
// 인라인 <!-- 주석 --> 안의 삭제종목 코드를 라인업으로 오수확하지 않도록 먼저 제거.
function loadSection6() {
  const md = fs.readFileSync(AI_DANTE_PATH, 'utf-8');
  const m = md.match(/## 6\. 현재 종목 현황[\s\S]*?(?=\n## 6\.X|\n## 7\.|\n---\n\n## )/);
  if (!m) throw new Error('§6 "현재 종목 현황" 섹션을 찾지 못함 (AI단테.md 형식 변경?) — fail-safe 차단');
  const txt = m[0];
  const lineCodes = (label) => {
    const lm = txt.match(new RegExp(`\\*\\*${label}\\*\\*\\s*:([^\\n]*)`));
    if (!lm) throw new Error(`§6 **${label}**: 라인을 찾지 못함 — fail-safe 차단`);
    const noComment = lm[1].replace(/<!--[\s\S]*?-->/g, '');
    return set([...noComment.matchAll(/\((\d{6})\)/g)].map(x => x[1]));
  };
  return { active: lineCodes('활성') };
}

// ── C) 최신 analysis.json (있을 때만) ────────────────────────────
function loadAnalysis() {
  let p = analysisArg ? path.resolve(ROOT, analysisArg) : null;
  if (!p) {
    if (!fs.existsSync(DATA_DIR)) return null;
    const files = fs.readdirSync(DATA_DIR)
      .filter(f => /^dante_analysis_\d{4}-\d{2}-\d{2}_\d{4}\.json$/.test(f))
      .sort();
    if (!files.length) return null;
    p = path.join(DATA_DIR, files[files.length - 1]);
  }
  if (!fs.existsSync(p)) return null;
  const a = JSON.parse(fs.readFileSync(p, 'utf-8'));
  const stocks = a.stocks || [];
  if (!stocks.length) return null; // recommend 회차 등 활성 라인업 없음 → 교차검증 스킵
  return {
    file: path.basename(p),
    active: set(stocks.filter(s => (s.category || s.tag) === 'active').map(s => s.code)),
  };
}

// ── D) §6 헤더 비대화 감시 (비차단 경고) ─────────────────────────
// 분석 프롬프트가 실제로 보는 것은 §6 앞 PROMPT_SLICE 자뿐이다.
// 헤더 한 줄이 그 예산을 잠식하면 라인업 SSOT 불릿이 AI에게 도달하지 않는다.
const PROMPT_SLICE = Number(process.env.DANTE_PROMPT_SLICE || 5000);
const HEADER_WARN_CHARS = Number(process.env.DANTE_HEADER_WARN || 3000);

function section6Metrics() {
  try {
    const md = fs.readFileSync(AI_DANTE_PATH, 'utf-8');
    const m = md.match(/## 6\. 현재 종목 현황[\s\S]*?(?=\n## 6\.X|\n## 7\.|\n---\n\n## )/);
    if (!m) return null;
    const txt = m[0];
    const headerLen = txt.split('\n')[0].length;
    const ssotIdx = txt.indexOf('**활성**:');       // 📋 라인업 SSOT 불릿
    return {
      headerLen,
      ssotIdx,
      ssotInSlice: ssotIdx >= 0 && ssotIdx < PROMPT_SLICE,
      headroom: PROMPT_SLICE - headerLen,
    };
  } catch { return null; }
}

// 반환: Slack으로 올릴 치명 경고 문자열(없으면 null). 콘솔 출력은 이 함수가 직접 한다.
function reportSection6Health(mx) {
  if (!mx) return null;
  const { headerLen, ssotIdx, ssotInSlice, headroom } = mx;
  if (ssotIdx < 0) {
    log(`⚠️ §6에서 「**활성**:」 라인업 SSOT 불릿을 찾지 못함 — 프롬프트 도달 여부 확인 불가`);
    return null;
  }
  if (!ssotInSlice) {
    log(`🚨 §6 헤더 비대화 — 라인업 SSOT가 분석 프롬프트 밖으로 밀렸다 (헤더 ${headerLen.toLocaleString()}자 · SSOT 시작 ${ssotIdx.toLocaleString()}자 > 슬라이스 ${PROMPT_SLICE.toLocaleString()}자)`);
    log(`   영향: 분석 AI가 차수 진입가·손절·주수를 못 본 채 판정 → 폐기된 옛 계획을 인용할 수 있다 (2026-08-05 사고와 동일 조건)`);
    log(`   조치: §6 헤더의 ((이력: ...)) 누적분을 AI단테_아카이브.md §6.H로 이관 · 발행은 차단하지 않음`);
    return `🚨 *단테 §6 헤더 비대화 — 라인업 SSOT가 분석 프롬프트에서 이탈*\n`
         + `헤더 ${headerLen.toLocaleString()}자 · SSOT 시작 ${ssotIdx.toLocaleString()}자 > 슬라이스 ${PROMPT_SLICE.toLocaleString()}자\n`
         + `분석 AI가 차수 진입가·손절·주수를 못 보고 판정한다(에러 없이 그럴듯한 오답).\n`
         + `조치: §6 헤더 이력을 AI단테_아카이브.md §6.H로 이관.`;
  }
  if (headerLen > HEADER_WARN_CHARS) {
    log(`⚠️ §6 헤더 ${headerLen.toLocaleString()}자 — 임계 ${HEADER_WARN_CHARS.toLocaleString()}자 초과 (프롬프트 여유 ${headroom.toLocaleString()}자)`);
    log(`   이력이 더 쌓이면 라인업 SSOT가 프롬프트 밖으로 밀린다 → 지금 §6.H로 이관 권장`);
  }
  return null;
}

function postSlack(text) {
  return new Promise((resolve) => {
    if (NO_SLACK || !WEBHOOK_URL) { resolve(false); return; }
    const body = JSON.stringify({
      username: process.env.SLACK_USERNAME || '단테 정합성 게이트',
      icon_emoji: ':rotating_light:',
      text,
    });
    try {
      const url = new URL(WEBHOOK_URL);
      const req = https.request(
        { hostname: url.hostname, path: url.pathname + url.search, method: 'POST',
          headers: { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(body) } },
        (res) => { res.on('data', () => {}); res.on('end', () => resolve(res.statusCode === 200)); }
      );
      req.on('error', () => resolve(false));
      req.write(body); req.end();
    } catch { resolve(false); }
  });
}

async function main() {
  let S, M, A;
  try {
    S = loadStocks();
    M = loadSection6();   // 파싱 실패 시 throw → fail-safe
    A = loadAnalysis();   // 없으면 null (정상)
  } catch (e) {
    log(`🚨 정합성 검사 불가(파싱 실패) — 발행 차단: ${e.message}`);
    await postSlack(`🚨 *단테 정합성 게이트 — 파싱 실패로 발행 차단*\n${e.message}`);
    process.exit(1);
  }

  const problems = [];
  const checkPair = (label, a, b, an, bn) => {
    if (!eqSet(a, b)) {
      const onlyA = diff(a, b), onlyB = diff(b, a);
      problems.push(
        `• ${label} 불일치\n` +
        `    ${an}: [${sortedList(a).join(', ') || '없음'}]\n` +
        `    ${bn}: [${sortedList(b).join(', ') || '없음'}]` +
        (onlyA.length ? `\n    → ${an}에만: ${onlyA.join(', ')}` : '') +
        (onlyB.length ? `\n    → ${bn}에만: ${onlyB.join(', ')}` : '')
      );
    }
  };

  checkPair('활성 (stocks.json ↔ §6)', S.active, M.active, 'stocks.json', '§6');
  // 후보 분류 폐지 — 매집 여부와 무관하게 라인업 전 종목은 active (2026-10-13)
  if (S.nonActive.length) problems.push(`• stocks.json 에 active 아닌 태그: ${S.nonActive.join(', ')}`);
  if (A) {
    checkPair(`활성 (stocks.json ↔ ${A.file})`, S.active, A.active, 'stocks.json', 'analysis');
  }

  // §6 헤더 비대화 감시 — 정합성 결과와 무관하게 매번 점검(비차단)
  const s6alert = reportSection6Health(section6Metrics());
  if (s6alert) await postSlack(s6alert);

  if (problems.length === 0) {
    const mx = section6Metrics();
    const s6 = mx ? ` · §6 헤더 ${mx.headerLen.toLocaleString()}자` : '';
    log(`✅ 라인업 정합성 OK (활성 ${S.active.size}${A ? ` · analysis 교차검증 ${A.file}` : ' · analysis 없음(스킵)'}${s6})`);
    process.exit(0);
  }

  const report = problems.join('\n');
  log('🚨 라인업 SSOT 불일치 — GitHub 업로드·Slack 발송 차단');
  console.log(report);
  log('   조치: stocks.json / AI단테.md §6 / analysis 중 어긋난 쪽을 맞춘 뒤 재실행');
  await postSlack(`🚨 *단테 라인업 SSOT 불일치 — 발행 차단(fail-closed)*\n동기화 빠뜨림 의심. 어긋난 원본을 맞춘 뒤 재실행.\n\`\`\`\n${report}\n\`\`\``);
  process.exit(1);
}

main();
