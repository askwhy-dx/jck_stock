/**
 * delete_github_images.js — GitHub financial/images/ 디렉토리 전체 삭제 (일회성)
 *   사유: 이미지 GitHub 미발행 정책(2026-06-19). 기존 누적 이미지 정리.
 *   GitHub Contents API는 디렉토리 단위 삭제가 없어 파일별 DELETE 반복.
 *   (목록은 최대 1000개 반환·무페이지네이션 → 초과 시 재실행)
 */
const path = require('path');
require('dotenv').config({ path: path.join(__dirname, '..', '..', '.env') });  // tools/ → test/.env
const https = require('https');

const TOKEN = process.env.GITHUB_TOKEN;
const OWNER = process.env.GITHUB_OWNER || 'jichang999-dotcom';
const REPO = 'tes2'; // 공개 리포 고정 (2026-09-17: tes1 은 사내 자료 이력 때문에 비공개, .env GITHUB_REPO 를 따르지 않는다)
const BRANCH = 'main';
const DIR = 'financial/images';

if (!TOKEN) { console.error('GITHUB_TOKEN 미설정'); process.exit(1); }

function req(method, urlPath, body) {
  return new Promise((resolve, reject) => {
    const bodyStr = body ? JSON.stringify(body) : null;
    const opts = {
      hostname: 'api.github.com', path: urlPath, method,
      headers: {
        'Authorization': `token ${TOKEN}`,
        'User-Agent': 'image-cleaner',
        'Accept': 'application/vnd.github.v3+json',
        'Content-Type': 'application/json',
        ...(bodyStr ? { 'Content-Length': Buffer.byteLength(bodyStr) } : {}),
      },
    };
    const r = https.request(opts, res => {
      let data = '';
      res.on('data', c => data += c);
      res.on('end', () => { try { resolve({ status: res.statusCode, body: JSON.parse(data) }); } catch { resolve({ status: res.statusCode, body: data }); } });
    });
    r.on('error', reject);
    if (bodyStr) r.write(bodyStr);
    r.end();
  });
}

(async () => {
  console.log(`=== financial/images/ 디렉토리 삭제 시작 ===`);
  const enc = DIR.split('/').map(encodeURIComponent).join('/');
  const list = await req('GET', `/repos/${OWNER}/${REPO}/contents/${enc}?ref=${BRANCH}`);
  if (list.status !== 200) {
    console.log(`디렉토리 없음/접근 실패 [${list.status}] ${list.body?.message || ''}`);
    return;
  }
  const files = Array.isArray(list.body) ? list.body.filter(f => f.type === 'file') : [];
  console.log(`대상 파일: ${files.length}개`);
  if (files.length === 0) { console.log('삭제할 파일 없음 (이미 비어있음)'); return; }

  let ok = 0, fail = 0;
  for (const f of files) {
    const fEnc = f.path.split('/').map(encodeURIComponent).join('/');
    const res = await req('DELETE', `/repos/${OWNER}/${REPO}/contents/${fEnc}`, {
      message: `🗑️ financial/images/ 정리 — 이미지 GitHub 미발행 정책 (2026-06-19)`,
      sha: f.sha, branch: BRANCH,
    });
    if (res.status === 200) { ok++; if (ok % 25 === 0) console.log(`  ...${ok}/${files.length} 삭제`); }
    else { fail++; console.error(`  ❌ ${f.name} [${res.status}] ${res.body?.message || ''}`); }
  }
  console.log(`\n완료: 삭제 ${ok} / 실패 ${fail} (총 ${files.length})`);
  if (files.length >= 1000) console.log('⚠️ 1000개 한도 — 남은 파일 위해 재실행 필요');
})().catch(e => { console.error('에러:', e); process.exit(1); });
