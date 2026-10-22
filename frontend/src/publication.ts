export interface PublicationStatus {
  id: string
  status: string
  pr_url: string
  pr_number: number | null
  pr_state: string
  commit_sha: string
  error: string
}
export interface PublicationSummary extends PublicationStatus {
  repository: string
  title: string
  created_at: string
}
export interface Publication extends PublicationSummary {
  body: string
  files: { path: string; kind: string; diff: string }[]
  warnings?: string[]
  base_sha?: string
  base_branch?: string
}
export interface PublicationState {
  job: Publication | null
  error: string
  busy: boolean
  bodyKey: string
  reviewedKey: string
}
export const activePublication = (job: PublicationStatus | null) =>
  !!job && ['queued', 'forking', 'committing', 'opening_pr'].includes(job.status)
export const canSubmitPublication = (job: PublicationStatus | null) =>
  !!job && ['preview', 'failed', 'needs_authorization'].includes(job.status)

type Request = <T>(path: string, method?: string, body?: unknown) => Promise<T>

/** Keep the reviewed job on lost responses; only that immutable ID is submitted. */
export function createPublicationFlow(request: Request, state: PublicationState) {
  let sequence = 0
  const root = '/catalog-export'
  const message = (error: unknown) => error instanceof Error ? error.message : String(error)
  return {
    change(body: unknown) {
      const key = JSON.stringify(body)
      if (key === state.bodyKey) return
      sequence++
      state.bodyKey = key
      state.reviewedKey = ''
      state.job = null
      state.error = ''
      state.busy = false
    },
    async preview(body: unknown) {
      if (state.busy) return
      const current = ++sequence
      state.busy = true
      state.error = ''
      state.job = null
      state.reviewedKey = ''
      try {
        const job = await request<Publication>(`${root}/publication-previews`, 'POST', body)
        if (sequence === current) {
          state.job = job
          state.reviewedKey = state.bodyKey
        }
      } catch (error) {
        if (sequence === current) state.error = message(error)
      } finally {
        if (sequence === current) state.busy = false
      }
    },
    async submit() {
      if (state.busy || !canSubmitPublication(state.job) || state.reviewedKey !== state.bodyKey) return
      const id = state.job!.id
      const current = sequence
      state.busy = true
      state.error = ''
      try {
        const job = await request<Publication>(`${root}/publications/${id}/submit`, 'POST')
        if (sequence === current) state.job = job
      } catch (error) {
        if (sequence === current) state.error = message(error)
      } finally {
        if (sequence === current) state.busy = false
      }
    },
    async poll() {
      if (state.busy || !activePublication(state.job)) return
      const id = state.job!.id
      const current = sequence
      try {
        const status = await request<PublicationStatus>(`${root}/publications/${id}/status`)
        if (sequence === current && state.job?.id === id) {
          state.job = { ...state.job, ...status }
          state.error = ''
        }
      } catch (error) {
        if (sequence === current && state.job?.id === id) state.error = message(error)
      }
    },
  }
}
