'use strict';

const assert = require('assert');
const { entryConditionGate, compareUnfilledInfo } = require('./reorder_active_cards');
const { normalizeEntryConditions, parsePositionMap, normalizeDecisionForPosition, renderEntryGateSummary, patchEntryGateSummary } = require('./claude_dante_analyze');
const { buildBlocks } = require('./slack_send');

function conditions(overrides = {}) {
  return {
    source: 'schedule_flow_images',
    price: { status: 'pass' },
    bullish_candle: { status: 'pass' },
    volume: { status: 'pass' },
    foreign_net_buy: { status: 'pass', value_shares: 100 },
    program_net_buy: { status: 'pass', value_shares: 200 },
    execution_strength: { status: 'not_required' },
    ...overrides,
  };
}

const allPass = entryConditionGate({ entry_conditions: conditions() });
assert.strictEqual(allPass.met, true);
assert.strictEqual(allPass.passCount, 5);

const foreignSell = entryConditionGate({
  entry_conditions: conditions({ foreign_net_buy: { status: 'pass', value_shares: -1 } }),
});
assert.strictEqual(foreignSell.met, false);
assert.deepStrictEqual(foreignSell.failed, ['foreign_net_buy']);

const missingFlow = entryConditionGate({ entry_conditions: conditions({ program_net_buy: undefined }) });
assert.strictEqual(missingFlow.met, false);
assert.deepStrictEqual(missingFlow.unknown, ['program_net_buy']);

const userConfirmed = entryConditionGate({
  entry_conditions: conditions({
    source: 'user_confirmed',
    foreign_net_buy: { status: 'pass', value_shares: null, source: 'user_confirmed' },
    program_net_buy: { status: 'pass', value_shares: null, source: 'user_confirmed' },
  }),
});
assert.strictEqual(userConfirmed.met, true);

const chartHeaderOnly = entryConditionGate({
  entry_conditions: conditions({
    foreign_net_buy: { status: 'unknown', value_shares: null, direction: 'buy', source: 'chart_header_dom' },
  }),
});
assert.strictEqual(chartHeaderOnly.met, false);
assert.deepStrictEqual(chartHeaderOnly.unknown, ['foreign_net_buy']);

const investorImageOverridesHeader = {
  name: '수급충돌테스트', code: '999997', decision: '⏸ 보류 (관망)', eval_gain: '0원 (미체결)',
  entry_conditions: conditions({
    foreign_net_buy: { status: 'pass', value_shares: -2419, direction: 'buy', source: 'schedule_flow_image' },
  }),
};
normalizeEntryConditions(
  investorImageOverridesHeader,
  '2026-08-21 09:30 KST',
  { foreign_flow_direction: 'buy', foreign_flow_label: '외국인 순매수 100위' },
);
assert.strictEqual(investorImageOverridesHeader.entry_conditions.foreign_net_buy.status, 'fail');
assert.strictEqual(investorImageOverridesHeader.entry_conditions.foreign_net_buy.value_shares, -2419);

const qualifiedFar = {
  code: '000001', conditionMet: true, conditionPassCount: 5, tier: 'T2', gap: 4.0,
};
const staleReadyNear = {
  code: '000002', conditionMet: false, conditionPassCount: 4, tier: 'T1', gap: 0.5,
};
assert.ok(compareUnfilledInfo(qualifiedFar, staleReadyNear) < 0);

const normalized = {
  name: '테스트', code: '999999', decision: '🟢 매수 준비', eval_gain: '0원 (미체결)',
  auto_conservative_action: '기존 행동',
  entry_conditions: conditions({ foreign_net_buy: { status: 'pass', value_shares: 0 } }),
};
normalizeEntryConditions(normalized, '2026-08-20 15:30 KST');
assert.strictEqual(normalized.entry_conditions.all_required_met, false);
assert.strictEqual(normalized.decision, '⏸ 보류 (관망)');

const volumeRuleOverride = {
  name: '거래량테스트', code: '999998', decision: '⏸ 보류 (관망)', eval_gain: '0원 (미체결)',
  entry_conditions: conditions({ volume: { status: 'fail', actual: 35150, minimum: 42000, maximum: 90000 } }),
};
normalizeEntryConditions(
  volumeRuleOverride,
  '2026-08-20 15:30 KST',
  {},
  { volumeMinimum: 22700, volumeMaximum: 48700, executionStrengthThreshold: null },
);
assert.strictEqual(volumeRuleOverride.entry_conditions.volume.status, 'pass');
assert.strictEqual(volumeRuleOverride.entry_conditions.volume.minimum, 22700);

const section6 = `## 6. 현재 종목 현황
> - **활성**: 프로텍 (053610) **1차 체결 보유** (08-25 2주 @61,300 / 2차 60,400 2주) · 대원전선 (006340) **미체결** (1차 15,200 / 69주)`;
const positions = parsePositionMap(section6, [
  { name: '프로텍', code: '053610' },
  { name: '대원전선', code: '006340' },
]);
assert.deepStrictEqual(positions.get('053610'), {
  status: 'holding', label: '1차 체결 보유', quantity: 2, average_price: 61300,
  source: 'AI단테.md §6 라인업 SSOT',
});
assert.strictEqual(positions.get('006340').status, 'unfilled');

const heldWait = { name: '프로텍', code: '053610', decision: '⏸ 보류 (관망)' };
normalizeDecisionForPosition(heldWait, positions.get('053610'), () => {});
assert.strictEqual(heldWait.decision, '🟡 보유 유지');
assert.strictEqual(heldWait.position.quantity, 2);

const heldStop = { name: '프로텍', code: '053610', decision: '🚫 손절 검토' };
normalizeDecisionForPosition(heldStop, positions.get('053610'), () => {});
assert.strictEqual(heldStop.decision, '🚫 손절 검토');

const slackBlocks = buildBlocks({ stocks: [{ ...heldWait, category: 'active' }], summary: {} });
const slackText = slackBlocks.flatMap(b => b.fields || (b.text ? [b.text] : []))
  .map(x => x.text || '').join('\n');
assert.ok(slackText.includes('🟡 유저판단: 🟡 보유 유지'));
assert.ok(slackText.includes('📦 포지션: 보유 2주 · 평단 61,300원'));

const gateAnalysis = {
  date: '2026-08-26', time: '1206',
  stocks: [
    {
      name: '한화오션', code: '042660', current_price: '86,700',
      position: { status: 'unfilled' },
      entry_conditions: conditions({
        volume: { status: 'fail', actual: 498120, minimum: 1022000 },
        foreign_net_buy: { status: 'fail', value_shares: 0 },
      }),
    },
  ],
};
const gateHtml = renderEntryGateSummary(gateAnalysis);
assert.ok(gateHtml.includes('한화오션'));
assert.ok(gateHtml.includes('3/5'));
assert.ok(gateHtml.includes('498,120주, 최소 1,022,000주 미달'));
const patchedSummary = patchEntryGateSummary('<div class="tab-content" id="tab-summary">\nOLD</div>', gateAnalysis);
assert.ok(patchedSummary.indexOf('entry-gate-summary') < patchedSummary.indexOf('OLD'));
assert.strictEqual((patchEntryGateSummary(patchedSummary, gateAnalysis).match(/id="entry-gate-summary"/g) || []).length, 1);

console.log(JSON.stringify({ status: 'PASS', tests: 20 }));
