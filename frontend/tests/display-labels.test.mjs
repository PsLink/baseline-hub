import { test } from 'node:test'
import assert from 'node:assert/strict'
import { statusLabel, metricLabel } from '../src/platform/i18n/display.ts'
test('display statuses localize without altering unknown technical identifiers', () => {
  assert.equal(statusLabel('queued', 'zh'), '排队中')
  assert.equal(statusLabel('open', 'en'), 'Open')
  assert.equal(statusLabel('future_status', 'zh'), 'future_status')
})
test('known metrics include units while unknown metric keys remain searchable', () => {
  assert.equal(metricLabel('perf.total_tpm_card_norm', 'zh'), '总吞吐量（TPM@8）')
  assert.equal(metricLabel('perf.ttft_ms', 'zh'), '首 token 延迟（ms）')
  assert.equal(metricLabel('quality.custom_score', 'zh'), 'quality.custom_score')
})
