const XLSX = require('xlsx');
const wb = XLSX.readFile('C:\\Users\\User\\Desktop\\주식_레벨업_통합.xlsx', { cellFormula: true });
console.log('Sheet names:', wb.SheetNames);
for (const name of wb.SheetNames) {
  const ws = wb.Sheets[name];
  const ref = ws['!ref'];
  console.log(`\n=== ${name} (${ref}) ===`);
  // print first 6 rows
  const rows = XLSX.utils.sheet_to_json(ws, { header: 1, defval: '', blankrows: false });
  rows.slice(0, 6).forEach((r, i) => console.log(`  [${i}]`, JSON.stringify(r).slice(0, 220)));
}
// Check a known formula cell
const kpi = wb.Sheets['03_KPI_자동집계'];
console.log('\nKPI C2 (총거래):', kpi['C2']);
console.log('KPI C10 (손절준수율):', kpi['C10']);
console.log('KPI C19 (자립지수):', kpi['C19']);
const l2l3 = wb.Sheets['07_L2→L3'];
console.log('\nL2→L3 E5 (운영기간 본인값):', l2l3['E5']);
console.log('L2→L3 G5 (판정):', l2l3['G5']);
console.log('L2→L3 D17 (최종판정):', l2l3['D17']);
