// One-off: simplify spreadsheet to 2 input sheets only.
//   - Delete: 00_사용방법, 03_KPI_자동집계, 04_로드맵_L0_L5, 05_대시보드,
//             06_L1→L2, 07_L2→L3, 08_L3→L4, 09_L4→L5, 10_L5_유지
//   - Rename: 02_월별스냅샷 → 02_월별수익
//   - Keep: 01_거래일지, 02_월별수익
// All computations move to build_preview_html.js (HTML-side).
const path = require('path');
const { google } = require('googleapis');

const SPREADSHEET_ID = '1DhDbm9jPTT5d7QNtFCp6jEPBOfdtQk3cs4SiwHr4R6Q';
const CREDS = path.join(__dirname, '..', '..', 'credentials.json');

const DELETE_NAMES = new Set([
  '00_사용방법',
  '03_KPI_자동집계',
  '04_로드맵_L0_L5',
  '05_대시보드',
  '06_L1→L2',
  '07_L2→L3',
  '08_L3→L4',
  '09_L4→L5',
  '10_L5_유지',
]);
const RENAME = { '02_월별스냅샷': '02_월별수익' };

(async () => {
  const auth = new google.auth.GoogleAuth({
    keyFile: CREDS,
    scopes: ['https://www.googleapis.com/auth/spreadsheets'],
  });
  const sheets = google.sheets({ version: 'v4', auth });

  const meta = await sheets.spreadsheets.get({ spreadsheetId: SPREADSHEET_ID });
  const requests = [];
  let kept = [];
  for (const s of meta.data.sheets) {
    const title = s.properties.title;
    const id = s.properties.sheetId;
    if (DELETE_NAMES.has(title)) {
      requests.push({ deleteSheet: { sheetId: id } });
    } else if (RENAME[title]) {
      requests.push({
        updateSheetProperties: {
          properties: { sheetId: id, title: RENAME[title] },
          fields: 'title',
        },
      });
      kept.push(RENAME[title]);
    } else {
      kept.push(title);
    }
  }

  if (!requests.length) {
    console.log('No-op — already simplified. Kept:', kept.join(', '));
    return;
  }
  await sheets.spreadsheets.batchUpdate({
    spreadsheetId: SPREADSHEET_ID,
    requestBody: { requests },
  });
  console.log('Applied', requests.length, 'changes.');
  console.log('Kept sheets:', kept.join(', '));
})().catch(e => { console.error(e.message); process.exit(1); });
