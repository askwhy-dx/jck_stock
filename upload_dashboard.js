/**
 * upload_dashboard.js — 분석_대시보드.html 전용 업로더
 * 한글 파일명 URL 인코딩 처리 포함
 */
const path = require('path');
require('dotenv').config({ path: path.join(__dirname, '..', '.env') });
const fs = require('fs');
const https = require('https');
const crypto = require('crypto');

// git blob SHA (= GitHub contents API가 반환하는 sha)를 로컬에서 계산.
// 원격 sha와 동일하면 내용 무변경 → PUT 스킵 (대량 이미지 재업로드/네트워크 블립 노출 제거)
function gitBlobSha(buf) {
  const header = Buffer.from(`blob ${buf.length}\0`, 'utf-8');
  return crypto.createHash('sha1').update(Buffer.concat([header, buf])).digest('hex');
}

const TOKEN = process.env.GITHUB_TOKEN;
const OWNER = process.env.GITHUB_OWNER || 'jichang999-dotcom';
const REPO = 'tes2'; // 공개 리포 고정 (2026-09-17: tes1 은 사내 자료 이력 때문에 비공개, .env GITHUB_REPO 를 따르지 않는다)
const BRANCH = 'main';
const LOCAL_FILE = path.join(__dirname, '분석_대시보드.html');
const REMOTE_PATH = 'financial/분석_대시보드.html';
const REMOTE_PATH_ENCODED = REMOTE_PATH.split('/').map(encodeURIComponent).join('/');
const AI_DANTE_LOCAL = path.join(__dirname, 'AI단테.md');
const AI_DANTE_REMOTE = 'financial/AI단테.md';
const AI_DANTE_REMOTE_ENCODED = AI_DANTE_REMOTE.split('/').map(encodeURIComponent).join('/');
// 2026-05-19 분리: AI단테.md 마스터에서 옛 §6 헤더·종목 카드·매매일지를 별도 파일로 분리
const AI_DANTE_ARCHIVE_LOCAL = path.join(__dirname, 'AI단테_아카이브.md');
const AI_DANTE_ARCHIVE_REMOTE = 'financial/AI단테_아카이브.md';
const AI_DANTE_ARCHIVE_REMOTE_ENCODED = AI_DANTE_ARCHIVE_REMOTE.split('/').map(encodeURIComponent).join('/');
const AI_DANTE_JOURNAL_LOCAL = path.join(__dirname, 'AI단테_매매일지.md');
const AI_DANTE_JOURNAL_REMOTE = 'financial/AI단테_매매일지.md';
const AI_DANTE_JOURNAL_REMOTE_ENCODED = AI_DANTE_JOURNAL_REMOTE.split('/').map(encodeURIComponent).join('/');

if (!TOKEN) { console.error('GITHUB_TOKEN 미설정'); process.exit(1); }
if (!fs.existsSync(LOCAL_FILE)) { console.error('로컬 파일 없음:', LOCAL_FILE); process.exit(1); }

// 네트워크 블립 하드닝 (2026-07-09): 커밋(PUT) 중 소켓 오류/타임아웃/5xx 한 건에
//   promise가 reject → 최상위 catch → exit 1 로 튀던 간헐 오탐 제거.
//   전략: 일시적 실패(소켓 error, socket timeout, 429/5xx)만 백오프 후 재시도.
//   영구 실패(401/403/404/409/422 등)는 그대로 반환 → 상위에서 진짜 실패로 처리.
const REQ_MAX_RETRY = 2;               // 최초 1회 + 재시도 2회 = 총 3회
const REQ_BACKOFF_MS = [800, 1800];    // attempt 0 실패 후 대기, attempt 1 실패 후 대기
const REQ_TIMEOUT_MS = 20000;          // 소켓 무응답 상한

function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }
function isTransientStatus(s) { return s === 429 || (s >= 500 && s <= 599); }

function reqOnce(method, urlPath, body) {
  return new Promise((resolve, reject) => {
    const bodyStr = body ? JSON.stringify(body) : null;
    const opts = {
      hostname: 'api.github.com',
      path: urlPath,
      method,
      headers: {
        'Authorization': `token ${TOKEN}`,
        'User-Agent': 'dashboard-uploader',
        'Accept': 'application/vnd.github.v3+json',
        'Content-Type': 'application/json',
        ...(bodyStr ? { 'Content-Length': Buffer.byteLength(bodyStr) } : {}),
      },
    };
    let settled = false;                              // 이중 settle 방지(응답 후 소켓 error 등)
    let timer;
    const finish = fn => { if (!settled) { settled = true; clearTimeout(timer); fn(); } };
    const r = https.request(opts, res => {
      let data = '';
      res.on('data', c => data += c);
      res.on('end', () => finish(() => {
        try { resolve({ status: res.statusCode, body: JSON.parse(data) }); }
        catch { resolve({ status: res.statusCode, body: data }); }
      }));
    });
    // 하드 상한: connect+전송 전체를 REQ_TIMEOUT_MS로 제한. request.setTimeout은
    //   "소켓 연결 후"에만 무장돼 connect 단계 hang을 못 막으므로 별도 타이머 사용.
    //   destroy → 'error' 이벤트 → finish(reject) 경로로 상위 재시도 트리거.
    timer = setTimeout(() => r.destroy(new Error(`request timeout ${REQ_TIMEOUT_MS}ms`)), REQ_TIMEOUT_MS);
    r.on('error', err => finish(() => reject(err)));
    if (bodyStr) r.write(bodyStr);
    r.end();
  });
}

async function req(method, urlPath, body) {
  const label = `${method} ${urlPath.split('?')[0]}`;
  let lastErr;
  for (let attempt = 0; attempt <= REQ_MAX_RETRY; attempt++) {
    try {
      const res = await reqOnce(method, urlPath, body);
      if (isTransientStatus(res.status) && attempt < REQ_MAX_RETRY) {
        console.warn(`    ⏳ 일시 오류 [${res.status}] 재시도 ${attempt + 1}/${REQ_MAX_RETRY} — ${label}`);
        await sleep(REQ_BACKOFF_MS[attempt]);
        continue;
      }
      return res;
    } catch (err) {
      lastErr = err;
      if (attempt < REQ_MAX_RETRY) {
        console.warn(`    ⏳ 네트워크 오류(${err.code || err.message}) 재시도 ${attempt + 1}/${REQ_MAX_RETRY} — ${label}`);
        await sleep(REQ_BACKOFF_MS[attempt]);
        continue;
      }
      throw lastErr;
    }
  }
  throw lastErr;   // 도달 불가(방어)
}

async function uploadFile(localPath, remotePath, remoteEncoded, commitMessage) {
  if (!fs.existsSync(localPath)) { console.warn(`  skip (not found): ${localPath}`); return; }
  const content = fs.readFileSync(localPath);
  console.log(`[${remotePath}] 로컬 파일 읽기 (${content.length.toLocaleString()} bytes)`);

  const shaRes = await req('GET', `/repos/${OWNER}/${REPO}/contents/${remoteEncoded}?ref=${BRANCH}`);
  const sha = shaRes.status === 200 ? shaRes.body.sha : null;
  console.log(`    → SHA: ${sha ? sha.slice(0,10)+'...' : '신규 파일'}`);

  // 내용 무변경이면 PUT 생략 (불필요한 커밋·API 호출·전송 오류 노출 제거)
  if (sha && gitBlobSha(content) === sha) {
    console.log(`    ⏭️  변경 없음 — 스킵`);
    return true;
  }

  const res = await req('PUT', `/repos/${OWNER}/${REPO}/contents/${remoteEncoded}`, {
    message: commitMessage,
    content: content.toString('base64'),
    branch: BRANCH,
    ...(sha ? { sha } : {}),
  });

  if (res.status === 200 || res.status === 201) {
    const pagesUrl = `https://${OWNER}.github.io/${REPO}/${remotePath}`;
    console.log(`    ✅ 커밋: ${res.body.commit?.sha?.slice(0,10)}`);
    console.log(`    페이지: ${pagesUrl}`);
    return true;
  } else {
    console.error(`    ❌ 실패 [${res.status}]`, res.body?.message || res.body);
    return false;
  }
}

async function deleteFile(remotePath, remoteEncoded, commitMessage) {
  const shaRes = await req('GET', `/repos/${OWNER}/${REPO}/contents/${remoteEncoded}?ref=${BRANCH}`);
  if (shaRes.status !== 200) { console.log(`    (already absent) ${remotePath}`); return; }
  const res = await req('DELETE', `/repos/${OWNER}/${REPO}/contents/${remoteEncoded}`, {
    message: commitMessage,
    sha: shaRes.body.sha,
    branch: BRANCH,
  });
  if (res.status === 200) console.log(`    🗑️  삭제 완료: ${remotePath}`);
  else console.error(`    ❌ 삭제 실패 [${res.status}]`, res.body?.message);
}

// images/ 폴더 자동 스캔 → financial/images/ 경로로 업로드
const IMAGES_DIR = path.join(__dirname, 'images');
function scanImages() {
  if (!fs.existsSync(IMAGES_DIR)) return [];
  return fs.readdirSync(IMAGES_DIR)
    .filter(f => /\.(png|jpe?g|gif|webp|svg)$/i.test(f))
    .map(f => ({
      local: path.join(IMAGES_DIR, f),
      remote: `financial/images/${f}`,
      remoteEncoded: `financial/images/${f}`.split('/').map(encodeURIComponent).join('/'),
    }));
}

(async () => {
  const commitMsg = `2026-11-03 💼 매입 탭을 손익비 왼쪽으로 이동.`;

  console.log(`=== [1/4] financial/ 경로 업로드 (HTML + MD 3-tier) ===`);
  const results = [];
  results.push(await uploadFile(LOCAL_FILE, REMOTE_PATH, REMOTE_PATH_ENCODED, commitMsg));
  results.push(await uploadFile(AI_DANTE_LOCAL, AI_DANTE_REMOTE, AI_DANTE_REMOTE_ENCODED, commitMsg));
  results.push(await uploadFile(AI_DANTE_ARCHIVE_LOCAL, AI_DANTE_ARCHIVE_REMOTE, AI_DANTE_ARCHIVE_REMOTE_ENCODED, commitMsg));
  results.push(await uploadFile(AI_DANTE_JOURNAL_LOCAL, AI_DANTE_JOURNAL_REMOTE, AI_DANTE_JOURNAL_REMOTE_ENCODED, commitMsg));

  // [2/4] 이미지 업로드 — 영구 비활성화 (2026-06-19 사용자 지시).
  //   사유: 신규 이미지 다량 PUT 시 GitHub API 실패(exit 255) + 대용량 누적.
  //   차트/투자자공매 PNG는 로컬 분석(claude -p Read)에만 사용, GitHub 발행 대상에서 제외.
  console.log(`\n=== [2/4] financial/images/ 이미지 업로드 — 비활성화 (앞으로 업로드 금지) ===`);
  console.log(`    (이미지는 로컬 분석 전용 · GitHub 미발행)`);

  console.log(`\n=== [3/4] counsel-report/ 레거시 경로 정리 ===`);
  await deleteFile('counsel-report/분석_대시보드.html',
    'counsel-report/분석_대시보드.html'.split('/').map(encodeURIComponent).join('/'),
    `counsel-report/ 경로 정리 — financial/로 이전 (2026-04-16)`);
  await deleteFile('counsel-report/AI단테.md',
    'counsel-report/AI단테.md'.split('/').map(encodeURIComponent).join('/'),
    `counsel-report/ 경로 정리 — financial/로 이전 (2026-04-16)`);

  console.log(`\n=== [4/4] 완료 ===`);
  console.log(`✅ 정식 URL: https://${OWNER}.github.io/${REPO}/${REMOTE_PATH}`);

  // 종료코드 결정론화: 재시도 후에도 남은 진짜 실패(비-2xx)가 있을 때만 exit 1.
  //   일시적 네트워크 블립은 req() 재시도로 흡수되므로 여기 도달 안 함.
  const failed = results.filter(r => r === false).length;
  if (failed > 0) {
    console.error(`⚠️ 업로드 실패 ${failed}건 — 위 로그의 ❌ 확인 (발행 미완)`);
    process.exit(1);
  }
})().catch(e => { console.error('에러:', e); process.exit(1); });
