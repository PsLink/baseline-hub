import { test } from 'node:test'
import assert from 'node:assert/strict'
import { qualityVerdict, formatRankingValue, filterBoardRows, hasQualityAssessment } from '../src/features/hub/utils/board.ts'

test('compact quality summaries preserve missing and failed verdicts', () => {
  assert.equal(qualityVerdict([]), 'unknown')
  assert.equal(qualityVerdict([{ ok: true }, { ok: null }]), 'unknown')
  assert.equal(qualityVerdict([{ ok: true }, { ok: false }, { ok: null }]), 'failed')
  assert.equal(qualityVerdict([{ ok: true }, { ok: true }]), 'passed')
})

test('custom ranking scores retain meaningful precision instead of rounding to throughput', () => {
  assert.equal(formatRankingValue(0.87654, 'quality.score'), '0.87654')
  assert.equal(formatRankingValue(128400.6, 'perf.total_tpm_card_norm'), '128,401')
  assert.equal(formatRankingValue(null, 'quality.score'), '—')
  assert.equal(formatRankingValue(NaN, 'quality.score'), '—')
})

test('board search matches contributors, engine and image as well as names', () => {
  const rows = [
    { name: 'Optimized', submitted_by: 'Alice', engine: 'sglang', image: 'repo/image:v1' },
    { name: 'Reference', submitted_by: 'Bob', engine: 'vllm', image: 'repo/image:v2' },
  ]
  assert.deepEqual(filterBoardRows(rows, ' ALICE '), [rows[0]])
  assert.deepEqual(filterBoardRows(rows, 'vllm'), [rows[1]])
  assert.deepEqual(filterBoardRows(rows, ':v2'), [rows[1]])
  assert.deepEqual(filterBoardRows(rows, 'missing'), [])
  assert.equal(filterBoardRows(rows, '  '), rows)
})

test('performance-only tracks do not show a misleading unreported quality column', () => {
  // The real performance-only board has passing runs with empty quality arrays.
  const rows = [
    { rank: 1, passed: true, quality: [] },
    { rank: 2, passed: true, quality: [] },
  ]
  const modules = [{ key: 'perf_guidellm_sweep', is_scenario: true }]
  assert.equal(hasQualityAssessment(rows, modules), false)
  assert.equal(qualityVerdict(rows[0].quality), 'unknown')
})

test('configured quality checks remain visible even before a verdict is available', () => {
  assert.equal(hasQualityAssessment([{ quality: [] }], [
    { is_scenario: true }, { is_scenario: false },
  ]), true)
  assert.equal(qualityVerdict([]), 'unknown')
})

test('recorded quality results remain visible when the live benchmark is unavailable', () => {
  assert.equal(hasQualityAssessment([{ quality: [{ ok: true }] }], undefined), true)
  assert.equal(hasQualityAssessment([{ quality: [] }], undefined), false)
})
