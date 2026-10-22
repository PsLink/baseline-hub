/** Where a queued Baseline Hub submission stands in the machine queue.
 *
 *  `queue_ahead` comes from the worker's own ordering (oldest waiter first,
 *  one run per turn), so "#3 in line" is the order it will actually be served
 *  in, not a guess made on the page. */
type Translate = (key: string, params?: Record<string, string | number>) => string
type Queued = { queued_reason?: string; queue_ahead?: string[] }

export function inLineLabel(t: Translate, sub: Queued): string {
  const ahead = sub.queue_ahead?.length ?? 0
  return ahead ? t('baselineHub.inLine', { n: ahead + 1 }) : t('baselineHub.nextInLine')
}

export function queueTooltip(t: Translate, sub: Queued): string {
  const ahead = sub.queue_ahead ?? []
  return [
    sub.queued_reason,
    ahead.length ? t('baselineHub.aheadInLine', { who: ahead.join(', ') }) : '',
  ].filter(Boolean).join(' — ')
}
