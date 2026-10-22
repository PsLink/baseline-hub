import { reactive } from 'vue'
import type { AxiosInstance } from 'axios'
import type { AgentReport } from '../../platform/api/client.ts'
import type { Job } from './types.ts'

export interface ComparisonPair {
  trackSlug: string
  baselineRunId: number
  optimizedRunId: number
}

/** Own one dialog's exact comparison, including reusable jobs and stale-request guards. */
export function createComparisonReportFlow(
  api: Pick<AxiosInstance, 'get' | 'post'>,
  options: {
    text?: (zh: string, en: string) => string
    wait?: () => Promise<void>
  } = {},
) {
  const text = options.text ?? ((_zh: string, en: string) => en)
  const wait = options.wait ?? (() => new Promise<void>(resolve => setTimeout(resolve, 2000)))
  const state = reactive({ reportId: null as number | null, status: '', failed: false })
  const jobs = new Map<string, Job>()
  const starts = new Map<string, Promise<Job>>()
  let pair: ComparisonPair | null = null
  let revision = 0
  let active: { revision: number; promise: Promise<number | null> } | null = null

  function cancel() {
    revision++
    active = null
  }

  function select(next: ComparisonPair | null) {
    cancel()
    pair = next ? { ...next } : null
    state.reportId = null
    state.status = ''
    state.failed = false
  }

  async function run(selected: ComparisonPair, current: number, retry: boolean) {
    const fresh = () => current === revision
    const { baselineRunId: baseline, optimizedRunId: opt } = selected
    const key = `${selected.trackSlug}:${baseline}:${opt}`
    let job = jobs.get(key)
    state.failed = false
    state.status = text('正在检查已有报告…', 'Checking existing reports…')
    try {
      if (job?.status === 'failed') {
        // Another page may have retried this task since our last failure.
        job = (await api.get<Job>(`/autoreport/jobs/${job.id}`)).data
        if (!fresh()) return null
        jobs.set(key, job)
      }
      if (!job || job.status === 'failed') {
        const reports = (await api.get<AgentReport[]>('/agent/v1/reports')).data
        if (!fresh()) return null
        const matches = reports.filter(report => report.baseline_run_id === baseline
          && report.attempt_run_ids.length === 1 && report.attempt_run_ids[0] === opt)
        const existing = matches.find(report => report.lang === 'zh') ?? matches[0]
        if (existing) {
          state.reportId = existing.id
          state.status = text('已复用对应报告，发布时将附带 HTML。', 'Existing report reused; HTML will be included in the publication.')
          return existing.id
        }
        const pending = (await api.get<Job[]>('/autoreport/jobs')).data
        if (!fresh()) return null
        job = pending.find(candidate => candidate.baseline_run_id === baseline
          && candidate.attempt_run_ids.length === 1 && candidate.attempt_run_ids[0] === opt
          && (candidate.status === 'queued' || candidate.status === 'running')) ?? job
        if (!job) {
          let started = starts.get(key)
          if (!started) {
            started = api.post<Job>('/autoreport/jobs', {
              baseline_run_id: baseline, attempt_run_ids: [opt], langs: ['en', 'zh'],
            }).then(response => response.data)
            starts.set(key, started)
          }
          try {
            job = await started
          } finally {
            starts.delete(key)
          }
        }
        // Keep a received job even if its dialog closed while POST was pending:
        // returning to the same pair must not create a duplicate job.
        jobs.set(key, job)
      }
      if (!fresh()) return null
      if (retry && job.status === 'failed') {
        job = (await api.post<Job>(`/autoreport/jobs/${job.id}/retry`)).data
        jobs.set(key, job)
        if (!fresh()) return null
      }
      state.status = text('正在等待对应报告完成…', 'Waiting for the comparison report…')
      while (fresh()) {
        if (job.status === 'failed') {
          state.failed = true
          throw new Error(job.error || text('报告生成失败，请重试', 'Report failed; retry available'))
        }
        if (job.status === 'done') {
          const id = job.report_ids.zh ?? Object.values(job.report_ids)[0]
          if (!id) throw new Error(text('报告任务完成但未返回报告', 'Completed job has no report'))
          state.reportId = id
          state.status = text('报告已生成，发布时将附带 HTML。', 'Report ready; HTML will be included in the publication.')
          return id
        }
        await wait()
        if (!fresh()) return null
        job = (await api.get<Job>(`/autoreport/jobs/${job.id}`)).data
        if (!fresh()) return null
        jobs.set(key, job)
      }
      return null
    } catch (error) {
      if (!fresh()) return null
      throw error
    }
  }

  function ensure({ retry = false } = {}): Promise<number | null> {
    if (!pair) return Promise.resolve(null)
    if (active?.revision === revision) return active.promise
    if (state.reportId && !retry) return Promise.resolve(state.reportId)
    const current = revision
    const promise = run(pair, current, retry).finally(() => {
      if (active?.revision === current) active = null
    })
    active = { revision: current, promise }
    return promise
  }

  return { state, select, ensure, cancel }
}
