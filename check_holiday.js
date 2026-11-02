/**
 * check_holiday.js — KRX 영업일 검사
 *
 * 동작:
 *   exit 0 = 영업일 (배치 진행)
 *   exit 1 = 휴장일 (배치 스킵)
 *
 * 옵션:
 *   --date YYYY-MM-DD   특정 날짜로 검사 (기본: 오늘 KST)
 *   --force             휴장일 무시하고 항상 영업일로 처리 (테스트용)
 */
const fs = require('fs');
const path = require('path');

const argv = process.argv.slice(2);
const dateArg = (argv.find(a => a.startsWith('--date=')) || '').slice('--date='.length)
  || (argv[argv.indexOf('--date') + 1] && !argv[argv.indexOf('--date') + 1].startsWith('--')
       ? argv[argv.indexOf('--date') + 1] : null);
const force = argv.includes('--force');

function todayKst() {
  const ms = Date.now() + 9 * 60 * 60 * 1000;
  return new Date(ms).toISOString().slice(0, 10);
}

const date = dateArg || todayKst();
// KST 자정 기준 요일 계산: 'YYYY-MM-DD'를 UTC 자정으로 만들어 getUTCDay().
// 한국 자정과 UTC 자정은 9시간 차이지만 같은 달력일 → 요일 동일.
const [Y, M, D] = date.split('-').map(Number);
const dayOfWeek = new Date(Date.UTC(Y, M - 1, D)).getUTCDay();
const weekdayNames = ['일','월','화','수','목','금','토'];

if (force) {
  console.log(`[check_holiday] ${date} (${weekdayNames[dayOfWeek]}) — --force, 영업일 처리`);
  process.exit(0);
}

if (dayOfWeek === 0 || dayOfWeek === 6) {
  console.log(`[check_holiday] ${date} (${weekdayNames[dayOfWeek]}) — 주말, 휴장`);
  process.exit(1);
}

const calendarPath = path.join(__dirname, 'config', 'holiday_kr.json');
let holidays = [];
if (fs.existsSync(calendarPath)) {
  try {
    const cal = JSON.parse(fs.readFileSync(calendarPath, 'utf-8'));
    const year = date.slice(0, 4);
    holidays = (cal.holidays && cal.holidays[year]) || [];
  } catch (e) {
    console.error(`[check_holiday] 캘린더 파싱 실패: ${e.message} — 안전상 영업일 처리`);
    process.exit(0);
  }
} else {
  console.warn(`[check_holiday] 캘린더 파일 없음: ${calendarPath} — 안전상 영업일 처리`);
  process.exit(0);
}

if (holidays.includes(date)) {
  console.log(`[check_holiday] ${date} (${weekdayNames[dayOfWeek]}) — KRX 휴장일, 스킵`);
  process.exit(1);
}

console.log(`[check_holiday] ${date} (${weekdayNames[dayOfWeek]}) — 영업일, 진행`);
process.exit(0);
