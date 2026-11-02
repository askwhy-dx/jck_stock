// node test_auto_intake.js — auto_intake.js 핵심 로직 자가 점검 (실파일 무수정)
'use strict';
const assert = require('assert');
const { scenario, tick, removeFromLineup } = require('./auto_intake.js');

assert.strictEqual(tick(1999), 1); assert.strictEqual(tick(4999), 5); assert.strictEqual(tick(15000), 10);
assert.strictEqual(tick(49999), 50); assert.strictEqual(tick(150000), 100); assert.strictEqual(tick(300000), 500);

// 라인업 줄 삭제: 가운데·마지막
const stocks = [{ name: 'A주', code: '000001' }, { name: 'NHN KCP', code: '060250' }, { name: 'C주', code: '000003' }];
const line = '> - **활성**: A주 (000001) **미체결 x** (1차 1 · 목표 2) · NHN KCP (060250) **미체결 🤖자동** (1차 3 · 손절 1) · C주 (000003) **미체결 y** (z)';
assert.strictEqual(removeFromLineup(line, stocks, '060250'), '> - **활성**: A주 (000001) **미체결 x** (1차 1 · 목표 2) · C주 (000003) **미체결 y** (z)');
assert.strictEqual(removeFromLineup(line, stocks, '000003'), '> - **활성**: A주 (000001) **미체결 x** (1차 1 · 목표 2) · NHN KCP (060250) **미체결 🤖자동** (1차 3 · 손절 1)');
assert.strictEqual(removeFromLineup(line, stocks, '000001'), '> - **활성**: NHN KCP (060250) **미체결 🤖자동** (1차 3 · 손절 1) · C주 (000003) **미체결 y** (z)');

// 시나리오: 완만한 상승 + 파동 → 사다리 순서·§4.3 ±2%·§4.4 버퍼
const bars = Array.from({ length: 260 }, (_, i) => {
  const c = Math.round(10000 + i * 15 + 600 * Math.sin(i / 6));
  return { date: String(20250101 + i), close: c, high: c + 120, low: c - 120, volume: 100000 };
});
const sc = scenario(bars, null);
assert.ok(sc.ok, sc.reason);
assert.ok(sc.p1 > sc.p2 && sc.p2 > sc.p3 && sc.p3 > sc.stop, 'p1>p2>p3>stop');
assert.ok(sc.p2 >= sc.p1 * 0.98, '2차는 1차 −2% 이내');
assert.ok(sc.p3 >= sc.stop / 0.98 && sc.p3 <= sc.stop / 0.95, '3차 버퍼 2~5%');
assert.ok(sc.unit * (sc.p1 + sc.p2 + 2 * sc.p3) <= 1_000_000, '100만 캡');
assert.ok(sc.target > sc.avg * 1.05, '목표는 평단 +5% 이상 저항');
assert.ok(!scenario(bars.slice(0, 100), null).ok, 'MA224 불가 시 거절');
console.log('OK test_auto_intake', { p1: sc.p1, p2: sc.p2, p3: sc.p3, stop: sc.stop, target: sc.target, rr: +sc.rr.toFixed(2) });
