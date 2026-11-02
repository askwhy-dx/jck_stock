const XLSX = require('xlsx');
const path = require('path');
const fs = require('fs');

const dir = 'C:\\Users\\User\\Desktop\\새 폴더';
const files = ['Auto-aggregation.xlsx', 'investment_roadmap_L0_L5.xlsx', 'level_checklist.xlsx'];

const out = [];
for (const f of files) {
  const fullpath = path.join(dir, f);
  out.push('='.repeat(80));
  out.push('FILE: ' + f);
  out.push('='.repeat(80));
  const wb = XLSX.readFile(fullpath);
  for (const sheetName of wb.SheetNames) {
    const ws = wb.Sheets[sheetName];
    const rows = XLSX.utils.sheet_to_json(ws, { header: 1, defval: '', blankrows: false });
    // trim trailing blanks
    const nonEmpty = rows.filter(r => r.some(c => c !== '' && c !== null && c !== undefined));
    out.push(`\n--- Sheet: ${sheetName} (nonEmptyRows=${nonEmpty.length}) ---`);
    nonEmpty.forEach((r, i) => out.push(`[${i}] ` + JSON.stringify(r)));
  }
  out.push('\n');
}
fs.writeFileSync(path.join(__dirname, 'xlsx_dump.txt'), out.join('\n'), 'utf8');
console.log('Wrote xlsx_dump.txt, size=', fs.statSync('xlsx_dump.txt').size);
