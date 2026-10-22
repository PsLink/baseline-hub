/** Display labels only: API identifiers and raw metric keys remain unchanged. */
const statuses: Record<string, [string, string]> = {
  open: ['开放中', 'Open'], draft: ['准备中', 'Draft'], retired: ['已退役', 'Retired'],
  queued: ['排队中', 'Queued'], running: ['运行中', 'Running'], measured: ['已测量', 'Measured'],
  failed: ['失败', 'Failed'], aborted: ['已取消', 'Cancelled'], passed: ['通过', 'Passed'],
  not_passed: ['未通过', 'Not passed'], pending: ['等待中', 'Pending'], succeeded: ['已完成', 'Completed'],
  completed: ['已完成', 'Completed'], cancelled: ['已取消', 'Cancelled'],
}
export function statusLabel(status: string, locale: string): string {
  return statuses[status]?.[locale === 'zh' ? 0 : 1] ?? status
}
const metrics: Record<string, [string, string]> = {
  total_tpm_card_norm: ['总吞吐量（TPM@8）', 'Total throughput (TPM@8)'],
  output_tpm_card_norm: ['输出吞吐量（TPM@8）', 'Output throughput (TPM@8)'],
  ttft_ms: ['首 token 延迟（ms）', 'Time to first token (ms)'],
  request_output_tps: ['单请求输出速度（tok/s）', 'Per-request output (tok/s)'],
  score: ['评测分数', 'Benchmark score'], score_total: ['评测总分', 'Total score'],
}
const compactMetrics: Record<string, [string, string]> = {
  total_tpm_card_norm: ['总 TPM@8', 'Total TPM@8'],
  output_tpm_card_norm: ['输出 TPM@8', 'Output TPM@8'],
  ttft_ms: ['TTFT（ms）', 'TTFT (ms)'],
  request_output_tps: ['单请求 tok/s', 'Output tok/s/request'],
  score: ['分数', 'Score'], score_total: ['总分', 'Total score'],
}
export function metricLabel(key: string, locale: string, compact = false): string {
  const labels = compact ? compactMetrics : metrics
  return labels[key.split('.').at(-1) ?? '']?.[locale === 'zh' ? 0 : 1] ?? (key || labels.score[locale === 'zh' ? 0 : 1])
}
