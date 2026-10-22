<script setup lang="ts">
/** One submission: the declared config, the live run, and the frozen record.
 *
 *  The measurement block shows the whole metrics dict, not a fixed column
 *  set — benchmarks vary their modules, and trimming to the target metric
 *  would hide exactly the numbers a reader came to compare. One module gets
 *  a reading of its own: a guidellm concurrency sweep is a table of scenario
 *  × concurrency (see `sweepOf`), and dumped flat it is two hundred rows
 *  nobody can read a deployment decision off.
 */
import { rememberedPage } from '@/platform/composables/pageState'
import { statusLabel, metricLabel } from '@/platform/i18n/display'
import { inLineLabel } from '../utils/queue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api, useAuthStore, ownsSubmission } from '@/plugins/api'
import { type BaselineMeasurement, type BaselineSubmission, type BaselineTrack, type FlagSpec } from '../types'
import { BackLink } from '@/plugins/api'
import BaselineSubmitDialog from '../components/BaselineSubmitDialog.vue'
import { CampaignRuns } from '@/plugins/api'
import { FlagChips } from '@/plugins/api'
import { CatalogExportDialog, type CatalogCandidate } from '@/plugins/api'
import { useI18n } from '@/plugins/api'
import { apiErrorText } from '@/plugins/api'
import { usePoll } from '@/plugins/api'
import { PageLoading, useFirstLoad } from '@/plugins/api'
import { exactTime, relativeTime } from '@/plugins/api'

const route = useRoute()
const router = useRouter()
const { t, locale } = useI18n()
const text = (zh: string, en: string) => locale.value === 'zh' ? zh : en
const auth = useAuthStore()
const isAdmin = computed(() => auth.user?.role === 'admin')
/** Its place in the machine queue and who is ahead, for a queued entry. */
const queueLine = computed(() => {
  if (sub.value?.status !== 'queued') return ''
  const ahead = sub.value?.queue_ahead ?? []
  return [inLineLabel(t, sub.value), ahead.length
    ? t('baselineHub.aheadInLine', { who: ahead.join(', ') }) : ''].filter(Boolean).join(' · ')
})
const sub = ref<BaselineSubmission | null>(null)
const measurements = computed(() => [...(sub.value?.measurements ?? [])].sort((a, b) => {
  const time = (value: string | null) => value ? Date.parse(value) || 0 : 0
  return time(b.measured_at) - time(a.measured_at) || b.id - a.id
}))
/** The track this entry belongs to — needed to evolve it here, since the
 *  dialog holds an entrant to the track's images and fixed fields. */
const track = ref<BaselineTrack | null>(null)
const gpuTypes = ref<string[]>([])
const evolveOpen = ref(false)
const canEvolve = computed(
  () => !!sub.value && ownsSubmission(sub.value) && !!sub.value.track_slug && track.value?.status === 'open',
)
/** Advisory flag catalog per engine ({key, kind, aliases, flag} specs) —
 *  flattened to every accepted spelling for the chips' unknown badges. */
const flagCatalog = ref<Record<string, FlagSpec[]>>({})
const knownNames = computed(() =>
  (flagCatalog.value[sub.value?.engine ?? ''] ?? []).flatMap((s) => [s.key, ...s.aliases]),
)

async function load() {
  sub.value = (await api.get(`/baseline-hub/submissions/${route.params.id}`)).data
  const slug = sub.value?.track_slug
  if (slug && track.value?.slug !== slug) {
    try {
      track.value = (await api.get(`/baseline-hub/tracks/${slug}`)).data
    } catch {
      track.value = null
    }
  }
}

const live = computed(() => ['queued', 'running'].includes(sub.value?.status ?? ''))

/** Why a queued submission has not started, or "" when nothing needs saying.
 *  The worker fills this from the same gates it places runs with, so a stall
 *  that no tick will ever clear — a machine leased but never handed over —
 *  reads as a fixable sentence instead of an endless spinner. */
const queueNote = computed(() =>
  sub.value?.status === 'queued' ? (sub.value?.queued_reason ?? '') : '',
)

function statusType(status: string): 'success' | 'warning' | 'danger' | 'info' | 'primary' {
  return (
    {
      queued: 'info',
      running: 'primary',
      measured: 'success',
      failed: 'danger',
      aborted: 'warning',
    } as const
  )[status] ?? 'info'
}

function fmtMetric(value: number | string | null | undefined): string {
  if (value == null) return '—'
  if (typeof value !== 'number') return String(value)
  return Math.abs(value) >= 100 ? value.toFixed(1) : value.toPrecision(5)
}

/** What this measurement ranks by: the objective when it scored, else the
 *  benchmark's own overall score. */
function heroValue(m: BaselineMeasurement): number | null {
  return m.objective_value ?? m.score
}

/** Redlines are the BENCHMARK's, not ours: LLMBench defines them per module
 *  (metric_configs with min/max bounds), and the measurement froze each
 *  module's verdict and thresholds at harvest. A "redline" gates the run; a
 *  "display" bound is shown on the platform but does not fail it. */
interface BoundRow {
  metric: string
  role: string
  min: number | null
  max: number | null
  actual: number | null
  ok: boolean | null
}
interface ModuleReport {
  module: string
  module_name?: string
  params?: Record<string, string | number | boolean>
  status: string
  passed: boolean | null
  score: number | null
  error: string
  bounds: BoundRow[]
}

function reports(m: BaselineMeasurement): ModuleReport[] {
  return (m.module_reports ?? []) as ModuleReport[]
}

function boundText(b: BoundRow): string {
  if (b.min != null && b.max != null) return `${b.min} ≤ ${metricLabel(b.metric, locale.value)} ≤ ${b.max}`
  if (b.max != null) return `${metricLabel(b.metric, locale.value)} ≤ ${b.max}`
  return `${metricLabel(b.metric, locale.value)} ≥ ${b.min}`
}

function boundLook(b: BoundRow): { type: 'success' | 'danger' | 'info'; label: string } {
  if (b.ok === true) return { type: 'success', label: t('baselineHub.detail.held') }
  if (b.ok === false) return { type: 'danger', label: t('baselineHub.detail.crossed') }
  return { type: 'info', label: text('未测量', 'Not measured') }
}

/** Metrics grouped by module (`<module>.<metric>`) — a benchmark can run
 *  several modules, and one flat table hides which numbers belong together.
 *  The target metric's module leads; keys without a module land in
 *  "(overall)" (the benchmark's own score_total & co). */
function metricGroups(m: BaselineMeasurement): { module: string; score: number | null; rows: [string, number | string][] }[] {
  const groups = new Map<string, [string, number | string][]>()
  for (const [key, value] of Object.entries(m.metrics)) {
    const module = key.includes('.') ? key.split('.')[0] : '(overall)'
    if (!groups.has(module)) groups.set(module, [])
    groups.get(module)!.push([key, value])
  }
  const targetModule = m.target_metric.includes('.') ? m.target_metric.split('.')[0] : ''
  const ordered = [...groups.keys()].sort(
    (a, b) => Number(b === targetModule) - Number(a === targetModule) || a.localeCompare(b),
  )
  return ordered.map((module) => {
    const rows = groups.get(module)!
    rows.sort(([a], [b]) =>
      a === m.target_metric ? -1 : b === m.target_metric ? 1 : a.localeCompare(b),
    )
    const score = rows.find(([k]) => k === `${module}.score`)?.[1]
    return { module, score: typeof score === 'number' ? score : null, rows }
  })
}

/* ---- GuideLLM sweep: the special handler --------------------------------
 *
 * A guidellm sweep reports one metric group per concurrency level
 * (`<module>.c<N>.<metric>`), and sweep-test runs the module twice with
 * different prompt/answer lengths — filed as `perf_guidellm_sweep` and
 * `perf_guidellm_sweep#2`, each with its params frozen on the module report.
 * Read the way an ops throughput table is read, that is ONE table: scenario
 * (input + output length) × concurrency, with the columns that decide a
 * deployment — aggregate input/output tok/s, what a single request sees,
 * and the per-machine TPM normalized to 8 cards. Every other module keeps
 * the plain grouped table in "All metrics".
 */
const CARD_NORM_BASELINE = 8
const LEVEL_KEY = /^([^.]+)\.c(\d+)\.output_tps_mean$/

interface SweepRow {
  scenarioKey: string
  scenario: string
  /** Rows this scenario cell spans; 0 on the rows folded into the first. */
  span: number
  concurrency: number
  inputTps: number | null
  outputTps: number | null
  requestOutputTps: number | null
  /** Per-machine total tokens/min as if the instance held 8 cards. */
  tpm: number | null
  ttftP50: number | null
  ttftP99: number | null
  meetsSlo: boolean | null
  /** An auto-search probe: a short level run to find the knee, not the
   *  full-length confirmation. Its numbers are real but noisier. */
  probe: boolean
  /** The level the benchmark promoted to its top-level (scored) metrics. */
  reported: boolean
}
interface Sweep {
  title: string
  rows: SweepRow[]
  /** Where the 8-card factor came from — the benchmark's own numbers when
   *  it reported a normalized pair, else the cards this run held. */
  scaleSource: 'benchmark' | 'cards'
}

function num(metrics: Record<string, number | string>, key: string): number | null {
  const v = metrics[key]
  return typeof v === 'number' && Number.isFinite(v) ? v : null
}

/** 50000 → "50k", 1500 → "1.5k", 300 → "300". */
function fmtTokens(n: number): string {
  if (n < 1000) return String(n)
  const k = n / 1000
  return `${Number.isInteger(k) ? k : k.toFixed(1).replace(/\.0$/, '')}k`
}

/** The scenario a module report ran: "50k + 1.5k" — the frozen input and
 *  output lengths. Empty when the row predates params being kept. */
function scenarioOf(report: ModuleReport | undefined): string {
  const p = report?.params
  const inp = typeof p?.input_tokens === 'number' ? p.input_tokens : null
  const out = typeof p?.output_tokens === 'number' ? p.output_tokens : null
  return inp != null && out != null ? `${fmtTokens(inp)} + ${fmtTokens(out)}` : ''
}

/** The factor turning this module's tok/s into per-machine TPM at 8 cards.
 *  Recovered from a pair the benchmark actually returned (it normalized
 *  the reported level for us; the same factor applies to every level),
 *  else derived from the cards this run held — LLMBench assumes 8 when no
 *  hardware is given, and so do we. */
function tpmScale(m: BaselineMeasurement, key: string): { scale: number; source: Sweep['scaleSource'] } {
  for (const base of ['total_tps_mean', 'output_tps', 'input_tps']) {
    const raw = num(m.metrics, `${key}.${base}`)
    const norm = num(m.metrics, `${key}.${base.replace(/_tps(_mean)?$/, '_tpm_card_norm')}`)
    if (raw && norm != null) return { scale: norm / raw, source: 'benchmark' }
  }
  const cards = Math.max(1, (m.gpu_count || CARD_NORM_BASELINE) * Math.max(1, m.machine_count || 1))
  return { scale: (60 * CARD_NORM_BASELINE) / cards, source: 'cards' }
}

function sweepOf(m: BaselineMeasurement): Sweep {
  // Which module instances swept, and at which levels — from the metrics
  // themselves, so a row frozen before module reports existed still reads.
  const levels = new Map<string, number[]>()
  for (const key of Object.keys(m.metrics)) {
    const hit = LEVEL_KEY.exec(key)
    if (!hit) continue
    if (!levels.has(hit[1])) levels.set(hit[1], [])
    levels.get(hit[1])!.push(Number(hit[2]))
  }
  const byModule = new Map(reports(m).map((r) => [r.module, r]))
  // Benchmark order when reports exist (the order sweep-test lists its
  // scenarios), else the instance keys' own order.
  const keys = [...levels.keys()].sort(
    (a, b) => (reports(m).findIndex((r) => r.module === a) - reports(m).findIndex((r) => r.module === b)) || a.localeCompare(b),
  )
  const rows: SweepRow[] = []
  let source: Sweep['scaleSource'] = 'cards'
  const names = new Set<string>()
  for (const key of keys) {
    const report = byModule.get(key)
    names.add(report?.module_name || key.replace(/#\d+$/, ''))
    const { scale, source: src } = tpmScale(m, key)
    if (src === 'benchmark') source = 'benchmark'
    const reported = num(m.metrics, `${key}.reported_concurrency`)
    const maxSeconds = typeof report?.params?.max_seconds === 'number' ? report.params.max_seconds : null
    const scenario = scenarioOf(report) || (keys.length > 1 ? key : 'sweep')
    const sorted = [...new Set(levels.get(key)!)].sort((a, b) => a - b)
    sorted.forEach((c, i) => {
      const at = (metric: string) => num(m.metrics, `${key}.c${c}.${metric}`)
      const inputTps = at('input_tps_mean')
      const outputTps = at('output_tps_mean')
      const total = at('total_tps_mean') ?? (inputTps != null && outputTps != null ? inputTps + outputTps : null)
      const meets = at('meets_slo')
      const cap = at('duration_cap_seconds')
      rows.push({
        scenarioKey: key,
        scenario,
        span: i === 0 ? sorted.length : 0,
        concurrency: c,
        inputTps,
        outputTps,
        requestOutputTps: at('request_output_tps') ?? (outputTps != null ? outputTps / c : null),
        tpm: total != null ? total * scale : null,
        ttftP50: at('ttft_p50_ms'),
        ttftP99: at('ttft_p99_ms'),
        meetsSlo: meets == null ? null : meets >= 1,
        probe: report?.params?.search_mode === 'auto' && cap != null && maxSeconds != null && cap !== maxSeconds,
        reported: reported === c,
      })
    })
  }
  const title = [...names].map((n) => (n === 'perf_guidellm_sweep' ? 'GuideLLM sweep' : n)).join(' + ')
  return { title, rows, scaleSource: source }
}

/** One sweep per measurement, built once — the table, its span rule and its
 *  row classes all read it. */
const sweeps = computed(
  () => new Map((sub.value?.measurements ?? []).map((m) => [m.id, sweepOf(m)])),
)

/* ---- Quality suites: the other half of a measurement ---------------------
 *
 * A benchmark that gates on correctness (opencompass and friends) reports its
 * scores in the same flat metrics dict as everything else, where they sit
 * among two hundred keys. The board only carries the verdict — pass or fail —
 * so this is where the numbers have to be readable: one row per task, the
 * gate it was held to, and how many samples it was taken over.
 */
interface QualityRow {
  task: string
  value: number | null
  gate: string
  ok: boolean | null
  samples: number | null
  correct: number | null
}
interface QualityPanel {
  module: string
  passed: boolean | null
  error: string
  elapsed: number | null
  genTokens: number | null
  outputTps: number | null
  rows: QualityRow[]
}

function qualityPanels(m: BaselineMeasurement): QualityPanel[] {
  const panels: QualityPanel[] = []
  for (const report of reports(m)) {
    const params = report.params ?? {}
    if ('input_tokens' in params && 'output_tokens' in params) continue  // a sweep
    const key = report.module
    const num = (name: string) => {
      const value = m.metrics[`${key}.${name}`]
      return typeof value === 'number' ? value : null
    }
    // Tasks come from what the module actually scored, plus anything gated.
    const tasks = new Set<string>()
    const prefix = `${key}.scores.`
    for (const name of Object.keys(m.metrics))
      if (name.startsWith(prefix)) tasks.add(name.slice(prefix.length))
    for (const bound of report.bounds) tasks.add(bound.metric)
    const rows: QualityRow[] = [...tasks].sort().map((task) => {
      const bound = report.bounds.find((b) => b.metric === task)
      const value = num(`scores.${task}`) ?? num(task) ?? (bound?.actual ?? null)
      const gate = bound
        ? bound.min != null ? `≥ ${bound.min}` : bound.max != null ? `≤ ${bound.max}` : ''
        : ''
      return {
        task,
        value,
        gate,
        ok: bound?.ok ?? null,
        samples: num(`details.${task}.total_samples`),
        correct: num(`details.${task}.correct_samples`),
      }
    })
    if (!rows.length) continue
    panels.push({
      module: key,
      passed: report.passed,
      error: report.error,
      elapsed: num('elapsed_s'),
      genTokens: num('gen_tokens'),
      outputTps: num('output_tps'),
      rows,
    })
  }
  return panels
}

function fmtScore(value: number | null): string {
  if (value == null) return '—'
  return Math.abs(value) <= 1 ? value.toFixed(3) : value.toFixed(1)
}
function fmtMinutes(seconds: number | null): string {
  if (seconds == null) return '—'
  return seconds >= 90 ? `${(seconds / 60).toFixed(0)} min` : `${seconds.toFixed(0)} s`
}

/** Merge each scenario's cell down its concurrency rows, as the ops table does. */
function sweepSpan({ row, columnIndex }: { row: SweepRow; columnIndex: number }) {
  if (columnIndex !== 0) return undefined
  return row.span ? { rowspan: row.span, colspan: 1 } : { rowspan: 0, colspan: 0 }
}

function sweepRowClass({ row }: { row: SweepRow }): string {
  return [row.reported ? 'sweep-reported' : '', row.probe ? 'sweep-probe' : ''].join(' ').trim()
}

function fmtInt(value: number | null): string {
  return value == null ? '—' : Math.round(value).toLocaleString('en-US')
}
function fmt1(value: number | null): string {
  return value == null ? '—' : value.toFixed(1)
}
function fmtSec(ms: number | null): string {
  return ms == null ? '—' : `${(ms / 1000).toFixed(1)} s`
}

async function abort() {
  if (!sub.value) return
  try {
    await ElMessageBox.confirm(
      t('baselineHub.msg.abortRunning', { name: sub.value?.name ?? '' }),
      t('baselineHub.msg.abortTitle'),
      {
        confirmButtonText: t('baselineHub.msg.abortConfirm'),
        cancelButtonText: t('baselineHub.msg.keep'),
        type: 'warning',
      },
    )
  } catch {
    return
  }
  try {
    await api.post(`/baseline-hub/submissions/${sub.value.id}/abort`)
    await load()
  } catch (error: any) {
    ElMessage.error(apiErrorText(error, t('baselineHub.msg.abortFailed')))
  }
}

async function setReference() {
  if (!sub.value) return
  try {
    await ElMessageBox.confirm(
      `Make this the reference config for ${sub.value.served_model_name} / ` +
        `${sub.value.engine} / ${sub.value.gpu_type || '(any card)'}? Campaigns tuning ` +
        'that combination will compare their candidates against it.',
      'Set as reference',
      { confirmButtonText: 'Set reference', cancelButtonText: 'Cancel' },
    )
  } catch {
    return
  }
  try {
    await api.post(`/baseline-hub/submissions/${sub.value.id}/set-reference`)
    ElMessage.success('This config is now the campaign reference')
  } catch (error: any) {
    ElMessage.error(apiErrorText(error, 'Could not set the reference'))
  }
}

/** Publishing this entry's measured config to the model catalog, by the
 *  platform's run behind its latest measurement. */
const catalogOpen = ref(false)
const catalogCandidates = computed<CatalogCandidate[]>(() => {
  const m = sub.value?.latest_measurement
  if (!m?.run_id || !sub.value || !ownsSubmission(sub.value)) return []
  return [{ run_id: m.run_id, label: `${sub.value.name} v${sub.value.version}` }]
})

const first = useFirstLoad(load)
const poll = usePoll(() => (live.value ? load() : undefined), 10000)
onMounted(async () => {
  // The page's own data first; the lists only its dialogs use come alongside.
  api.get('/machines/gpu-types')
    .then(({ data }) => (gpuTypes.value = data.gpu_types))
    .catch(() => (gpuTypes.value = []))
  api.get('/baseline-hub/flag-catalog')
    .then(({ data }) => (flagCatalog.value = data.engines))
    .catch(() => (flagCatalog.value = {}))
  await first.run()
  poll.start()
})
</script>

<template>
  <div v-if="sub" class="page">
    <BackLink :to="rememberedPage(sub.track_slug ? `/baseline-hub/tracks/${sub.track_slug}` : '/baseline-hub')"
      :label="sub.track_slug ? t('baselineHub.backToTrack') : t('baselineHub.back')" />
    <div class="header-row">
      <div>
        <h1 class="page-title">
          {{ sub.name }}
          <el-tag v-if="sub.version > 1" effect="plain" class="version">v{{ sub.version }}</el-tag>
          <el-tag :type="statusType(sub.status)" class="version">{{ statusLabel(sub.status, locale) }}</el-tag>
        </h1>
        <span class="muted">
          {{ sub.served_model_name }} · {{ sub.engine }} ·
          {{ sub.gpu_count }}×{{ sub.gpu_type || 'any' }}
          <template v-if="sub.precision"> · {{ sub.precision }}</template>
          · {{ text('提交于', 'Submitted') }} {{ relativeTime(sub.created_at) }} · {{ sub.created_by_name }}
          <template v-if="sub.track_slug">
            · {{ text('赛道', 'Track') }} <router-link class="link nav-link" :to="`/baseline-hub/tracks/${sub.track_slug}`">
              {{ sub.track_slug }}</router-link>
          </template>
        </span>
      </div>
      <div class="actions">
        <el-button v-if="live && ownsSubmission(sub)" type="danger" plain @click="abort">
          {{ t('baselineHub.abort') }}</el-button>
        <el-button v-if="canEvolve" type="primary" @click="evolveOpen = true">
          {{ t('baselineHub.evolve') }}</el-button>
        <el-button v-if="isAdmin && sub.status === 'measured'" type="primary" plain @click="setReference">
          {{ t('baselineHub.setReference') }}
        </el-button>
        <el-button v-if="catalogCandidates.length" plain @click="catalogOpen = true">
          {{ t('catalogExport.menu') }}
        </el-button>
      </div>
    </div>

    <CatalogExportDialog :key="sub.id" v-model="catalogOpen" :candidates="catalogCandidates"
      :initial="catalogCandidates.map((c) => ({ run_id: c.run_id, role: 'optimized' }))"
      :model-name="sub.served_model_name" />

    <div v-if="sub.supersedes_id" class="lineage muted">
      {{ t('baselineHub.detail.evolves') }}
      <router-link class="link nav-link" :to="`/baseline-hub/${sub.supersedes_id}`">
        submission #{{ sub.supersedes_id }}</router-link>
    </div>

    <el-alert v-if="sub.status === 'failed'" type="error" :closable="false" class="block">
      <b>{{ sub.failure_class || 'failed' }}</b> — {{ sub.error || text('未记录详细错误', 'No error details recorded') }}
    </el-alert>

    <el-alert v-if="queueNote" type="warning" :closable="false" show-icon class="queue-note">
      <template #title>{{ t('baselineHub.detail.queuedTitle') }}</template>
      <span class="mono">{{ queueNote }}</span>
      <div class="muted">{{ t('baselineHub.detail.queuedHint') }}</div>
    </el-alert>

    <p v-if="live && !(sub.measurements ?? []).length" class="muted empty">
      {{ sub.status === 'queued'
        ? text('正在等待符合硬件要求的空闲机器…', 'Waiting for an available machine matching the hardware requirements…')
        : text('正在运行，评测结束后将在此显示结果。', 'Running. Results will appear here when the benchmark finishes.') }}
      <span v-if="queueLine" class="queue-line">{{ queueLine }}</span>
    </p>


    <details v-for="(m, index) in measurements" :key="m.id" :open="index === 0" class="measurement block">
      <summary>
        <div class="measure-head">
          <b>{{ t('baselineHub.detail.measurement') }}</b>
          <el-tag v-if="m.passed === false" type="danger" size="small">
            {{ t('baselineHub.detail.notPassed') }}</el-tag>
          <el-tag v-else-if="m.passed" type="success" size="small" effect="plain">
            {{ t('baselineHub.detail.passed') }}</el-tag>
          <span class="muted tiny" :title="exactTime(m.measured_at)">
            {{ relativeTime(m.measured_at) }} · {{ m.machine_name }}
            <template v-if="m.engine_version"> · {{ m.engine }} {{ m.engine_version }}</template>
            <template v-if="m.dataset_build_id"> · {{ text('数据集', 'Dataset') }} {{ m.dataset_build_id }}</template>
          </span>
          <span class="spacer" />
          <a v-if="m.llmbench_url" :href="m.llmbench_url" target="_blank"
            rel="noopener" class="link tiny">
            LLMBench submission #{{ m.llmbench_submission_id }} ↗</a>
        </div>
      </summary>
      <el-card shadow="never">

      <!-- The verdict, readable without opening anything: the ranking number,
           and the benchmark's OWN redlines against what was actually measured
           — LLMBench defines the gates, the measurement froze its verdicts. -->
      <div class="score-row">
        <div class="hero">
          <div class="hero-num mono">{{ fmtMetric(heroValue(m)) }}</div>
          <div class="hero-label" :title="m.target_metric">{{ metricLabel(m.target_metric, locale) }}</div>
          <div v-if="m.target_metric && m.score != null" class="muted tiny">
            {{ text('评测分数', 'Benchmark score') }} {{ fmtMetric(m.score) }}
          </div>
        </div>
        <div class="redlines">
          <template v-if="reports(m).length">
            <div v-for="report in reports(m)" :key="report.module" class="module-report">
              <div class="module-verdict">
                <b class="mono tiny">{{ report.module }}</b>
                <span v-if="scenarioOf(report)" class="muted tiny">{{ scenarioOf(report) }}</span>
                <el-tag v-if="report.passed === false" type="danger" size="small">{{ statusLabel('failed', locale) }}</el-tag>
                <el-tag v-else-if="report.passed" type="success" size="small"
                  effect="plain">{{ statusLabel('passed', locale) }}</el-tag>
                <span v-if="report.score != null" class="muted tiny">
                  {{ text('分数', 'Score') }} {{ fmtMetric(report.score) }}</span>
              </div>
              <div v-if="report.error" class="crossed-val tiny">{{ report.error }}</div>
              <div v-for="b in report.bounds" :key="b.role + b.metric" class="redline"
                :class="b.role === 'display' ? 'display-bound' : ''">
                <el-tag :type="boundLook(b).type" size="small" effect="plain">
                  {{ boundLook(b).label }}</el-tag>
                <span class="mono tiny" :title="b.metric">{{ boundText(b) }}</span>
                <span class="mono tiny" :class="b.ok === false ? 'crossed-val' : ''">
                  {{ text('实测', 'Measured') }} {{ fmtMetric(b.actual) }}</span>
                <span v-if="b.role === 'display'" class="muted tiny">{{ text('仅展示，不影响通过判定', 'Display only; does not affect the verdict') }}</span>
              </div>
              <span v-if="!report.bounds.length" class="muted tiny">
                {{ text('此模块未设置阈值', 'No thresholds configured for this module') }}
              </span>
            </div>
          </template>
          <span v-else class="muted tiny">{{ t('baselineHub.detail.noRedlines') }}</span>
        </div>
      </div>

      <!-- The sweep as an ops table: scenario × concurrency. The reported
           level is the one the benchmark scored; a probe is an auto-search
           step, shorter than the confirmation run and read accordingly. -->
      <div v-if="sweeps.get(m.id)?.rows.length" class="sweep">
        <div class="sweep-head">
          <b>{{ sweeps.get(m.id)!.title }}</b>
          <span class="muted tiny">
            {{ m.gpu_count }}×{{ m.gpu_type || 'GPU' }} {{ text('每实例', 'per instance') }} ·
            {{ text('单机 TPM 归一化至', 'Per-machine TPM normalized to') }} {{ CARD_NORM_BASELINE }} {{ text('卡', 'cards') }}
            {{ sweeps.get(m.id)!.scaleSource === 'benchmark'
              ? text('（使用评测归一化系数）', '(benchmark normalization factor)') : text('（根据本次运行卡数）', '(from the cards this run held)') }}
          </span>
        </div>
        <el-table :data="sweeps.get(m.id)!.rows" size="small" :span-method="sweepSpan"
          :row-class-name="sweepRowClass" class="sweep-table">
          <el-table-column :label="text('场景（输入 + 输出）', 'Scenario (in + out)')" width="150">
            <template #default="{ row }">
              <span class="mono scenario">{{ row.scenario }}</span>
            </template>
          </el-table-column>
          <el-table-column :label="text('并发数', 'Concurrency')" width="150" align="right">
            <template #default="{ row }">
              <span class="mono" :class="row.meetsSlo === false ? 'slo-miss' : ''"
                :title="row.meetsSlo === false ? text('未满足此场景的 SLO', 'Did not meet the scenario SLO') : ''">
                {{ row.concurrency }}</span>
              <el-tag v-if="row.reported" size="small" effect="plain" class="level-tag">
                {{ text('计分档位', 'Reported') }}</el-tag>
              <span v-else-if="row.probe" class="muted tiny level-tag">{{ text('探索档位', 'Probe') }}</span>
            </template>
          </el-table-column>
          <el-table-column :label="text('输入速度（tok/s）', 'Input tok/s')" min-width="110" align="right">
            <template #default="{ row }"><span class="mono">{{ fmtInt(row.inputTps) }}</span></template>
          </el-table-column>
          <el-table-column :label="text('输出速度（tok/s）', 'Output tok/s')" min-width="110" align="right">
            <template #default="{ row }"><span class="mono">{{ fmtInt(row.outputTps) }}</span></template>
          </el-table-column>
          <el-table-column :label="text('单请求输出（tok/s）', 'Per-request out tok/s')" min-width="150" align="right">
            <template #default="{ row }">
              <span class="mono">{{ fmt1(row.requestOutputTps) }}</span>
            </template>
          </el-table-column>
          <el-table-column :label="text(`单机 TPM（${CARD_NORM_BASELINE} 卡）`, `Per-machine TPM (${CARD_NORM_BASELINE} cards)`)"
            min-width="170" align="right">
            <template #default="{ row }"><span class="mono tpm">{{ fmtInt(row.tpm) }}</span></template>
          </el-table-column>
          <el-table-column label="TTFT p50 / p99" min-width="130" align="right">
            <template #default="{ row }">
              <span class="mono muted">{{ fmtSec(row.ttftP50) }} / {{ fmtSec(row.ttftP99) }}</span>
            </template>
          </el-table-column>
        </el-table>
      </div>

      <!-- What the model got right, next to how it was asked: the board only
           carries pass or fail, so the numbers belong here. -->
      <div v-for="panel in qualityPanels(m)" :key="panel.module" class="sweep">
        <div class="sweep-head">
          <b>{{ t('baselineHub.detail.quality') }}</b>
          <span class="mono tiny">{{ panel.module }}</span>
          <el-tag v-if="panel.passed === false" type="danger" size="small">
            {{ t('baselineHub.board.qualityFail') }}</el-tag>
          <el-tag v-else-if="panel.passed" type="success" size="small" effect="plain">
            {{ t('baselineHub.board.qualityPass') }}</el-tag>
          <span class="muted tiny">
            <template v-if="panel.elapsed != null">{{ fmtMinutes(panel.elapsed) }}</template>
            <template v-if="panel.genTokens != null"> ·
              {{ fmtInt(panel.genTokens) }} {{ t('baselineHub.detail.generated') }}</template>
            <template v-if="panel.outputTps != null"> ·
              {{ fmt1(panel.outputTps) }} tok/s</template>
          </span>
        </div>
        <div v-if="panel.error" class="crossed-val tiny">{{ panel.error }}</div>
        <el-table :data="panel.rows" size="small" class="sweep-table">
          <el-table-column :label="t('baselineHub.detail.task')" min-width="180">
            <template #default="{ row }"><span class="mono">{{ row.task }}</span></template>
          </el-table-column>
          <el-table-column :label="t('baselineHub.detail.score')" width="110" align="right">
            <template #default="{ row }">
              <span class="mono tpm" :class="row.ok === false ? 'crossed-val' : ''">
                {{ fmtScore(row.value) }}</span>
            </template>
          </el-table-column>
          <el-table-column :label="t('baselineHub.detail.gate')" width="110" align="right">
            <template #default="{ row }">
              <span v-if="row.gate" class="mono muted">{{ row.gate }}</span>
              <span v-else class="muted tiny">{{ t('baselineHub.detail.notGated') }}</span>
            </template>
          </el-table-column>
          <el-table-column :label="t('baselineHub.detail.samples')" width="140" align="right">
            <template #default="{ row }">
              <span v-if="row.samples != null" class="mono muted">
                <template v-if="row.correct != null">{{ fmtInt(row.correct) }} /
                </template>{{ fmtInt(row.samples) }}</span>
              <span v-else class="muted">—</span>
            </template>
          </el-table-column>
          <el-table-column width="90">
            <template #default="{ row }">
              <el-tag v-if="row.ok === true" type="success" size="small" effect="plain">
                {{ t('baselineHub.detail.held') }}</el-tag>
              <el-tag v-else-if="row.ok === false" type="danger" size="small">
                {{ t('baselineHub.detail.crossed') }}</el-tag>
            </template>
          </el-table-column>
        </el-table>
      </div>

      <el-collapse class="measure-details">
        <el-collapse-item name="metrics">
          <template #title>
            <span>{{ t('baselineHub.detail.allMetrics') }}</span>
            <span class="muted tiny title-note">
              {{ t('baselineHub.detail.values', { n: Object.keys(m.metrics).length }) }} ·
              {{ metricGroups(m).map((g) => g.module).join(', ') }}</span>
          </template>
          <div v-for="group in metricGroups(m)" :key="group.module" class="module">
            <div class="module-head">
              <b class="mono">{{ group.module }}</b>
              <span v-if="group.score != null" class="muted tiny">
                {{ text('模块分数', 'Module score') }} {{ fmtMetric(group.score) }}</span>
            </div>
            <el-table :data="group.rows" size="small" max-height="320">
              <el-table-column :label="text('指标', 'Metric')" min-width="260">
                <template #default="{ row }">
                  <span class="mono" :class="row[0] === m.target_metric ? 'target' : ''">
                    {{ row[0] }}</span>
                </template>
              </el-table-column>
              <el-table-column :label="text('数值', 'Value')" width="150" align="right">
                <template #default="{ row }">
                  <span class="mono">{{ fmtMetric(row[1]) }}</span>
                </template>
              </el-table-column>
            </el-table>
          </div>
        </el-collapse-item>
        <el-collapse-item :title="t('baselineHub.detail.repro')" name="repro">
          <pre class="mono args">{{ m.launch_command || t('baselineHub.detail.notRecorded') }}</pre>
          <pre class="mono args">{{ JSON.stringify(m.env_snapshot, null, 2) }}</pre>
        </el-collapse-item>
      </el-collapse>
      </el-card>
    </details>

    <details class="disclosure block">
      <summary>{{ t('baselineHub.detail.declared') }}</summary>
      <div class="disclosure-body">
      <div class="config-grid">
        <div><span class="muted">{{ t('baselineHub.detail.image') }}</span>
          <span class="mono">{{ sub.image }}</span></div>
        <div><span class="muted">{{ t('baselineHub.detail.modelPath') }}</span>
          <span class="mono">{{ sub.model_path }}</span></div>
        <div><span class="muted">{{ t('baselineHub.detail.benchmark') }}</span>
          <span class="mono">{{ sub.benchmark_slug || t('baselineHub.detail.platformDefault') }}</span></div>
        <div><span class="muted">{{ t('baselineHub.detail.rankingMetric') }}</span>
          <span class="mono">{{ (sub.objective ?? {}).target_metric
            || t('baselineHub.detail.benchmarkScore') }}</span></div>
        <div><span class="muted">{{ t('baselineHub.detail.occupancy') }}</span>
          <span>{{ sub.share_machine ? t('baselineHub.detail.shareMachine')
            : t('baselineHub.detail.wholeMachine') }}</span></div>
        <div v-if="sub.machine_name"><span class="muted">{{ t('baselineHub.detail.machine') }}</span>
          <span>{{ sub.machine_name }}</span></div>
        <div v-if="sub.campaign_id"><span class="muted">{{ t('baselineHub.detail.campaign') }}</span>
          <span class="mono">#{{ sub.campaign_id }}</span>
          <span v-if="sub.campaign_status" class="muted"> · {{ statusLabel(sub.campaign_status, locale) }}</span></div>
      </div>
      <FlagChips class="args-block" :args="sub.engine_args"
        :known="knownNames" />
      <details class="raw">
        <summary class="muted tiny">{{ t('baselineHub.detail.rawParams') }}</summary>
        <pre class="mono args">{{ JSON.stringify(sub.engine_args, null, 2) }}</pre>
      </details>
      <div v-if="Object.keys(sub.extra_env ?? {}).length" class="muted tiny">
        {{ text('环境变量', 'Environment') }}: <span class="mono">{{ JSON.stringify(sub.extra_env) }}</span>
      </div>
      <div v-if="Object.keys(sub.extra_volumes ?? {}).length" class="muted tiny">
        {{ text('挂载', 'Volumes') }}: <span class="mono">{{ JSON.stringify(sub.extra_volumes) }}</span>
      </div>
      <div v-if="sub.notes" class="muted tiny">{{ sub.notes }}</div>
      </div>
    </details>

    <!-- The entry's runs, right here: an entry is a hidden one-config
         campaign, and "running" alone sent readers off to find it. Each run
         opens to its launch command, results, error and captured log. -->
    <details v-if="sub.campaign_id" :open="live" class="disclosure block">
      <summary>{{ t('baselineHub.detail.runs') }}</summary>
      <div class="disclosure-body">
      <div>
        <div class="measure-head">
          <b>{{ t('baselineHub.detail.runs') }}</b>
          <span v-if="sub.status === 'running' && sub.run_status" class="muted tiny">
            {{ statusLabel(sub.run_status, locale) }}<template v-if="sub.run_id"> · run #{{ sub.run_id }}</template>
          </span>
        </div>
      </div>
      <CampaignRuns :campaign-id="sub.campaign_id" :name="sub.name" :can-stop="ownsSubmission(sub)" />
      </div>
    </details>


    <BaselineSubmitDialog v-model="evolveOpen" :track="track" :resubmit-of="sub"
      :gpu-types="gpuTypes" :flag-catalog="flagCatalog"
      @submitted="(id: number) => router.push(`/baseline-hub/${id}`)" />
  </div>
  <PageLoading v-else :error="first.error" @retry="first.run" />
</template>

<style scoped>
.measurement { border: 1px solid var(--autotune-border); border-radius: 8px; background: white; }
.measurement > summary { padding: 16px 20px; cursor: pointer; }
.measurement .measure-head { display: inline-flex; flex-wrap: wrap; vertical-align: middle; max-width: calc(100% - 24px); }
.measurement > .el-card { border: 0; border-top: 1px solid var(--autotune-border); border-radius: 0 0 8px 8px; }
@media (max-width: 720px) {
  .page .config-grid { grid-template-columns: minmax(0, 1fr); }
  .score-row .hero { max-width: 100%; overflow-wrap: anywhere; }
  .redline, .module-verdict { flex-wrap: wrap; }
}

.header-row {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 24px;
  margin-bottom: 12px;
}
.actions {
  display: flex;
  gap: 8px;
}
.version {
  margin-left: 8px;
  vertical-align: middle;
}
.lineage {
  margin-bottom: 12px;
}
.block {
  margin-bottom: 16px;
}
.config-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: 6px 24px;
  margin-bottom: 10px;
}
.config-grid > div {
  display: flex;
  gap: 8px;
  align-items: baseline;
}
.config-grid .muted {
  min-width: 110px;
  font-size: 12px;
}
.args {
  background: var(--autotune-bg, #f5f7fa);
  border-radius: 6px;
  padding: 10px;
  font-size: 12px;
  overflow-x: auto;
  margin: 6px 0;
}
.measure-head {
  display: flex;
  align-items: center;
  gap: 10px;
}
.measure-head .spacer {
  flex: 1;
}
.args-block {
  margin: 4px 0 8px;
}
.raw {
  margin-bottom: 8px;
}
.score-row {
  display: flex;
  gap: 32px;
  align-items: flex-start;
  margin-bottom: 12px;
  flex-wrap: wrap;
}
.hero-num {
  font-size: 30px;
  font-weight: 700;
  line-height: 1.1;
}
.hero-label {
  font-size: 12px;
  color: var(--autotune-muted, #909399);
}
.redlines {
  display: flex;
  flex-direction: column;
  gap: 5px;
  padding-top: 4px;
}
.redline {
  display: flex;
  align-items: center;
  gap: 8px;
}
.module-report {
  margin-bottom: 8px;
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.module-verdict {
  display: flex;
  align-items: center;
  gap: 8px;
}
.display-bound {
  opacity: 0.65;
}
.crossed-val {
  color: var(--el-color-danger);
  font-weight: 600;
}
.sweep {
  margin: 4px 0 14px;
}
.sweep-head {
  display: flex;
  align-items: baseline;
  gap: 10px;
  margin-bottom: 6px;
  flex-wrap: wrap;
}
.scenario {
  font-weight: 600;
}
.level-tag {
  margin-left: 6px;
}
.tpm {
  font-weight: 600;
}
.slo-miss {
  color: var(--el-color-danger);
}
.sweep-table :deep(.sweep-reported) {
  font-weight: 700;
}
.sweep-table :deep(.sweep-probe) {
  opacity: 0.6;
}
.measure-details {
  margin-top: 4px;
}
.title-note {
  margin-left: 10px;
}
.module {
  margin-bottom: 14px;
}
.module-head {
  display: flex;
  align-items: baseline;
  gap: 10px;
  margin: 6px 0;
}
.target {
  font-weight: 700;
}
.tiny {
  font-size: 13px;
}
.repro {
  margin-top: 10px;
}
.link {
  color: var(--el-color-primary);
  cursor: pointer;
}
.queue-line {
  display: block;
  margin-top: 4px;
}
.empty {
  margin-top: 16px;
}
.queue-note {
  margin-top: 16px;
}
</style>
