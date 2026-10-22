/** The Baseline Hub's API shapes. The platform's own types come from
 *  @/plugins/api; what other plugins may use is re-exported by public.ts. */
/** One frozen config→performance record. Fully denormalized — it answers
 *  "what ran, on what, how fast" without touching any other table. */
export interface BaselineMeasurement {
  id: number
  submission_id: number
  campaign_id: number
  run_id: number
  name: string
  version: number
  served_model_name: string
  engine: string
  image: string
  engine_args: Record<string, unknown>
  precision: string
  gpu_type: string
  gpu_count: number
  machine_count: number
  machine_name: string
  benchmark_slug: string
  stage: string
  dataset_build_id: string
  target_metric: string
  metrics: Record<string, number | string>
  score: number | null
  objective_value: number | null
  feasible: boolean | null
  passed: boolean
  /** Per-module verdicts + the benchmark's OWN redline/display thresholds
   *  evaluated against what was measured — redlines live on the benchmark,
   *  not on the submission. */
  module_reports?: {
    /** The run's instance key — the prefix its metrics are filed under. A
     *  benchmark that runs one module twice reports `perf_guidellm_sweep`
     *  and `perf_guidellm_sweep#2`. */
    module: string
    /** The LLMBench module that ran (absent on rows frozen before it was kept). */
    module_name?: string
    /** The run's locked params as LLMBench reported them — input_tokens,
     *  output_tokens, the concurrency grid… what tells two runs of one
     *  module apart. Absent on rows frozen before it was kept. */
    params?: Record<string, string | number | boolean>
    status: string
    passed: boolean | null
    score: number | null
    error: string
    bounds: {
      metric: string
      role: string
      min: number | null
      max: number | null
      actual: number | null
      ok: boolean | null
    }[]
  }[]
  env_snapshot: Record<string, unknown>
  launch_command: string
  engine_version: string
  llmbench_submission_id: string
  measured_at: string | null
  track_id?: number | null
  /** The benchmark platform's own page for this submission ("" = no link). */
  llmbench_url: string
}

/** A submitted baseline config: the declared side. The engine config is ONE
 *  normalized snake_case dict — unknown flags are ordinary keys. */
export interface BaselineSubmission {
  id: number
  name: string
  is_baseline?: boolean
  served_model_name: string
  model_path: string
  engine: string
  image: string
  engine_args: Record<string, unknown>
  extra_env: Record<string, string>
  extra_volumes: Record<string, string>
  service_port: number
  precision: string
  gpu_type: string
  gpu_count: number
  machine_count: number
  share_machine: boolean
  benchmark_slug: string
  objective: Record<string, unknown>
  max_run_minutes: number
  status: 'queued' | 'running' | 'measured' | 'failed' | 'aborted'
  failure_class: string
  error: string
  campaign_id: number | null
  machine_name: string
  /** Why a queued submission has not started, in plain words — empty while a
   *  machine is ready and the next tick will place it. */
  queued_reason: string
  /** Who the machine queue serves before a queued submission, oldest first;
   *  its place in line is `queue_ahead.length + 1`. */
  queue_ahead: string[]
  supersedes_id: number | null
  version: number
  created_by_name: string
  notes: string
  created_at: string | null
  started_at: string | null
  finished_at: string | null
  cards: number
  campaign_status: string
  run_id: number | null
  run_status: string
  latest_measurement: BaselineMeasurement | null
  /** The track this entry competes in; null for a free-form submission. */
  track_id: number | null
  track_slug: string
  /** Detail endpoint only. */
  measurements?: BaselineMeasurement[]
}

/** One measured config inside its partition. Rows only rank within
 *  (model, gpu_type×count, benchmark, metric) — rank is null for a row that
 *  cannot rank (nothing scoreable, or redlines crossed). */
export interface BaselineLeaderboardRow {
  measurement_id: number
  submission_id: number
  name: string
  version: number
  served_model_name: string
  engine: string
  engine_version: string
  precision: string
  gpu_type: string
  gpu_count: number
  benchmark_slug: string
  target_metric: string
  value: number | null
  score: number | null
  feasible: boolean | null
  passed: boolean
  rank: number | null
  measured_at: string | null
}

/** The advisory flag catalog entry per engine: canonical key, kind
 *  (value|switch), accepted alias spellings, CLI form. */
export interface FlagSpec {
  key: string
  kind: string
  aliases: string[]
  flag: string
}

/** One verified engine image on a track. Exact reference; mirrors are
 *  separate entries. */
export interface TrackImage {
  engine: string
  image: string
  label?: string
}

/** The SLO every entry on a track is held to. */
export interface TrackGate {
  ttft_ms: number
  ttft_percentile: 'mean' | 'p50' | 'p90' | 'p99' | string
  min_request_output_tps: number
}

export interface TrackLeader {
  submission_id: number
  measurement_id: number
  name: string
  version: number
  value: number | null
  gpu_count: number
}

/** One leaderboard: a model on a card type, rules fixed by an admin, the
 *  benchmark behind it created on LLMBench by the platform (managed) or
 *  bound read-only (unmanaged). */
export interface TrackReference {
  measurement_id: number
  submission_id: number
  name: string
  version: number
  /** Percentage POINTS under the score (5 under 0.85 is 0.80), less an epsilon. */
  margin_points: number
  scores: Record<string, number>
  confirmed_by: string
  confirmed_at: string
}

export interface TrackReferenceFloor {
  key: string
  score: number | null
  previous_min: number | null
  floor: number
}

export interface TrackReferenceDrop {
  measurement_id: number
  submission_id: number
  name: string
  version: number
  reason: string
}

/** What confirming an entry as the baseline would do (dry run), or did. */
export interface TrackReferencePlan {
  measurement_id: number
  floors: TrackReferenceFloor[]
  drops: TrackReferenceDrop[]
  applied: string[]
  track: BaselineTrack | null
}

export interface BaselineTrack {
  id: number
  slug: string
  name: string
  status: 'draft' | 'open' | 'retired'
  model_name: string
  served_model_name: string
  model_path: string
  precision: string
  gpu_type: string
  pinned_machine_id: number | null
  pinned_machine_name: string
  images: TrackImage[]
  slo: TrackGate
  template: string
  benchmark_slug: string
  benchmark_id: number | null
  benchmark_config_hash: string
  /** Earlier hashes whose rows still compare with the current one — every
   *  threshold-only rewrite (a confirmed quality floor, a raised uptime bar)
   *  keeps the hash it replaced here instead of un-ranking its rows. */
  benchmark_config_hashes: string[]
  benchmark_locked: boolean
  benchmark_managed: boolean
  benchmark_error: string
  /** `{"<module>.<metric>": min}` confirmed from the reference baseline, and
   *  the confirmation itself. Empty until an admin confirms an entry. */
  quality_floors: Record<string, number>
  reference: TrackReference | Record<string, never>
  /** The external contest platform's leaderboard id. Set = every entry that
   *  PASSES the gate is forwarded there; null = the track forwards nothing.
   *  Held by the track's LLMBench benchmark, which is where forwarding is
   *  actually decided — `contest_error` reports a push that did not take. */
  contest_benchmark_id: number | null
  contest_error: string
  ranking_metric: string
  board_columns: TrackBoardColumn[]
  max_run_minutes: number
  cookbook_url: string
  notes: string
  created_by: number | null
  created_at: string | null
  opened_at: string | null
  retired_at: string | null
  gate_text: string
  entry_count: number
  measured_count: number
  leader: TrackLeader | null
}

/** One metric shown as a board column, as `<module>.<metric>`. */
export interface TrackBoardColumn {
  key: string
  label: string
}

/** What a row scored on a non-throughput column, with the gate it was held
 *  to. `ok` is the benchmark's own verdict, frozen at harvest. */
export interface TrackQualityCell {
  /** "module" = a suite's verdict (no value); "metric" = one named number. */
  kind: 'module' | 'metric'
  detail: string
  key: string
  label: string
  module: string
  value: number | null
  min: number | null
  max: number | null
  ok: boolean | null
}

/** One scenario (input + output length) of a measurement, as the board
 *  shows it: per-machine total TPM at 8 cards at the reported level. */
export interface TrackScenario {
  key: string
  /** A synthetic sweep, or a replay of recorded traffic at one concurrency
   *  (no token counts; its concurrency is the one it ran at). */
  kind: 'sweep' | 'replay'
  label: string
  input_tokens: number | null
  output_tokens: number | null
  tpm_card_norm: number | null
  output_tpm_card_norm: number | null
  /** Total minus output: the sustained input rate, not the sweep's own
   *  prefill-window mean (which counts overlapping requests twice). */
  input_tpm_card_norm: number | null
  concurrency: number | null
  request_output_tps: number | null
  ttft_ms: number | null
  meets_gate: boolean | null
  passed: boolean | null
}

/** One measurement on a track's board. rank is null when it cannot rank;
 *  unranked_reason says why. */
export interface TrackBoardRow {
  measurement_id: number
  submission_id: number
  is_baseline?: boolean
  /** The platform's run behind the measurement (0 for a row from before). */
  run_id: number
  name: string
  version: number
  engine: string
  engine_version: string
  image: string
  precision: string
  gpu_type: string
  gpu_count: number
  machine_name: string
  submitted_by: string
  /** Which benchmark the row was measured under, and which version of it —
   *  a benchmark edited in place keeps its slug. */
  benchmark_slug: string
  benchmark_config_hash: string
  /** Measured under an older version of the track's benchmark. */
  stale_benchmark: boolean
  /** The confirmed baseline the track's quality floors were taken from. */
  is_reference: boolean
  /** The whole config, carried on the row so the leader can be copied. */
  engine_args: Record<string, unknown>
  extra_env: Record<string, string>
  extra_volumes: Record<string, string>
  launch_command: string
  value: number | null
  score: number | null
  passed: boolean
  feasible: boolean | null
  rank: number | null
  unranked_reason: string
  scenarios: TrackScenario[]
  quality: TrackQualityCell[]
  measured_at: string | null
}

/** The two entries a report and a catalog entry compare. */
export interface TrackPair {
  baseline_run_id: number | null
  optimized_run_id: number | null
  baseline_why: string
  optimized_why: string
}

/** Whether a machine holds the track's weights (`state`: present, absent,
 *  unknown or checking), node by node on a k8s machine. */
export interface MachineWeights {
  machine_id: number
  machine: string
  state: 'present' | 'absent' | 'unknown' | 'checking'
  nodes: { node: string; present: boolean | null; detail: string }[]
  checked_at: string | null
}

export interface BaselineTrackDetail extends BaselineTrack {
  board: TrackBoardRow[]
  entries: BaselineSubmission[]
  pair: TrackPair
  weights: MachineWeights[]
}

/** The benchmark as LLMBench holds it now, next to what the track recorded. */
export interface BenchmarkMetric {
  key: string
  role: string
  min_val: number | null
  max_val: number | null
}

/** One module run of the live benchmark, keyed as its metrics are. */
export interface BenchmarkModule {
  key: string
  module_name: string
  order_index: number
  weight: number
  is_scenario: boolean
  /** "50k + 1.5k", "replay"; "" when not a scenario. */
  label: string
  input_tokens: number | null
  output_tokens: number | null
  metrics: BenchmarkMetric[]
}

export interface TrackBenchmarkLive {
  live: { id: number; config_hash: string; is_locked: boolean; status: string } | null
  recorded_config_hash: string
  drifted: boolean
  url: string
  modules: BenchmarkModule[]
}

export interface BenchmarkYaml {
  slug: string
  benchmark_id: number
  config_hash: string
  yaml: string
}

export interface ParamsRolloutTrack {
  slug: string
  ok: boolean
  applied: string[]
  benchmark_config_hash: string
  benchmark_config_hashes: string[]
  error: string
}

export interface ParamsRollout {
  params: Record<string, unknown>
  dry_run: boolean
  tracks: ParamsRolloutTrack[]
  /** Where a real roll stopped; the tracks after it were not touched. */
  stopped_at: string
}

export interface TrackTemplate {
  name: string
  title: string
  description: string
  modules: {
    module_name: string
    order_index: number
    weight: number
    is_scenario: boolean
    label: string
    input_tokens: number | null
    output_tokens: number | null
    search_mode: string | null
    takes_gate: boolean
  }[]
  default: boolean
}
