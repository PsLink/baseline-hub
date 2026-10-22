/** The autoreport plugin's API shapes (autotune_autoreport/schemas.py). */
export interface Job {
  id: number
  status: 'queued' | 'running' | 'done' | 'failed'
  source: { kind: 'track' | 'runs'; slug?: string }
  baseline_run_id: number | null
  attempt_run_ids: number[]
  langs: string[]
  step: string
  error: string
  report_ids: Record<string, number>
  llm: { base_url?: string; model?: string }
  usage: Record<string, number>
  shape: { scenarios?: { key: string; kind: string }[]; quality?: string[]; unsupported?: string[] }
  created_by_name: string
  created_at: string | null
  started_at: string | null
  finished_at: string | null
}

export interface LogEntry {
  at: string
  step: string
  lang?: string
  round?: number
  seconds?: number
  messages?: { role: string; content: string }[]
  text?: string
  outline?: string
  facts?: unknown
  problems?: string[]
  why?: string
  [key: string]: unknown
}

export interface JobDetail extends Job {
  labels: string[]
  log: LogEntry[]
}

export interface Preview {
  baseline_run_id: number
  attempt_run_ids: number[]
  why: string
  shape: { unsupported?: string[]; scenarios?: { key: string; kind: string }[] }
  languages: { lang: string; title: string; skeleton: string; messages: { role: string; content: string }[] }[]
}

export interface LLMSettings {
  base_url: string
  model: string
  has_api_key: boolean
  temperature: number | null
  max_tokens: number
  json_mode: boolean
  timeout_seconds: number
  fix_rounds: number
  extra_body: Record<string, unknown>
  env_base_url: string
  env_model: string
  env_has_api_key: boolean
}

export interface TrackRow {
  slug: string
  name: string
  run_count: number
  measured_count: number
}
