import type { TrackBoardRow, TrackQualityCell } from '../types.ts'

/** Empty quality results alone do not establish that a quality check was configured. */
export function hasQualityAssessment(
  rows: Pick<TrackBoardRow, 'quality'>[],
  modules?: { is_scenario: boolean }[],
): boolean {
  return rows.some((row) => row.quality.length > 0)
    || (modules?.some((module) => !module.is_scenario) ?? false)
}

/** An absent verdict must not turn into a green pass in a compact summary. */
export function qualityVerdict(cells: TrackQualityCell[]): 'passed' | 'failed' | 'unknown' {
  if (cells.some((cell) => cell.ok === false)) return 'failed'
  return cells.length && cells.every((cell) => cell.ok === true) ? 'passed' : 'unknown'
}

/** Throughput is integer-valued for reading; custom scores retain their precision. */
export function formatRankingValue(value: number | null | undefined, metric: string): string {
  if (value == null || !Number.isFinite(value)) return '—'
  if (metric.endsWith('tpm_card_norm')) return Math.round(value).toLocaleString('en-US')
  return Number(value.toPrecision(5)).toLocaleString('en-US', { maximumSignificantDigits: 5 })
}

export function filterBoardRows(rows: TrackBoardRow[], query: string): TrackBoardRow[] {
  const search = query.trim().toLowerCase()
  return search ? rows.filter((row) => [row.name, row.submitted_by, row.engine, row.image]
    .some((value) => value.toLowerCase().includes(search))) : rows
}
