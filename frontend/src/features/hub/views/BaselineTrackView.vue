<script setup lang="ts">
/** One track: its rules at a glance (model, gate, benchmark, entries,
 *  baseline, budget), the config that currently leads, and — a tab down —
 *  the whole board, the entries still on their way and the full rules.
 *
 *  The leader is the page's point. A track exists to answer "what is the best
 *  known way to serve this model on this card", so the answer sits at the top
 *  with its launch command attached, copyable in one click: a leader you
 *  cannot reproduce is trivia. The ranking table underneath is for reading
 *  how it was won, which is a second question.
 */
import { statusLabel, metricLabel } from '@/platform/i18n/display'
import { useUrlState, usePageScroll, rememberedPage, queryString, queryBoolean, queryPage, queryChoice } from '@/platform/composables/pageState'
import { inLineLabel, queueTooltip as queueTooltipOf } from '../utils/queue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { computed, onMounted, ref, toRefs, watch } from 'vue'
import { filterBoardRows, formatRankingValue, hasQualityAssessment, qualityVerdict } from '../utils/board'
import { useRoute, useRouter } from 'vue-router'
import { api, ownsSubmission } from '@/plugins/api'
import { type BaselineSubmission, type BaselineTrackDetail, type BenchmarkYaml, type ParamsRollout, type FlagSpec, type MachineWeights, type TrackBenchmarkLive, type TrackBoardRow, type TrackQualityCell, type TrackReferencePlan, type TrackTemplate } from '../types'
import { Aim, Cpu, Document, Medal, Timer, Trophy } from '@element-plus/icons-vue'
import { BackLink, CatalogExportDialog, FactTile, PluginSlot, type CatalogCandidate } from '@/plugins/api'
import BaselineSubmitDialog from '../components/BaselineSubmitDialog.vue'
import { CopyButton } from '@/plugins/api'
import { FlagChips } from '@/plugins/api'
import { InfoHint } from '@/plugins/api'
import { useI18n } from '@/plugins/api'
import { useAuthStore } from '@/plugins/api'
import { apiErrorText } from '@/plugins/api'
import { copyText } from '@/plugins/api'
import { useRowLink } from '@/plugins/api'
import { usePoll } from '@/plugins/api'
import { PageLoading, useFirstLoad } from '@/plugins/api'
import { exactTime, relativeTime } from '@/plugins/api'

const boardState = useUrlState({
  details: queryBoolean(), q: queryString(), page: queryPage(), size: queryChoice('20', ['20', '50', '100']),
  scenario: queryString(), tab: queryChoice('board', ['board', 'pending', 'rules']), hideStale: queryBoolean(true),
})
const { details: showBoardDetails, q: boardSearch, page: boardPage, scenario: selectedScenario,
  tab, hideStale } = toRefs(boardState)
const boardPageSize = computed({ get: () => Number(boardState.size), set: (n: number) => { boardState.size = String(n) as '20' | '50' | '100' } })
const resetBoardPage = () => { boardPage.value = 1 }
const trackListUrl = rememberedPage('/baseline-hub')
const selectedConfig = ref<TrackBoardRow | null>(null)
const configOpen = computed({
  get: () => !!selectedConfig.value,
  set: (value: boolean) => { if (!value) selectedConfig.value = null },
})
const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
const { t, locale } = useI18n()
const inLine = (row: { queued_reason?: string; queue_ahead?: string[] }) => inLineLabel(t, row)
const queueTooltip = (row: { queued_reason?: string; queue_ahead?: string[] }) => queueTooltipOf(t, row)
const slug = computed(() => String(route.params.slug))

const track = ref<BaselineTrackDetail | null>(null)
const live = ref<TrackBenchmarkLive | null>(null)
const templates = ref<TrackTemplate[]>([])
const gpuTypes = ref<string[]>([])
const flagCatalog = ref<Record<string, FlagSpec[]>>({})
const isAdmin = computed(() => auth.user?.role === 'admin')
/** Rows measured under an older version of the benchmark are hidden by
 *  default: they cannot be compared with the rest, so showing them next to
 *  numbers they do not share invites exactly the comparison the board
 *  refuses to make. The toggle is there because they are still real runs. */

const submitOpen = ref(false)
const resubmitOf = ref<BaselineSubmission | null>(null)
const editOpen = ref(false)
const editBusy = ref(false)
const benchOpen = ref(false)
const benchBusy = ref(false)
const columnsOpen = ref(false)
const columnsBusy = ref(false)
/** Metric keys the admin picked, in pick order; empty = automatic. */
const chosenColumns = ref<string[]>([])
const columnLabels = ref<Record<string, string>>({})
/** Confirming an entry as the baseline: the dialog shows the plan (floors,
 *  who drops) from a dry run before anything is written. */
const refOpen = ref(false)
const refBusy = ref(false)
const refPlanning = ref(false)
const refRow = ref<TrackBoardRow | null>(null)
const refMargin = ref(5)
const refPlan = ref<TrackReferencePlan | null>(null)
const refError = ref('')
const bench = ref({
  mode: 'bind' as 'create' | 'bind',
  template: '',
  bind_benchmark_slug: '',
  confirm: false,
})
type ImageRow = { engine: string; image: string; label: string }
/** Machines of this track's card type — what the pin may point at — plus
 *  the ones with no card type recorded, listed disabled: a k8s machine whose
 *  node probe failed has none, and a machine that silently isn't in the list
 *  reads as a bug. Loaded when the edit dialog opens; admins only see this
 *  dialog, and /machines is an admin call. */
const pinnableMachines = ref<{ id: number; name: string; untyped: boolean }[]>([])
async function loadPinnable() {
  try {
    const all = (await api.get('/machines')).data as { id: number; name: string; gpu_type: string }[]
    pinnableMachines.value = all
      .filter((m) => m.gpu_type === track.value?.gpu_type || !m.gpu_type)
      .map((m) => ({ id: m.id, name: m.name, untyped: !m.gpu_type }))
  } catch {
    pinnableMachines.value = []
  }
}

const edit = ref({
  model_path: '',
  images: [] as ImageRow[],
  cookbook_url: '',
  notes: '',
  max_run_minutes: 150,
  ranking_metric: '',
  contest_benchmark_id: undefined as number | undefined,
  ttft_ms: 60000,
  ttft_percentile: 'p50',
  min_request_output_tps: 15,
  pinned_machine_id: 0,
})

async function load() {
  track.value = (await api.get(`/baseline-hub/tracks/${slug.value}`)).data
}
/** Once per visit, not per poll: this one asks LLMBench. */
async function loadLive() {
  try {
    live.value = (await api.get(`/baseline-hub/tracks/${slug.value}/benchmark`)).data
  } catch {
    live.value = null
  }
}
async function loadTemplates() {
  try {
    templates.value = (await api.get('/baseline-hub/track-templates')).data
  } catch {
    templates.value = []
  }
}
/** The template this track's benchmark was filled from, when it had one. */
const template = computed(
  () => templates.value.find((t) => t.name === track.value?.template) ?? null,
)

/** Scenario columns, in the order they first appear on the board. */
const scenarioLabels = computed(() => {
  const seen: string[] = []
  for (const row of board.value)
    for (const s of row.scenarios) if (!seen.includes(s.label)) seen.push(s.label)
  return seen
})
function scenario(row: TrackBoardRow, label: string) {
  return row.scenarios.find((s) => s.label === label) ?? null
}
/** The non-throughput columns, taken from the rows themselves: a benchmark
 *  that gates on quality puts that number on every row it measured. */
const hasQualityChecks = computed(() => hasQualityAssessment(board.value, live.value?.modules))
const performanceOnly = computed(() => !!live.value?.modules.length && !hasQualityChecks.value)
const qualityColumns = computed(() => {
  const seen = new Map<string, TrackQualityCell>()
  for (const row of board.value)
    for (const q of row.quality) if (!seen.has(q.key)) seen.set(q.key, q)
  const cells = [...seen.values()]
  const modules = cells.filter((c) => c.kind === 'module').length
  return cells.map((c) => ({
    key: c.key,
    kind: c.kind,
    // One quality suite is just "Quality"; two need naming apart.
    header: c.kind === 'metric' ? c.label : modules > 1 ? `${t('baselineHub.board.quality')} · ${c.label}` : t('baselineHub.board.quality'),
  }))
})
function quality(row: TrackBoardRow, key: string): TrackQualityCell | null {
  return row.quality.find((q) => q.key === key) ?? null
}
function qualityText(cell: TrackQualityCell | null): string {
  if (!cell || cell.value == null) return '—'
  return Math.abs(cell.value) >= 100 ? cell.value.toFixed(0) : cell.value.toPrecision(3)
}
function qualityGate(cell: TrackQualityCell | null): string {
  if (!cell) return ''
  if (cell.kind === 'module') {
    if (cell.detail) return `${cell.key}: ${cell.detail}`
    return cell.ok === null
      ? `${cell.key}: ${locale.value === 'zh' ? '未报告' : 'Not reported'}`
      : `${cell.key}: ${cell.ok ? t('baselineHub.detail.held') : t('baselineHub.detail.crossed')}`
  }
  const parts = []
  if (cell.min != null) parts.push(`≥ ${cell.min}`)
  if (cell.max != null) parts.push(`≤ ${cell.max}`)
  const gate = parts.length ? ` (${parts.join(', ')})` : ''
  return `${cell.key}${gate}`
}
function first(row: TrackBoardRow) {
  return row.scenarios[0] ?? null
}
const pending = computed(() => (track.value?.entries ?? []).filter((e) => e.status !== 'measured'))
const staleRows = computed(() => (track.value?.board ?? []).filter((r) => r.stale_benchmark))
const board = computed(() =>
  hideStale.value
    ? (track.value?.board ?? []).filter((r) => !r.stale_benchmark)
    : (track.value?.board ?? []),
)
const filteredBoard = computed(() => filterBoardRows(board.value, boardSearch.value))
const pagedBoard = computed(() => filteredBoard.value.slice(
  (boardPage.value - 1) * boardPageSize.value, boardPage.value * boardPageSize.value,
))
const visibleScenarioLabels = computed(() => showBoardDetails.value ? scenarioLabels.value
  : selectedScenario.value ? [selectedScenario.value] : [])
watch(() => [!!track.value, filteredBoard.value.length, boardPageSize.value] as const, ([loaded, length]) => {
  if (!loaded) return
  boardPage.value = Math.min(boardPage.value, Math.max(1, Math.ceil(length / boardPageSize.value)))
})
const boardEmptyText = computed(() => boardSearch.value.trim()
  ? (locale.value === 'zh' ? '没有匹配的提交，请调整搜索条件。' : 'No matching entries. Try a different search.')
  : t('baselineHub.board.empty'))
const rankingLabel = computed(() => metricLabel(track.value?.ranking_metric ?? '', locale.value))
function rankValue(value: number | null | undefined) {
  return formatRankingValue(value, track.value?.ranking_metric ?? '')
}
function qualityLabel(row: TrackBoardRow) {
  const verdict = qualityVerdict(row.quality)
  return verdict === 'passed' ? t('baselineHub.board.qualityPass')
    : verdict === 'failed' ? t('baselineHub.board.qualityFail') : (locale.value === 'zh' ? '未报告' : 'Not reported')
}
const winner = computed(() => (track.value?.board ?? []).find((r) => r.rank === 1 && !r.stale_benchmark) ?? null)
const rankedCount = computed(() => (track.value?.board ?? []).filter((r) => r.rank && !r.stale_benchmark).length)
/** Every spelling the winner's engine knows, so novel flags get badged. */
const knownFlags = computed(() =>
  (flagCatalog.value[selectedConfig.value?.engine ?? ''] ?? []).flatMap((s) => [s.key, ...s.aliases]),
)

function fmtTpm(v: number | null | undefined): string {
  return v == null ? '—' : Math.round(v).toLocaleString('en-US')
}
function fmt1(v: number | null | undefined): string {
  return v == null ? '—' : v.toFixed(1)
}
function fmtSec(ms: number | null | undefined): string {
  return ms == null ? '—' : `${(ms / 1000).toFixed(1)} s`
}
function fmtConc(c: number | null | undefined): string {
  return c == null ? '—' : String(Math.round(c))
}
function shortImage(reference: string): string {
  return reference.split('/').pop() ?? reference
}
function tokens(n: number | null): string {
  if (n == null) return '?'
  if (n >= 1000) {
    const k = n / 1000
    return `${Number.isInteger(k) ? k.toFixed(0) : k.toFixed(1)}k`
  }
  return String(n)
}
/** A sweep reads as its token shape; a replay has none, so it reads as itself. */
function scenarioText(m: { label?: string; input_tokens: number | null; output_tokens: number | null }) {
  if (m.input_tokens == null && m.label) return m.label
  return `${tokens(m.input_tokens)} in + ${tokens(m.output_tokens)} out`
}
const rankedBy = computed(() => {
  const m = track.value?.ranking_metric ?? ''
  if (m.endsWith('total_tpm_card_norm')) return t('baselineHub.rules.rankedByTpm')
  if (m.endsWith('output_tpm_card_norm')) return t('baselineHub.rules.rankedByOutput')
  return m || t('baselineHub.rules.rankedByScore')
})
const rankedByShort = computed(() => {
  const m = track.value?.ranking_metric ?? ''
  if (m.endsWith('total_tpm_card_norm')) return t('baselineHub.fact.rankedByTpm')
  if (m.endsWith('output_tpm_card_norm')) return t('baselineHub.fact.rankedByOutput')
  return m || t('baselineHub.fact.rankedByScore')
})
const ttftLabel = computed(() => `TTFT ${track.value?.slo.ttft_percentile ?? 'p50'}`)

/** Quantization belongs to the model name (Qwen3.8-27B-FP8). When it is
 *  already there, the separate precision field is noise, not information. */
const showPrecision = computed(() => {
  const t = track.value
  if (!t?.precision) return false
  return !t.model_name.toLowerCase().includes(t.precision.toLowerCase())
})
/** The leader's whole config as one pasteable block: the exact docker line
 *  that produced the measurement, or — when a run predates the capture — the
 *  declared parts, so the button never copies nothing. */
function configText(w: TrackBoardRow | null): string {
  if (!w) return ''
  if (w.launch_command) return w.launch_command
  const args = Object.entries(w.engine_args)
    .map(([k, v]) => (v === true ? `--${k.replace(/_/g, '-')}` : `--${k.replace(/_/g, '-')} ${v}`))
    .sort()
  return [`# image: ${w.image}`, `# engine: ${w.engine} ${w.engine_version}`, ...args].join('\n')
}
function copyConfig(row: TrackBoardRow) {
  copyText(configText(row), t('baselineHub.winner.copied'))
}

type TagType = 'success' | 'warning' | 'danger' | 'info' | 'primary'
function trackStatusType(status: string): TagType {
  return ({ open: 'success', draft: 'warning', retired: 'info' } as Record<string, TagType>)[status] ?? 'info'
}
function statusType(status: string): TagType {
  const looks: Record<string, TagType> = {
    queued: 'info', running: 'primary', measured: 'success', failed: 'danger', aborted: 'warning',
  }
  return looks[status] ?? 'info'
}

function openEntry() {
  resubmitOf.value = null
  submitOpen.value = true
}
function openResubmit(s: BaselineSubmission) {
  resubmitOf.value = s
  submitOpen.value = true
}
/** Evolve straight off the board: the entry behind the row, from the ones the
 *  track already handed us — a leader you cannot iterate on is a dead end. */
function evolveRow(row: TrackBoardRow) {
  const entry = (track.value?.entries ?? []).find((e) => e.id === row.submission_id)
  if (entry) openResubmit(entry)
  else ElMessage.error(t('baselineHub.msg.entryGone'))
}

async function abort(s: BaselineSubmission) {
  try {
    await ElMessageBox.confirm(
      s.status === 'running'
        ? t('baselineHub.msg.abortRunning', { name: s.name })
        : t('baselineHub.msg.abortQueued', { name: s.name }),
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
    await api.post(`/baseline-hub/submissions/${s.id}/abort`)
    await load()
  } catch (error: any) {
    ElMessage.error(apiErrorText(error, t('baselineHub.msg.abortFailed')))
  }
}

// -------------------------------------------------------------------- admin

async function adminPost(path: string, ok: string) {
  try {
    const { data } = await api.post(`/baseline-hub/tracks/${slug.value}/${path}`)
    ElMessage.success(ok)
    await load()
    return data
  } catch (error: any) {
    ElMessage.error(apiErrorText(error, t('baselineHub.msg.didNotWork')))
    return null
  }
}

async function onAdmin(command: string) {
  if (command === 'retry') {
    const data = await adminPost('retry-benchmark', t('baselineHub.msg.benchmarkRetried'))
    if (data?.status === 'draft')
      ElMessage.warning(data.benchmark_error || t('baselineHub.msg.stillDraft'))
    await loadLive()
  } else if (command === 'retire') {
    try {
      await ElMessageBox.confirm(
        t('baselineHub.msg.retireConfirm'), t('baselineHub.admin.retire'), {
          confirmButtonText: t('baselineHub.msg.retireAction'),
          cancelButtonText: t('baselineHub.msg.keep'),
          type: 'warning',
        },
      )
    } catch {
      return
    }
    await adminPost('retire', t('baselineHub.msg.trackRetired'))
  } else if (command === 'reopen') {
    await adminPost('reopen', t('baselineHub.msg.trackReopened'))
  } else if (command === 'adopt') {
    const data = await adminPost('adopt-submissions', t('baselineHub.msg.legacyAttached'))
    if (data) ElMessage.info(t('baselineHub.msg.attachedCounts', {
      subs: data.submissions, measurements: data.measurements }))
  } else if (command === 'benchmark') {
    openBenchmark()
  } else if (command === 'columns') {
    openColumns()
  } else if (command === 'params') {
    await openParams()
  } else if (command === 'redlines') {
    openRedlines()
  } else if (command === 'edit') {
    const t = track.value
    if (!t) return
    edit.value = {
      model_path: t.model_path,
      images: t.images.map((i) => ({ engine: i.engine, image: i.image, label: i.label ?? '' })),
      cookbook_url: t.cookbook_url,
      notes: t.notes,
      max_run_minutes: t.max_run_minutes,
      ranking_metric: t.ranking_metric,
      contest_benchmark_id: t.contest_benchmark_id ?? undefined,
      pinned_machine_id: t.pinned_machine_id ?? 0,
      ttft_ms: t.slo.ttft_ms,
      ttft_percentile: t.slo.ttft_percentile,
      min_request_output_tps: t.slo.min_request_output_tps,
    }
    editOpen.value = true
    void loadPinnable()
  }
}

/** Redlines: the minimums an entry has to clear, moved without changing what
 *  the benchmark measures. The old config hash joins the lineage, so no row
 *  stops ranking, and no row is re-judged — a frozen verdict stands.
 *
 *  The gate's own redlines are NOT here. `apply_gate` writes the TTFT ceiling
 *  and the per-request tok/s floor and demotes every other redline on a gated
 *  module, so a floor set on one of those from here would leave the track
 *  advertising an SLO its benchmark no longer runs, and would be wiped by the
 *  next gate change anyway. The server refuses them; this list never offers
 *  them. They move under Edit track, with the un-ranking that a gate costs. */
const GATE_METRICS = /^(ttft_(mean|p50|p90|p99)_ms|request_output_tps)$/

const redlinesOpen = ref(false)
const redlinesBusy = ref(false)
const redlinesError = ref('')
const redlineEdits = ref<Record<string, number | null>>({})

/** Every minimum the benchmark judges on that this dialog owns, in benchmark
 *  order, with the track's own note of which are confirmed quality floors. */
const redlineRows = computed(() => {
  const confirmed = track.value?.quality_floors ?? {}
  const rows: { key: string; metric: string; module: string; now: number; quality: boolean }[] = []
  for (const mod of live.value?.modules ?? []) {
    for (const metric of mod.metrics) {
      if (metric.role !== 'redline' || metric.min_val == null) continue
      if (GATE_METRICS.test(metric.key)) continue
      const key = `${mod.key}.${metric.key}`
      rows.push({
        key, metric: metric.key, module: mod.key,
        now: metric.min_val, quality: key in confirmed,
      })
    }
  }
  return rows
})

/** Only what actually moved — sending a floor its metric already has would
 *  rewrite the benchmark for nothing and put a no-op in the audit log. */
const redlineChanges = computed(() => {
  const out: Record<string, number> = {}
  for (const row of redlineRows.value) {
    const next = redlineEdits.value[row.key]
    if (next != null && next !== row.now) out[row.key] = next
  }
  return out
})

function openRedlines() {
  redlinesError.value = ''
  redlineEdits.value = Object.fromEntries(
    redlineRows.value.map((row) => [row.key, row.now]),
  )
  redlinesOpen.value = true
}

async function saveRedlines() {
  redlinesBusy.value = true
  redlinesError.value = ''
  try {
    await api.post(`/baseline-hub/tracks/${slug.value}/redlines`,
                   { floors: redlineChanges.value })
    ElMessage.success(t('baselineHub.admin.redlines.saved'))
    redlinesOpen.value = false
    await load()
    await loadLive()
  } catch (e) {
    redlinesError.value = apiErrorText(e, t('baselineHub.admin.redlines.failed'))
  } finally {
    redlinesBusy.value = false
  }
}

/** The mini params editor: the track's benchmark as LLMBench's own export
 *  YAML, edited by hand, and applied to one or more tracks.
 *
 *  What is applied is the DIFF between the document loaded and the document
 *  saved — never the document itself. Every track's benchmark carries its own
 *  identity, its own gate and its own quality floors, so writing this one's
 *  document onto three others would stamp this track's rules over theirs. The
 *  server computes the diff and refuses anything in it that is not a module
 *  param, naming the control that owns it instead. */
const paramsOpen = ref(false)
const paramsBusy = ref(false)
const paramsLoading = ref(false)
const paramsBase = ref('')          // the document as loaded — the diff's other side
const paramsText = ref('')          // the document as edited
const paramsTargets = ref<string[]>([])
const paramsComparable = ref(true)
const paramsNote = ref('')
const paramsPreview = ref<ParamsRollout | null>(null)
const paramsError = ref('')
/** Every track this edit could go to — only ones with a benchmark the
 *  platform owns, because those are the only ones it may rewrite. */
const paramsTracks = ref<{ slug: string; name: string }[]>([])

const paramsDirty = computed(() => paramsText.value !== paramsBase.value)

async function openParams() {
  paramsOpen.value = true
  paramsLoading.value = true
  paramsError.value = ''
  paramsPreview.value = null
  paramsNote.value = ''
  paramsComparable.value = true
  paramsTargets.value = [slug.value]
  try {
    const [loaded, all] = await Promise.all([
      api.get<BenchmarkYaml>(`/baseline-hub/tracks/${slug.value}/benchmark/yaml`),
      api.get<BaselineTrackDetail[]>('/baseline-hub/tracks'),
    ])
    paramsBase.value = loaded.data.yaml
    paramsText.value = loaded.data.yaml
    paramsTracks.value = all.data
      .filter((tr) => tr.benchmark_managed && tr.status !== 'retired')
      .map((tr) => ({ slug: tr.slug, name: tr.name }))
  } catch (e) {
    paramsError.value = apiErrorText(e, t('baselineHub.admin.params.loadFailed'))
  } finally {
    paramsLoading.value = false
  }
}

function paramsBody(dryRun: boolean) {
  return {
    base_yaml: paramsBase.value,
    yaml: paramsText.value,
    tracks: paramsTargets.value,
    comparable: paramsComparable.value,
    note: paramsNote.value,
    dry_run: dryRun,
  }
}

/** A dry run: the same refusals, the same param-name check against LLMBench,
 *  and per track what it would actually move — against that track's own
 *  current values, which is not always the same as this one's. */
async function previewParams() {
  paramsBusy.value = true
  paramsError.value = ''
  paramsPreview.value = null
  try {
    const { data } = await api.post<ParamsRollout>(
      '/baseline-hub/params-rollout', paramsBody(true))
    paramsPreview.value = data
  } catch (e) {
    paramsError.value = apiErrorText(e, t('baselineHub.admin.params.previewFailed'))
  } finally {
    paramsBusy.value = false
  }
}

async function applyParams() {
  if (!paramsComparable.value) {
    try {
      await ElMessageBox.confirm(
        t('baselineHub.admin.params.unrankConfirm'),
        t('baselineHub.admin.params.unrankTitle'),
        { confirmButtonText: t('baselineHub.admin.params.unrankAction'),
          cancelButtonText: t('common.cancel'), type: 'warning' },
      )
    } catch {
      return
    }
  }
  paramsBusy.value = true
  paramsError.value = ''
  try {
    const { data } = await api.post<ParamsRollout>(
      '/baseline-hub/params-rollout',
      { ...paramsBody(false), confirm: !paramsComparable.value })
    paramsPreview.value = data
    const done = data.tracks.filter((r) => r.ok).length
    if (data.stopped_at) {
      ElMessage.error(t('baselineHub.admin.params.stopped',
        { slug: data.stopped_at, n: done }))
    } else {
      ElMessage.success(t('baselineHub.admin.params.applied', { n: done }))
      paramsOpen.value = false
      await load()
      await loadLive()
    }
  } catch (e) {
    paramsError.value = apiErrorText(e, t('baselineHub.admin.params.applyFailed'))
  } finally {
    paramsBusy.value = false
  }
}

/** Replacing the benchmark is the one rule that can be changed rather than
 *  only re-read — and it costs the board its comparability, so the dialog
 *  says how many rows stop ranking before it will submit. Re-binding the
 *  benchmark the track already has is the exception: that only re-reads the
 *  live config hash, which is how a deliberate drift is accepted. */
function openBenchmark() {
  const t = track.value
  if (!t) return
  bench.value = {
    mode: t.benchmark_slug ? 'bind' : 'create',
    template: t.template || templates.value[0]?.name || '',
    bind_benchmark_slug: t.benchmark_slug,
    confirm: false,
  }
  benchOpen.value = true
}

/** Does this dialog point the track at something else than it has now? */
const benchChanges = computed(() => {
  const t = track.value
  if (!t) return false
  if (bench.value.mode === 'bind') return bench.value.bind_benchmark_slug.trim() !== t.benchmark_slug
  return true  // a fresh benchmark is always a different one
})
const benchLosses = computed(() =>
  benchChanges.value ? (track.value?.measured_count ?? 0) : 0,
)

/** What the benchmark runs, as LLMBench holds it now — the throughput
 *  scenarios and the modules that gate on something else. The template is
 *  only a fallback: a bound benchmark has no template, and a live one can
 *  have moved on from the template it was made from. */
const liveScenarios = computed(() => (live.value?.modules ?? []).filter((m) => m.is_scenario))
const liveQuality = computed(() => (live.value?.modules ?? []).filter((m) => !m.is_scenario))
function moduleGates(key: string): string {
  const mod = (live.value?.modules ?? []).find((m) => m.key === key)
  return (mod?.metrics ?? [])
    .filter((metric) => metric.role === 'redline')
    .map((metric) =>
      metric.min_val != null ? `${metric.key} ≥ ${metric.min_val}`
        : metric.max_val != null ? `${metric.key} ≤ ${metric.max_val}` : metric.key,
    )
    .join(', ')
}

/** Candidates: every metric the live benchmark declares, quality modules
 *  first — a throughput module's metrics are already the board's own columns. */
const columnCandidates = computed(() => {
  const mods = [...(live.value?.modules ?? [])].sort(
    (a, b) => Number(a.is_scenario) - Number(b.is_scenario) || a.order_index - b.order_index,
  )
  return mods.map((m) => ({
    key: m.key,
    module_name: m.module_name,
    is_scenario: m.is_scenario,
    metrics: m.metrics.map((metric) => ({
      key: `${m.key}.${metric.key}`,
      name: metric.key,
      role: metric.role,
      gate: metric.min_val != null ? `≥ ${metric.min_val}`
        : metric.max_val != null ? `≤ ${metric.max_val}` : '',
    })),
  }))
})

function openColumns() {
  const t = track.value
  chosenColumns.value = (t?.board_columns ?? []).map((c) => c.key)
  columnLabels.value = Object.fromEntries(
    (t?.board_columns ?? []).map((c) => [c.key, c.label]),
  )
  columnsOpen.value = true
}

async function saveColumns() {
  columnsBusy.value = true
  try {
    await api.patch(`/baseline-hub/tracks/${slug.value}`, {
      board_columns: chosenColumns.value.map((key) => ({
        key,
        label: (columnLabels.value[key] ?? '').trim(),
      })),
    })
    columnsOpen.value = false
    await load()
  } catch (error: any) {
    ElMessage.error(apiErrorText(error, t('baselineHub.msg.couldNotSaveColumns')))
  } finally {
    columnsBusy.value = false
  }
}

async function saveBenchmark() {
  benchBusy.value = true
  try {
    const body = bench.value.mode === 'bind'
      ? {
          mode: 'bind',
          bind_benchmark_slug: bench.value.bind_benchmark_slug.trim(),
          confirm: bench.value.confirm,
        }
      : { mode: 'create', template: bench.value.template, confirm: bench.value.confirm }
    const { data } = await api.post(`/baseline-hub/tracks/${slug.value}/benchmark`, body)
    ElMessage.success(t('baselineHub.msg.measuresAgainst', { slug: data.benchmark_slug }))
    benchOpen.value = false
    await load()
    await loadLive()
  } catch (error: any) {
    ElMessage.error(apiErrorText(error, t('baselineHub.msg.couldNotChangeBenchmark')))
  } finally {
    benchBusy.value = false
  }
}

async function saveEdit() {
  editBusy.value = true
  try {
    await api.patch(`/baseline-hub/tracks/${slug.value}`, {
      model_path: edit.value.model_path.trim() || undefined,
      images: edit.value.images
        .filter((i) => i.image.trim())
        .map((i) => ({ engine: i.engine, image: i.image.trim(), label: i.label.trim() })),
      cookbook_url: edit.value.cookbook_url.trim(),
      notes: edit.value.notes,
      max_run_minutes: edit.value.max_run_minutes,
      ranking_metric: edit.value.ranking_metric.trim(),
      // 0 unbinds. LLMBench holds the binding, so this has to be sent even
      // when it is being cleared — the board would keep forwarding otherwise.
      contest_benchmark_id: edit.value.contest_benchmark_id || 0,
      // 0 clears the pin: entries may again land on any machine of the card type.
      pinned_machine_id: edit.value.pinned_machine_id || 0,
    })
    const e = edit.value
    const before = track.value?.slo
    const gateChanged = !!before && (
      e.ttft_ms !== before.ttft_ms
      || e.ttft_percentile !== before.ttft_percentile
      || e.min_request_output_tps !== before.min_request_output_tps
    )
    if (gateChanged) {
      // Its own endpoint, and its own confirmation: this rewrites the
      // benchmark on LLMBench and stops every existing row from ranking.
      await ElMessageBox.confirm(
        t('baselineHub.admin.edit.gateConfirm'),
        t('baselineHub.admin.edit.confirmGate'),
        { type: 'warning' },
      )
      await api.post(`/baseline-hub/tracks/${slug.value}/gate`, {
        gate: {
          ttft_ms: e.ttft_ms,
          ttft_percentile: e.ttft_percentile,
          min_request_output_tps: e.min_request_output_tps,
        },
        confirm: true,
      })
      ElMessage.success(t('baselineHub.admin.edit.gateChanged'))
    }
    editOpen.value = false
    await load()
  } catch (error: any) {
    if (error === 'cancel' || error === 'close') return
    ElMessage.error(apiErrorText(error, t('baselineHub.msg.couldNotSave')))
  } finally {
    editBusy.value = false
  }
}

// ---------------------------------------------------------- model-catalog export

const catalogOpen = ref(false)
/** Own measured entries and the administrator's shared baseline. */
const catalogCandidates = computed<CatalogCandidate[]>(() => {
  const seen = new Set<number>()
  return (track.value?.board ?? []).flatMap((r) => {
    if (!ownsSubmission(r) && !r.is_reference) return []
    if (!r.run_id || seen.has(r.run_id)) return []
    seen.add(r.run_id)
    return [{
      run_id: r.run_id,
      label: r.version > 1 ? `${r.name} v${r.version}` : r.name,
      detail: r.rank ? `#${r.rank}` : '',
    }]
  })
})
/** Opens with the track's pair ticked: the same baseline and optimized entry
 *  a report on this track compares (the Hub picks them, see pair.py), so the
 *  export finds that report. */
const catalogInitial = computed(() => {
  const pair = track.value?.pair
  const picks: { run_id: number; role: string }[] = []
  if (pair?.baseline_run_id) picks.push({ run_id: pair.baseline_run_id, role: 'baseline' })
  if (pair?.optimized_run_id) picks.push({ run_id: pair.optimized_run_id, role: 'optimized' })
  return picks
})

// ---------------------------------------------------------- baseline

/** The track's confirmed baseline, if any — typed narrowly because the API
 *  sends `{}` until one exists. */
const reference = computed(() => {
  const r = track.value?.reference
  return r && 'measurement_id' in r ? r : null
})
const floorsList = computed(() =>
  Object.entries(track.value?.quality_floors ?? {}).map(([key, min]) => ({
    key, name: key.split('.').pop() ?? key, module: key.split('.')[0], min,
  })),
)
function floorsOf(moduleKey: string) {
  return floorsList.value.filter((f) => f.module === moduleKey)
}

// ---------------------------------------------------------- where the weights are

const weightsRows = computed(() => track.value?.weights ?? [])
const weightsSummary = computed(() => {
  const rows = weightsRows.value
  if (!rows.length) return ''
  if (rows.some((w) => w.state === 'checking')) return t('baselineHub.weights.checkingShort')
  const on = rows.filter((w) => w.state === 'present').length
  return t('baselineHub.weights.summary', { n: on, of: rows.length })
})
function weightsType(state: string) {
  return state === 'present' ? 'success' : state === 'absent' ? 'danger' : 'info'
}
/** On a machine of several nodes: which of them hold the weights. */
function weightsNodes(w: MachineWeights): string {
  if (w.state === 'absent') return w.nodes.find((n) => n.detail)?.detail ?? ''
  if (w.nodes.length < 2) return ''
  const on = w.nodes.filter((n) => n.present).map((n) => n.node)
  return t('baselineHub.weights.onNodes', { nodes: on.join(', '), n: on.length, of: w.nodes.length })
}
async function recheckWeights() {
  try {
    await api.post(`/baseline-hub/tracks/${slug.value}/weights/check`)
    await load()
  } catch (error) {
    ElMessage.error(apiErrorText(error, t('baselineHub.weights.recheckFailed')))
  }
}
/** A baseline can be confirmed when it passed, or missed only a quality
 *  minimum (its own scores then set the floors); the server has the last word. */
function confirmable(row: TrackBoardRow): boolean {
  if (row.passed) return true
  const reason = row.unranked_reason ?? ''
  return reason.startsWith('did not pass:') && reason.includes(' < ')
}

function openReference(row: TrackBoardRow) {
  refRow.value = row
  refMargin.value = reference.value?.margin_points ?? 5
  refPlan.value = null
  refError.value = ''
  refOpen.value = true
  void planReference()
}
let planSeq = 0
async function planReference() {
  const row = refRow.value
  if (!row) return
  const seq = ++planSeq
  refPlanning.value = true
  refError.value = ''
  try {
    const { data } = await api.post(`/baseline-hub/tracks/${slug.value}/reference`, {
      measurement_id: row.measurement_id, margin_points: refMargin.value, dry_run: true,
    })
    if (seq === planSeq) refPlan.value = data
  } catch (error: any) {
    if (seq === planSeq) {
      refPlan.value = null
      refError.value = apiErrorText(error, t('baselineHub.admin.reference.failed'))
    }
  } finally {
    if (seq === planSeq) refPlanning.value = false
  }
}
async function confirmReference() {
  const row = refRow.value
  if (!row) return
  refBusy.value = true
  try {
    await api.post(`/baseline-hub/tracks/${slug.value}/reference`, {
      measurement_id: row.measurement_id, margin_points: refMargin.value,
    })
    ElMessage.success(t('baselineHub.admin.reference.done'))
    refOpen.value = false
    await Promise.all([load(), loadLive()])
  } catch (error: any) {
    ElMessage.error(apiErrorText(error, t('baselineHub.admin.reference.failed')))
  } finally {
    refBusy.value = false
  }
}
function fmtScore(v: number | null | undefined): string {
  if (v == null) return '—'
  return Math.abs(v) >= 100 ? v.toFixed(0) : v.toPrecision(3)
}

const page = useFirstLoad(load)
const poll = usePoll(load, 10000)
usePageScroll(() => !!track.value)
const pendingRows = useRowLink((row: BaselineSubmission) => `/baseline-hub/${row.id}`)
onMounted(async () => {
  // The page's own data first; the lists only its dialogs use come alongside.
  api.get('/machines/gpu-types')
    .then(({ data }) => (gpuTypes.value = data.gpu_types))
    .catch(() => (gpuTypes.value = []))
  api.get('/baseline-hub/flag-catalog')
    .then(({ data }) => (flagCatalog.value = data.engines))
    .catch(() => (flagCatalog.value = {}))
  if (!auth.user) void auth.fetchMe().catch(() => undefined)
  await Promise.all([page.run(), loadLive(), loadTemplates()])
  poll.start()
})
</script>

<template>
  <div v-if="track" class="page">
    <BackLink :to="trackListUrl" :label="t('baselineHub.back')" />
    <div class="header-row">
      <div>
        <h1 class="page-title">
          {{ track.name }}
          <el-tag :type="trackStatusType(track.status)" class="version" effect="plain">
            {{ statusLabel(track.status, locale) }}</el-tag>
        </h1>
        <span class="muted">
          {{ track.model_name }} · {{ track.gpu_type }}
          <template v-if="showPrecision"> · {{ track.precision }}</template>
          <template v-if="track.opened_at"> · {{ locale === 'zh' ? '开放于' : 'opened' }} {{ relativeTime(track.opened_at) }}</template>
          <template v-if="track.retired_at"> · {{ locale === 'zh' ? '退役于' : 'retired' }} {{ relativeTime(track.retired_at) }}</template>
        </span>
      </div>
      <div class="actions">
        <el-button v-if="track.status === 'open'" type="primary" @click="openEntry">
          {{ t('baselineHub.enterTrack') }}
        </el-button>
        <el-button v-if="catalogCandidates.length" @click="catalogOpen = true">
          {{ t('catalogExport.menu') }}
        </el-button>
        <el-dropdown v-if="isAdmin" trigger="click" @command="onAdmin">
          <el-button>{{ t('baselineHub.admin.menu') }} ▾</el-button>
          <template #dropdown>
            <el-dropdown-menu>
              <el-dropdown-item v-if="track.status === 'draft'" command="retry">
                {{ t('baselineHub.admin.retryBenchmark') }}</el-dropdown-item>
              <el-dropdown-item command="edit">
                {{ t('baselineHub.admin.editTrack') }}</el-dropdown-item>
              <el-dropdown-item command="benchmark">
                {{ t('baselineHub.admin.changeBenchmark') }}</el-dropdown-item>
              <el-dropdown-item command="columns" :disabled="!track.benchmark_slug">
                {{ t('baselineHub.admin.chooseColumns') }}</el-dropdown-item>
              <el-dropdown-item command="params" :disabled="!track.benchmark_managed">
                {{ t('baselineHub.admin.editParams') }}</el-dropdown-item>
              <el-dropdown-item command="redlines" :disabled="!track.benchmark_managed">
                {{ t('baselineHub.admin.editRedlines') }}</el-dropdown-item>
              <el-dropdown-item command="adopt" :disabled="!track.benchmark_slug">
                {{ t('baselineHub.admin.adopt') }}</el-dropdown-item>
              <el-dropdown-item v-if="track.status === 'open'" command="retire" divided>
                {{ t('baselineHub.admin.retire') }}</el-dropdown-item>
              <el-dropdown-item v-if="track.status === 'retired'" command="reopen" divided>
                {{ t('baselineHub.admin.reopen') }}</el-dropdown-item>
            </el-dropdown-menu>
          </template>
        </el-dropdown>
      </div>
    </div>

    <el-alert v-if="track.status === 'draft'" type="warning" :closable="false" class="block">
      <b>{{ t('baselineHub.admin.draftAlert') }}</b>
      {{ track.benchmark_error || t('baselineHub.admin.draftWaiting') }}
      <template v-if="isAdmin">{{ t('baselineHub.admin.draftRetryHint') }}</template>
    </el-alert>
    <el-alert v-if="live?.drifted" type="error" :closable="false" class="block">
      <b>{{ t('baselineHub.admin.driftAlert') }}</b> {{ t('baselineHub.admin.driftSince') }}
      <span class="mono">{{ live.recorded_config_hash }}</span> →
      <span class="mono">{{ live.live?.config_hash }}</span>).
      {{ t('baselineHub.admin.driftNotComparable') }}
      <template v-if="isAdmin">
        <a class="link" @click="openBenchmark">{{ t('baselineHub.admin.driftRebind') }}</a>
        {{ t('baselineHub.admin.driftRebindTail') }}
      </template>
    </el-alert>

    <!-- The track at a glance; the full rules are a tab down. -->
    <p class="scoring-summary"><span>{{ locale === 'zh' ? '排名口径' : 'Ranking' }}</span> {{ rankedBy }} <span class="scoring-gate">{{ track.gate_text }}</span></p>
    <details class="disclosure track-overview">
      <summary>{{ locale === 'zh' ? '赛道概览与评测口径' : 'Track overview and scoring' }}</summary>
    <div class="facts">
      <FactTile :icon="Cpu" :label="t('baselineHub.fact.model')">
        {{ track.served_model_name }}
        <template #sub>{{ track.gpu_type }}<template v-if="track.precision">
          · {{ track.precision }}</template><template v-if="weightsSummary">
          · {{ weightsSummary }}</template></template>
        <template #tip>{{ track.model_path }}<template v-for="i in track.images" :key="i.image">
{{ i.image }}</template></template>
      </FactTile>
      <FactTile :icon="Aim" :label="t('baselineHub.fact.gate')">
        <span class="mono">{{ track.gate_text }}</span>
        <template #sub>{{ rankedByShort }}</template>
        <template #tip>{{ t('baselineHub.rules.gateHint') }}

{{ t('baselineHub.rules.rankedBy') }}: {{ rankedBy }}</template>
      </FactTile>
      <FactTile :icon="Document" :label="t('baselineHub.fact.benchmark')">
        <a v-if="live?.url" class="link mono" :href="live.url" target="_blank" rel="noopener">
          {{ track.benchmark_slug || '—' }}</a>
        <span v-else class="mono">{{ track.benchmark_slug || '—' }}</span>
        <template v-if="track.benchmark_slug" #sub>{{ track.benchmark_locked
          ? t('baselineHub.rules.locked') : t('baselineHub.rules.unlocked') }} ·
          {{ track.benchmark_managed ? t('baselineHub.rules.managed')
            : t('baselineHub.rules.bound') }}</template>
        <template v-if="track.benchmark_config_hash" #tip>{{
          t('baselineHub.rules.hash', { hash: track.benchmark_config_hash }) }}</template>
      </FactTile>
      <FactTile :icon="Trophy" :label="t('baselineHub.fact.entries')">
        {{ t('baselineHub.fact.ranked', { n: rankedCount }) }}
        <template #sub>{{ t('baselineHub.fact.entriesSub',
          { measured: track.board.length, pending: pending.length }) }}</template>
      </FactTile>
      <FactTile :icon="Medal" :label="t('baselineHub.fact.baseline')">
        <router-link v-if="reference" class="link nav-link"
          :to="`/baseline-hub/${reference.submission_id}`">{{ reference.name }}</router-link>
        <span v-else class="muted">{{ t('baselineHub.fact.noBaseline') }}</span>
        <template v-if="reference" #sub>{{ t('baselineHub.rules.referenceMargin',
          { margin: reference.margin_points }) }}</template>
        <template v-if="!reference" #tip>{{ t('baselineHub.rules.noReferenceHint') }}</template>
      </FactTile>
      <FactTile :icon="Timer" :label="t('baselineHub.fact.budget')">
        {{ t('baselineHub.rules.perEntry', { n: track.max_run_minutes }) }}
        <template v-if="track.cookbook_url" #sub>
          <a class="link" :href="track.cookbook_url" target="_blank" rel="noopener">
            {{ t('baselineHub.rules.cookbook') }}</a></template>
      </FactTile>
    </div>

    </details>

    <!-- The answer the track exists to give, with the command that reproduces it. -->
    <el-card v-if="winner" class="block winner-card" shadow="never">
      <div class="winner-grid">
        <div class="winner-left">
          <div class="eyebrow">{{ t('baselineHub.winner.eyebrow') }}</div>
          <div class="winner-name">
            <router-link class="link nav-link" :to="`/baseline-hub/${winner.submission_id}`">
              {{ winner.name }}</router-link>
            <el-tag v-if="winner.version > 1" size="small" effect="plain" class="version">
              v{{ winner.version }}</el-tag>
          </div>
          <div class="hero-num mono">{{ rankValue(winner.value) }}</div>
          <div class="muted hero-label">
            {{ rankingLabel }}
            <InfoHint :width="360">{{ rankedBy }}</InfoHint>
          </div>
          <div v-if="hasQualityChecks" class="quality-line">
            <span class="muted">{{ t('baselineHub.board.quality') }}</span>
            <el-button link :type="qualityVerdict(winner.quality) === 'failed' ? 'danger' : 'primary'" @click="selectedConfig = winner">{{ qualityLabel(winner) }}<template v-if="winner.quality.length"> · {{ winner.quality.length }}</template></el-button>
          </div>
          <PluginSlot name="hub.track.winner" :props="{ track, row: winner }" />
          <div class="muted tiny-line">
            {{ t('baselineHub.winner.by', { who: winner.submitted_by || '—' }) }} ·
            {{ winner.gpu_count }}×{{ track.gpu_type }}
            <template v-if="winner.machine_name"> · {{ winner.machine_name }}</template> ·
            <span :title="exactTime(winner.measured_at)">{{ relativeTime(winner.measured_at) }}</span>
            <template v-if="rankedCount > 1"> ·
              {{ t(rankedCount === 2 ? 'baselineHub.winner.aheadOne' : 'baselineHub.winner.aheadMany',
                { n: rankedCount - 1 }) }}</template>
          </div>
        </div>
        <div class="winner-right winner-actions">
          <el-button @click="selectedConfig = winner">{{ locale === 'zh' ? '查看配置与结果' : 'View configuration and results' }}</el-button>
          <el-button text type="primary" @click="copyConfig(winner)">{{ t('baselineHub.winner.copy') }}</el-button>
        </div>
      </div>
    </el-card>
    <el-card v-else class="block" shadow="never">
      <span v-if="!track.board.length" class="muted">{{ t('baselineHub.winner.none') }}</span>
      <span v-else class="muted">
        {{ t('baselineHub.winner.nothingRanks', { n: track.board.length }) }}
        <b>{{ track.board[0].unranked_reason }}</b>. {{ t('baselineHub.winner.eachReason') }}
      </span>
    </el-card>


    <el-drawer v-model="configOpen" :title="selectedConfig?.name" size="min(680px, 100vw)" class="configuration-drawer">
      <template v-if="selectedConfig">
        <div class="drawer-summary"><strong>{{ rankValue(selectedConfig.value) }} {{ rankingLabel }}</strong><span :class="selectedConfig.rank ? 'link' : 'missed'">{{ selectedConfig.rank ? t('baselineHub.board.meetsGate') : selectedConfig.unranked_reason }}</span></div>
        <p v-if="selectedConfig.stale_benchmark" class="stale-label">{{ locale === 'zh' ? '此结果来自旧评测口径，不可与当前结果直接比较。' : 'This result uses an older benchmark and is not directly comparable.' }}</p>
        <section class="drawer-section"><h2>{{ t('baselineHub.winner.config') }}</h2>
          <div class="drawer-actions"><el-button type="primary" @click="copyConfig(selectedConfig)">{{ t('baselineHub.winner.copy') }}</el-button><router-link :to="`/baseline-hub/${selectedConfig.submission_id}`" class="link">{{ locale === 'zh' ? '打开提交详情' : 'Open submission' }}</router-link></div>
          <p class="drawer-image mono">{{ selectedConfig.image }} <CopyButton :value="selectedConfig.image" /></p>
          <FlagChips :args="selectedConfig.engine_args" :known="knownFlags" />
          <details class="command"><summary>{{ selectedConfig.launch_command ? t('baselineHub.winner.exact') : t('baselineHub.winner.declared') }}</summary><pre class="mono cmd">{{ configText(selectedConfig) }}</pre></details>
          <details v-if="Object.keys(selectedConfig.extra_env).length || Object.keys(selectedConfig.extra_volumes).length" class="command"><summary>{{ locale === 'zh' ? '环境与挂载' : 'Environment and volumes' }}</summary><pre class="mono cmd">{{ JSON.stringify({ env: selectedConfig.extra_env, volumes: selectedConfig.extra_volumes }, null, 2) }}</pre></details>
        </section>
        <section v-if="selectedConfig.scenarios.length" class="drawer-section"><h2>{{ locale === 'zh' ? '场景结果' : 'Scenario results' }}</h2>
          <div v-for="item in selectedConfig.scenarios" :key="item.key" class="drawer-scenario"><b>{{ item.label }}</b><span>{{ fmtTpm(item.tpm_card_norm) }} TPM@8</span><div class="muted">{{ ttftLabel }} {{ fmtSec(item.ttft_ms) }} · {{ fmt1(item.request_output_tps) }} tok/s · {{ t('baselineHub.winner.concurrent', { n: fmtConc(item.concurrency) }) }}</div></div>
        </section>
        <section v-if="hasQualityChecks" class="drawer-section"><h2>{{ t('baselineHub.detail.quality') }}</h2>
          <p v-if="!selectedConfig.quality.length" class="muted">{{ locale === 'zh' ? '此结果未返回质量报告。' : 'No quality report was returned for this result.' }}</p>
          <div v-for="cell in selectedConfig.quality" :key="cell.key" class="drawer-quality"><div><b>{{ cell.label }}</b><span :class="cell.ok === false ? 'missed' : ''">{{ cell.kind === 'metric' ? qualityText(cell) : cell.ok === true ? t('baselineHub.board.qualityPass') : cell.ok === false ? t('baselineHub.board.qualityFail') : '—' }}</span></div><p class="muted">{{ qualityGate(cell) }}</p></div>
        </section>
      </template>
    </el-drawer>

    <el-tabs v-model="tab">
      <el-tab-pane name="board">
        <template #label>{{ t('baselineHub.board.tab') }}
          <span class="count">{{ track.board.length }}</span></template>
        <div class="tabhead">
          <p class="muted tabnote">
            {{ track.ranking_metric.endsWith('tpm_card_norm') ? t('baselineHub.board.note', { n: track.measured_count }) : t('baselineHub.measuredCount', { n: track.measured_count }) }}
          </p>
          <div class="tabhead-actions">
            <el-checkbox v-model="showBoardDetails">{{ locale === 'zh' ? '详细指标' : 'Detailed metrics' }}</el-checkbox>
            <el-checkbox v-if="staleRows.length" v-model="hideStale" @update:model-value="resetBoardPage" size="small">
              {{ t('baselineHub.board.hideStale', { n: staleRows.length }) }}
              <InfoHint :width="360">{{ t('baselineHub.board.hideStaleHint') }}</InfoHint>
            </el-checkbox>
          </div>
        </div>
        <p v-if="performanceOnly" class="muted quality-not-configured">{{ locale === 'zh' ? '本赛道未配置输出质量检查，仅评测性能。' : 'This track evaluates performance; no output quality checks are configured.' }}</p>
        <div class="board-toolbar">
          <el-input v-model="boardSearch" clearable @update:model-value="resetBoardPage" :aria-label="locale === 'zh' ? '搜索提交' : 'Search entries'" :placeholder="locale === 'zh' ? '搜索配置、提交人或引擎' : 'Search configurations, contributors or engines'" />
          <el-select v-if="scenarioLabels.length > 1 && !showBoardDetails" v-model="selectedScenario" clearable :aria-label="locale === 'zh' ? '对比场景' : 'Compare scenario'" :placeholder="locale === 'zh' ? '对比场景（可选）' : 'Compare a scenario (optional)'">
            <el-option v-for="label in scenarioLabels" :key="label" :label="label" :value="label" />
          </el-select>
        </div>
        <div v-if="!showBoardDetails" class="mobile-board">
          <article v-for="row in pagedBoard" :key="row.measurement_id" class="mobile-result">
            <div class="mobile-result-heading"><span class="mobile-rank">{{ row.rank || '—' }}</span><router-link :to="`/baseline-hub/${row.submission_id}`" class="nav-link">{{ row.name }}</router-link><span v-if="row.version > 1" class="muted">v{{ row.version }}</span></div>
            <div class="mobile-result-score"><strong>{{ rankValue(row.value) }}</strong><span class="muted">{{ rankedByShort }}</span></div>
            <p v-if="!row.rank" class="missed">{{ row.unranked_reason }}</p>
            <p v-if="row.stale_benchmark" class="stale-label">{{ locale === 'zh' ? '旧评测口径，不可直接比较' : 'Older benchmark; not directly comparable' }}</p>
            <div class="mobile-result-footer"><span :class="row.rank ? 'link' : 'missed'">{{ t(row.rank ? 'baselineHub.board.meetsGate' : 'baselineHub.detail.notPassed') }}</span><span class="muted">{{ relativeTime(row.measured_at) }}</span></div>
            <div class="mobile-result-actions">
              <el-button text type="primary" @click="selectedConfig = row">{{ locale === 'zh' ? '查看详情' : 'View details' }}</el-button>
              <el-button v-if="ownsSubmission(row) && track.status === 'open'" text type="primary" @click="evolveRow(row)">{{ t('baselineHub.evolve') }}</el-button>
              <el-button v-if="isAdmin && track.benchmark_managed && confirmable(row) && !row.stale_benchmark" text @click="openReference(row)">{{ t('baselineHub.admin.confirmReference') }}</el-button>
            </div>
          </article>
          <p v-if="!filteredBoard.length" class="muted">{{ boardEmptyText }}</p>
        </div>
        <el-table :data="pagedBoard" row-key="measurement_id" size="small" class="board-table" :class="{ expanded: showBoardDetails }"
          :empty-text="boardEmptyText">
          <el-table-column label="#" width="58">
            <template #default="{ row }">
              <span v-if="row.rank && row.rank <= 3" class="medal" :class="`medal-${row.rank}`">
                {{ row.rank }}</span>
              <span v-else-if="row.rank">{{ row.rank }}</span>
              <el-tooltip v-else :content="row.unranked_reason">
                <span class="muted">—</span>
              </el-tooltip>
            </template>
          </el-table-column>
          <el-table-column :label="t('baselineHub.entry')" min-width="190">
            <template #default="{ row }">
              <router-link class="link nav-link" :to="`/baseline-hub/${row.submission_id}`">{{ row.name }}</router-link>
              <el-tag v-if="row.version > 1" size="small" effect="plain" class="version">v{{ row.version }}</el-tag>
              <el-tooltip v-if="row.is_reference" :content="t('baselineHub.board.referenceHint')">
                <el-tag size="small" type="success" class="version">
                  {{ t('baselineHub.board.reference') }}</el-tag>
              </el-tooltip>
              <div class="muted tiny">{{ row.submitted_by }}<template v-if="showBoardDetails && row.machine_name"> · {{ row.machine_name }}</template></div>
              <div v-if="!row.rank" class="missed tiny">{{ row.unranked_reason }}</div>
              <span v-if="row.stale_benchmark" class="stale-label">{{ locale === 'zh' ? '旧评测口径，不可直接比较' : 'Older benchmark; not directly comparable' }}</span>
            </template>
          </el-table-column>
          <el-table-column :label="rankingLabel" min-width="140" align="right">
            <template #default="{ row }"><strong class="ranking-value">{{ rankValue(row.value) }}</strong></template>
          </el-table-column>
          <el-table-column v-if="hasQualityChecks && (!showBoardDetails || !qualityColumns.length)" :label="t('baselineHub.board.quality')" width="110">
            <template #default="{ row }"><el-button link :type="qualityVerdict(row.quality) === 'failed' ? 'danger' : 'primary'" @click="selectedConfig = row">{{ qualityLabel(row) }}</el-button></template>
          </el-table-column>
          <el-table-column v-for="label in visibleScenarioLabels" :key="label"
            :label="t('baselineHub.board.tpm', { label })" width="140" align="right">
            <template #default="{ row }">
              <span class="mono" :class="scenario(row, label)?.meets_gate === false ? 'missed' : ''">
                {{ fmtTpm(scenario(row, label)?.tpm_card_norm) }}</span>
            </template>
          </el-table-column>
          <el-table-column v-if="showBoardDetails" :label="t('baselineHub.board.perRequest')" width="130" align="right">
            <template #default="{ row }"><span class="mono">{{ fmt1(first(row)?.request_output_tps) }}</span></template>
          </el-table-column>
          <el-table-column v-if="showBoardDetails" :label="ttftLabel" width="100" align="right">
            <template #default="{ row }"><span class="mono">{{ fmtSec(first(row)?.ttft_ms) }}</span></template>
          </el-table-column>
          <el-table-column v-for="col in (showBoardDetails ? qualityColumns : [])" :key="col.key"
            :label="col.header" :width="col.kind === 'module' ? 110 : 110"
            :align="col.kind === 'module' ? 'center' : 'right'">
            <template #default="{ row }">
              <el-tooltip :content="qualityGate(quality(row, col.key))"
                :disabled="!quality(row, col.key)">
                <template v-if="col.kind === 'module'">
                  <el-tag v-if="quality(row, col.key)?.ok === true" type="success" size="small"
                    effect="plain">{{ t('baselineHub.board.qualityPass') }}</el-tag>
                  <el-tag v-else-if="quality(row, col.key)?.ok === false" type="danger"
                    size="small">{{ t('baselineHub.board.qualityFail') }}</el-tag>
                  <span v-else class="muted">—</span>
                </template>
                <span v-else class="mono"
                  :class="quality(row, col.key)?.ok === false ? 'missed' : ''">
                  {{ qualityText(quality(row, col.key)) }}</span>
              </el-tooltip>
            </template>
          </el-table-column>
          <el-table-column v-if="showBoardDetails" :label="t('baselineHub.board.conc')" width="70" align="right">
            <template #default="{ row }"><span class="mono">{{ fmtConc(first(row)?.concurrency) }}</span></template>
          </el-table-column>
          <el-table-column v-if="showBoardDetails" :label="t('baselineHub.cards')" width="70" align="right">
            <template #default="{ row }">{{ row.gpu_count }}</template>
          </el-table-column>
          <el-table-column v-if="showBoardDetails" :label="t('baselineHub.image')" min-width="150">
            <template #default="{ row }">
              <span class="mono tiny-mono" :title="row.image">{{ shortImage(row.image) }}</span>
              <div v-if="row.engine_version" class="muted tiny">{{ row.engine }} {{ row.engine_version }}</div>
            </template>
          </el-table-column>
          <el-table-column v-if="showBoardDetails" :label="t('baselineHub.board.verdict')" min-width="170">
            <template #default="{ row }">
              <el-tag v-if="row.rank" type="success" size="small" effect="plain">
                {{ t('baselineHub.board.meetsGate') }}</el-tag>
              <el-tooltip v-else :content="row.unranked_reason">
                <el-tag type="danger" size="small">{{ row.unranked_reason.split(':')[0].split(' (')[0] }}</el-tag>
              </el-tooltip>
            </template>
          </el-table-column>
          <el-table-column :label="t('baselineHub.board.measured')" width="110">
            <template #default="{ row }">
              <span :title="exactTime(row.measured_at)">{{ relativeTime(row.measured_at) }}</span>
            </template>
          </el-table-column>
          <el-table-column label="" :width="isAdmin && track.benchmark_managed ? 240 : 150">
            <template #default="{ row }">
              <el-button size="small" text @click="selectedConfig = row">{{ locale === 'zh' ? '详情' : 'Details' }}</el-button>
              <el-button v-if="ownsSubmission(row) && track.status === 'open'" size="small" text type="primary"
                @click.stop="evolveRow(row)">{{ t('baselineHub.evolve') }}</el-button>
              <el-button v-if="isAdmin && track.benchmark_managed && confirmable(row) && !row.stale_benchmark"
                size="small" text :type="row.is_reference ? 'info' : 'success'"
                @click.stop="openReference(row)">{{ t('baselineHub.admin.confirmReference') }}</el-button>
            </template>
          </el-table-column>
        </el-table>
        <el-pagination v-if="filteredBoard.length > 20" v-model:current-page="boardPage" v-model:page-size="boardPageSize" @size-change="resetBoardPage" :total="filteredBoard.length" :page-sizes="[20, 50, 100]" layout="prev, pager, next, sizes" class="board-pagination" />
      </el-tab-pane>

      <el-tab-pane name="pending">
        <template #label>{{ t('baselineHub.board.pending') }}
          <span class="count">{{ pending.length }}</span></template>
        <p class="muted tabnote">{{ t('baselineHub.board.pendingNote') }}</p>
        <div class="mobile-pending">
          <article v-for="entry in pending" :key="entry.id" class="mobile-result">
            <div class="mobile-result-heading"><router-link :to="`/baseline-hub/${entry.id}`" class="nav-link">{{ entry.name }}</router-link><el-tag :type="statusType(entry.status)" effect="plain">{{ statusLabel(entry.status, locale) }}</el-tag></div>
            <p v-if="entry.status === 'queued'" class="muted">{{ inLine(entry) }}<br>{{ entry.queued_reason }}</p>
            <p v-else-if="entry.status === 'failed'" class="missed">{{ entry.failure_class }} {{ entry.error }}</p>
            <p v-else-if="entry.run_status" class="muted">{{ entry.run_status }}</p>
            <div class="mobile-result-footer"><span class="muted">{{ entry.gpu_count }} × {{ entry.gpu_type || track.gpu_type }}</span><span class="muted">{{ relativeTime(entry.created_at) }}</span></div>
            <div class="mobile-result-actions">
              <el-button v-if="ownsSubmission(entry) && ['queued', 'running'].includes(entry.status)" text type="danger" @click="abort(entry)">{{ t('baselineHub.abort') }}</el-button>
              <el-button v-if="ownsSubmission(entry) && track.status === 'open'" text type="primary" @click="openResubmit(entry)">{{ t('baselineHub.evolve') }}</el-button>
            </div>
          </article>
          <p v-if="!pending.length" class="muted">{{ t('baselineHub.board.pendingEmpty') }}</p>
        </div>
        <el-table :data="pending" class="pending-table" size="small" style="cursor: pointer"
          :empty-text="t('baselineHub.board.pendingEmpty')"
          v-bind="pendingRows">
          <el-table-column prop="id" :label="t('common.id')" width="60" />
          <el-table-column :label="t('baselineHub.entry')" min-width="180">
            <template #default="{ row }">
              <b>{{ row.name }}</b>
              <el-tag v-if="row.version > 1" size="small" effect="plain" class="version">v{{ row.version }}</el-tag>
              <div class="muted tiny">{{ row.notes }}</div>
            </template>
          </el-table-column>
          <el-table-column :label="t('common.status')" width="120">
            <template #default="{ row }">
              <el-tooltip v-if="row.status === 'queued' && (row.queued_reason || row.queue_ahead?.length)"
                :content="queueTooltip(row)" placement="top">
                <el-tag :type="statusType(row.status)" size="small">{{ statusLabel(row.status, locale) }}</el-tag>
              </el-tooltip>
              <el-tag v-else :type="statusType(row.status)" size="small">{{ statusLabel(row.status, locale) }}</el-tag>
              <div v-if="row.status === 'running' && row.run_status" class="muted tiny">{{ statusLabel(row.run_status, locale) }}</div>
              <template v-else-if="row.status === 'queued'">
                <div class="muted tiny">{{ inLine(row) }}</div>
                <div v-if="row.queued_reason" class="muted tiny">{{ row.queued_reason }}</div>
              </template>
              <div v-else-if="row.status === 'failed'" class="muted tiny">{{ row.failure_class }}</div>
            </template>
          </el-table-column>
          <el-table-column :label="t('baselineHub.cards')" width="70">
            <template #default="{ row }">{{ row.gpu_count }}</template>
          </el-table-column>
          <el-table-column :label="t('baselineHub.submitted')" width="140">
            <template #default="{ row }">
              <span :title="exactTime(row.created_at)">{{ relativeTime(row.created_at) }}</span>
              <div class="muted tiny">{{ row.created_by_name }}</div>
            </template>
          </el-table-column>
          <el-table-column label="" width="170">
            <template #default="{ row }">
              <el-button v-if="ownsSubmission(row) && ['queued', 'running'].includes(row.status)" size="small"
                type="danger" plain @click.stop="abort(row)">
                {{ t('baselineHub.abort') }}</el-button>
              <el-button v-if="ownsSubmission(row) && track!.status === 'open'" size="small"
                @click.stop="openResubmit(row)">{{ t('baselineHub.evolve') }}</el-button>
            </template>
          </el-table-column>
        </el-table>
      </el-tab-pane>
      <el-tab-pane name="rules" :label="t('baselineHub.rules.title')">
        <p class="muted tabnote">{{ t('baselineHub.fact.rulesNote') }}</p>
        <dl class="rules">
          <dt>{{ t('baselineHub.rules.weights') }}</dt>
          <dd><span class="mono">{{ track.model_path }}</span>
            <span class="muted tiny">{{ t('baselineHub.rules.servedAs',
              { name: track.served_model_name }) }}</span>
            <div v-for="w in weightsRows" :key="w.machine_id" class="image-line">
              <span>{{ w.machine }}</span>
              <el-tag size="small" :type="weightsType(w.state)" effect="plain">
                {{ t(`baselineHub.weights.${w.state}`) }}</el-tag>
              <span class="muted tiny">{{ weightsNodes(w) }}</span>
            </div>
            <el-button v-if="isAdmin && weightsRows.length" size="small" link type="primary"
              @click="recheckWeights">{{ t('baselineHub.weights.recheck') }}</el-button>
          </dd>
          <dt>{{ t('baselineHub.rules.images') }}</dt>
          <dd>
            <div v-for="i in track.images" :key="i.image" class="image-line">
              <span class="mono">{{ i.image }}</span>
              <el-tag size="small" effect="plain">{{ i.engine }}</el-tag>
              <span v-if="i.label" class="muted tiny">{{ i.label }}</span>
            </div>
          </dd>
          <dt>{{ t('baselineHub.rules.gate') }}</dt>
          <dd><span class="gate mono">{{ track.gate_text }}</span>
            <InfoHint :width="380">{{ t('baselineHub.rules.gateHint') }}</InfoHint></dd>
          <dt>{{ t('baselineHub.rules.scenarios') }}</dt>
          <dd>
            <template v-if="liveScenarios.length">
              <span v-for="(m, i) in liveScenarios" :key="m.key">
                <template v-if="i > 0"> · </template>
                {{ scenarioText(m) }}
                <span class="muted tiny">({{ m.weight > 0 ? t('baselineHub.rules.ranked')
                  : t('baselineHub.rules.shown') }})</span>
              </span>
            </template>
            <template v-else-if="template">
              <span v-for="(m, i) in template.modules.filter((x) => x.is_scenario)"
                :key="m.order_index">
                <template v-if="i > 0"> · </template>
                {{ scenarioText(m) }}
                <span class="muted tiny">({{ m.weight > 0 ? t('baselineHub.rules.ranked')
                  : t('baselineHub.rules.shown') }})</span>
              </span>
            </template>
            <span v-else class="muted">{{ t('baselineHub.rules.byBenchmark') }}</span>
          </dd>
          <dt v-if="liveQuality.length">{{ t('baselineHub.rules.quality') }}</dt>
          <dd v-if="liveQuality.length">
            <div v-for="m in liveQuality" :key="m.key" class="quality-mod">
              <span class="mono">{{ m.module_name }}</span>
              <span v-if="moduleGates(m.key)" class="mono gate-inline">{{ moduleGates(m.key) }}</span>
              <span v-else class="muted tiny">{{ t('baselineHub.rules.notGated') }}</span>
              <el-tag v-if="floorsOf(m.key).length" size="small" type="success" effect="plain"
                class="version">{{ t('baselineHub.rules.qualityFloor') }}</el-tag>
            </div>
          </dd>
          <dt v-if="liveQuality.length || reference">{{ t('baselineHub.rules.reference') }}</dt>
          <dd v-if="liveQuality.length || reference">
            <template v-if="reference">
              <router-link class="link nav-link" :to="`/baseline-hub/${reference.submission_id}`">
                {{ reference.name }}</router-link>
              <el-tag v-if="reference.version > 1" size="small" effect="plain" class="version">
                v{{ reference.version }}</el-tag>
              <span class="muted tiny">
                · {{ reference.confirmed_by }} ·
                <span :title="exactTime(reference.confirmed_at)">{{ relativeTime(reference.confirmed_at) }}</span>
                · {{ t('baselineHub.rules.referenceMargin', { margin: reference.margin_points }) }}
              </span>
              <div class="muted tiny mono">
                <span v-for="f in floorsList" :key="f.key" class="floor-chip">
                  {{ f.name }} ≥ {{ f.min }}<template v-if="reference.scores[f.key] != null">
                    ({{ fmtScore(reference.scores[f.key]) }})</template>
                </span>
              </div>
            </template>
            <template v-else>
              <span class="muted">{{ t('baselineHub.rules.noReference') }}</span>
              <InfoHint :width="360">{{ t('baselineHub.rules.noReferenceHint') }}</InfoHint>
            </template>
          </dd>
          <dt>{{ t('baselineHub.rules.rankedBy') }}</dt>
          <dd>{{ rankedBy }} <span class="muted tiny mono">{{ track.ranking_metric }}</span></dd>
          <dt>{{ t('baselineHub.rules.benchmark') }}</dt>
          <dd>
            <a v-if="live?.url" class="link mono" :href="live.url" target="_blank" rel="noopener">
              {{ track.benchmark_slug || '—' }}</a>
            <span v-else class="mono">{{ track.benchmark_slug || '—' }}</span>
            <el-tag v-if="track.benchmark_slug" size="small" effect="plain"
              :type="track.benchmark_locked ? 'success' : 'warning'" class="version">
              {{ track.benchmark_locked ? t('baselineHub.rules.locked')
                : t('baselineHub.rules.unlocked') }}</el-tag>
            <el-tag v-if="track.benchmark_slug" size="small" effect="plain" class="version">
              {{ track.benchmark_managed ? t('baselineHub.rules.managed')
                : t('baselineHub.rules.bound') }}</el-tag>
            <span v-if="track.benchmark_config_hash" class="muted tiny mono">
              {{ t('baselineHub.rules.hash', { hash: track.benchmark_config_hash }) }}</span>
            <el-button v-if="isAdmin" link type="primary" class="inline-change"
              @click="openBenchmark">{{ t('baselineHub.rules.change') }}</el-button>
          </dd>
          <dt v-if="track.cookbook_url">{{ t('baselineHub.rules.cookbook') }}</dt>
          <dd v-if="track.cookbook_url">
            <a class="link" :href="track.cookbook_url" target="_blank" rel="noopener">
              {{ track.cookbook_url }}</a>
          </dd>
          <dt>{{ t('baselineHub.rules.budget') }}</dt>
          <dd>{{ t('baselineHub.rules.perEntry', { n: track.max_run_minutes }) }}
            <span v-if="track.notes" class="muted tiny">· {{ track.notes }}</span></dd>
        </dl>
      </el-tab-pane>
      <!-- Tabs other plugins add to a track (pricing: what its winner is worth). -->
      <PluginSlot name="hub.track.tabs" :props="{ track, board, isAdmin }" />
    </el-tabs>

    <CatalogExportDialog :key="slug" v-model="catalogOpen" :candidates="catalogCandidates"
      :initial="catalogInitial" :model-name="track.model_name" :track-slug="slug"
      :fixed-baseline-run-id="track.board.find(r => r.is_reference)?.run_id ?? null" />
    <BaselineSubmitDialog v-model="submitOpen" :track="track" :resubmit-of="resubmitOf"
      :gpu-types="gpuTypes" :flag-catalog="flagCatalog"
      @submitted="(id: number) => { load(); router.push(`/baseline-hub/${id}`) }" />

    <el-dialog v-model="columnsOpen" :title="t('baselineHub.admin.columns.title')" width="680px">
      <p class="muted dialog-note">{{ t('baselineHub.admin.columns.note') }}</p>
      <p v-if="!columnCandidates.length" class="muted">
        {{ t('baselineHub.admin.columns.noModules') }}
      </p>
      <div v-for="mod in columnCandidates" :key="mod.key" class="col-module">
        <div class="col-module-head">
          <b class="mono">{{ mod.key }}</b>
          <el-tag size="small" effect="plain" :type="mod.is_scenario ? 'info' : 'success'">
            {{ mod.is_scenario ? t('baselineHub.admin.columns.throughput')
              : t('baselineHub.admin.columns.quality') }}</el-tag>
        </div>
        <el-checkbox-group v-model="chosenColumns" class="col-metrics">
          <div v-for="metric in mod.metrics" :key="metric.key" class="col-metric">
            <el-checkbox :value="metric.key">
              <span class="mono">{{ metric.name }}</span>
              <span v-if="metric.role !== 'display'" class="muted tiny">{{ metric.role }}</span>
              <span v-if="metric.gate" class="muted tiny">{{ metric.gate }}</span>
            </el-checkbox>
            <el-input v-if="chosenColumns.includes(metric.key)"
              v-model="columnLabels[metric.key]" size="small" class="col-label"
              :placeholder="t('baselineHub.admin.columns.headerPlaceholder')" />
          </div>
        </el-checkbox-group>
      </div>
      <template #footer>
        <el-button @click="chosenColumns = []">
          {{ t('baselineHub.admin.columns.automatic') }}</el-button>
        <el-button @click="columnsOpen = false">{{ t('common.cancel') }}</el-button>
        <el-button type="primary" :loading="columnsBusy" @click="saveColumns">
          {{ t('baselineHub.admin.columns.save') }}</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="refOpen" :title="t('baselineHub.admin.reference.title')" width="640px">
      <p class="muted">{{ t('baselineHub.admin.reference.intro') }}</p>
      <dl v-if="refRow" class="rules current">
        <dt>{{ t('baselineHub.entry') }}</dt>
        <dd>
          <b>{{ refRow.name }}</b>
          <el-tag v-if="refRow.version > 1" size="small" effect="plain" class="version">v{{ refRow.version }}</el-tag>
          <span class="muted tiny"> · {{ refRow.submitted_by }} · {{ shortImage(refRow.image) }}</span>
        </dd>
      </dl>
      <el-form label-position="top">
        <el-form-item :label="t('baselineHub.admin.reference.margin')">
          <el-input-number v-model="refMargin" :min="0" :max="100" :step="1" :precision="1"
            @change="planReference" />
          <div class="muted tiny-line">{{ t('baselineHub.admin.reference.marginHint') }}</div>
        </el-form-item>
      </el-form>
      <div v-if="refPlanning" class="muted">{{ t('baselineHub.admin.reference.planning') }}</div>
      <el-alert v-else-if="refError" type="error" :closable="false" class="block">{{ refError }}</el-alert>
      <template v-else-if="refPlan">
        <div class="muted tiny-label">{{ t('baselineHub.admin.reference.floors') }}</div>
        <el-table :data="refPlan.floors" size="small" class="block">
          <el-table-column :label="t('baselineHub.admin.reference.metric')" min-width="200">
            <template #default="{ row }"><span class="mono">{{ row.key }}</span></template>
          </el-table-column>
          <el-table-column :label="t('baselineHub.admin.reference.score')" width="120" align="right">
            <template #default="{ row }"><span class="mono">{{ fmtScore(row.score) }}</span></template>
          </el-table-column>
          <el-table-column :label="t('baselineHub.admin.reference.was')" width="110" align="right">
            <template #default="{ row }"><span class="mono muted">{{ fmtScore(row.previous_min) }}</span></template>
          </el-table-column>
          <el-table-column :label="t('baselineHub.admin.reference.floor')" width="110" align="right">
            <template #default="{ row }"><b class="mono">{{ fmtScore(row.floor) }}</b></template>
          </el-table-column>
        </el-table>
        <el-alert v-if="refPlan.drops.length" type="warning" :closable="false" class="block">
          <b>{{ t('baselineHub.admin.reference.drops', { n: refPlan.drops.length }) }}</b>
          <div v-for="d in refPlan.drops" :key="d.measurement_id" class="tiny-line">
            {{ d.name }}<template v-if="d.version > 1"> v{{ d.version }}</template>
            <span class="muted"> — {{ d.reason }}</span>
          </div>
        </el-alert>
        <div v-else class="muted tiny-line">{{ t('baselineHub.admin.reference.noDrops') }}</div>
      </template>
      <template #footer>
        <el-button @click="refOpen = false">{{ t('common.cancel') }}</el-button>
        <el-button type="success" :loading="refBusy"
          :disabled="refPlanning || !refPlan || !refPlan.floors.length" @click="confirmReference">
          {{ t('baselineHub.admin.reference.confirm') }}
        </el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="benchOpen" :title="t('baselineHub.admin.benchmark.title')" width="640px">
      <dl class="rules current">
        <dt>{{ t('baselineHub.admin.benchmark.now') }}</dt>
        <dd>
          <span class="mono">{{ track.benchmark_slug
            || t('baselineHub.admin.benchmark.noneBound') }}</span>
          <el-tag v-if="track.benchmark_slug" size="small" effect="plain" class="version">
            {{ track.benchmark_managed ? t('baselineHub.rules.managed')
              : t('baselineHub.rules.bound') }}</el-tag>
          <div v-if="track.benchmark_error" class="warn tiny-line">{{ track.benchmark_error }}</div>
          <div v-if="track.contest_benchmark_id" class="muted tiny-line">
            {{ t('baselineHub.rules.contestBoard', { id: track.contest_benchmark_id }) }}
          </div>
          <div v-if="track.contest_error" class="warn tiny-line">{{ track.contest_error }}</div>
        </dd>
        <dt>{{ t('baselineHub.admin.benchmark.measured') }}</dt>
        <dd>{{ t('baselineHub.admin.benchmark.rowsOnBoard', { n: track.measured_count }) }}</dd>
      </dl>
      <el-radio-group v-model="bench.mode" class="modes">
        <el-radio value="bind">{{ t('baselineHub.admin.benchmark.modeBind') }}</el-radio>
        <el-radio value="create">{{ t('baselineHub.admin.benchmark.modeCreate') }}</el-radio>
      </el-radio-group>
      <el-form label-position="top">
        <el-form-item v-if="bench.mode === 'bind'"
          :label="t('baselineHub.admin.benchmark.bindLabel')">
          <el-input v-model="bench.bind_benchmark_slug" class="mono" placeholder="sweep-test" />
          <div class="muted tiny-line">{{ t('baselineHub.admin.benchmark.bindHint') }}</div>
        </el-form-item>
        <el-form-item v-else :label="t('baselineHub.admin.benchmark.templateLabel')">
          <el-select v-model="bench.template" style="width: 100%">
            <el-option v-for="tpl in templates" :key="tpl.name" :value="tpl.name"
              :label="`${tpl.title || tpl.name} — ${t('baselineHub.admin.benchmark.scenarios',
                { n: tpl.modules.length })}`" />
          </el-select>
          <div class="muted tiny-line">{{ t('baselineHub.admin.benchmark.templateHint') }}</div>
        </el-form-item>
      </el-form>
      <el-alert v-if="benchLosses" type="warning" :closable="false" class="block">
        <b>{{ t('baselineHub.admin.benchmark.losing', { n: benchLosses }) }}</b>
        {{ t('baselineHub.admin.benchmark.losingNote', { slug: track.benchmark_slug }) }}
        <el-checkbox v-model="bench.confirm" class="confirm">
          {{ t('baselineHub.admin.benchmark.confirm') }}</el-checkbox>
      </el-alert>
      <template #footer>
        <el-button @click="benchOpen = false">{{ t('common.cancel') }}</el-button>
        <el-button type="primary" :loading="benchBusy"
          :disabled="!!benchLosses && !bench.confirm" @click="saveBenchmark">
          {{ benchChanges ? t('baselineHub.admin.benchmark.change')
            : t('baselineHub.admin.benchmark.reread') }}
        </el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="redlinesOpen" :title="t('baselineHub.admin.redlines.title')"
      width="620px">
      <p class="muted tiny">{{ t('baselineHub.admin.redlines.intro') }}</p>
      <el-alert v-if="redlinesError" type="error" :closable="false" class="params-alert">
        {{ redlinesError }}</el-alert>

      <div v-if="!redlineRows.length" class="muted tiny section">
        {{ t('baselineHub.admin.redlines.none') }}</div>
      <div v-for="row in redlineRows" :key="row.key" class="redline-row">
        <div class="redline-name">
          <span class="mono">{{ row.metric }}</span>
          <span class="muted tiny">{{ row.module }}</span>
          <el-tag v-if="row.quality" size="small" type="warning" effect="plain">
            {{ t('baselineHub.admin.redlines.qualityFloor') }}</el-tag>
        </div>
        <el-input-number v-model="redlineEdits[row.key]" :controls="false" :min="0"
          :step="0.01" style="width: 130px" />
        <span v-if="redlineEdits[row.key] !== row.now" class="muted tiny mono">
          {{ t('baselineHub.admin.redlines.was', { v: row.now }) }}</span>
      </div>

      <p class="muted tiny section">{{ t('baselineHub.admin.redlines.gateHint') }}</p>
      <template #footer>
        <el-button @click="redlinesOpen = false">{{ t('common.cancel') }}</el-button>
        <el-button type="primary" :loading="redlinesBusy"
          :disabled="!Object.keys(redlineChanges).length" @click="saveRedlines">
          {{ t('baselineHub.admin.redlines.save',
               { n: Object.keys(redlineChanges).length }) }}</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="paramsOpen" :title="t('baselineHub.admin.params.title')" width="860px">
      <div v-loading="paramsLoading">
        <p class="muted tiny">{{ t('baselineHub.admin.params.intro') }}</p>
        <el-alert v-if="paramsError" type="error" :closable="false" class="params-alert">
          {{ paramsError }}</el-alert>

        <div class="muted tiny section">
          {{ t('baselineHub.admin.params.yamlSection', { slug: track?.benchmark_slug }) }}
          <InfoHint :width="420">{{ t('baselineHub.admin.params.yamlHint') }}</InfoHint>
        </div>
        <el-input v-model="paramsText" type="textarea" :rows="16" class="mono yaml-edit"
          spellcheck="false" />

        <div class="muted tiny section">{{ t('baselineHub.admin.params.applyTo') }}</div>
        <el-select v-model="paramsTargets" multiple filterable style="width: 100%"
          :placeholder="t('baselineHub.admin.params.applyToPlaceholder')">
          <el-option v-for="tr in paramsTracks" :key="tr.slug"
            :label="`${tr.name} (${tr.slug})`" :value="tr.slug" />
        </el-select>
        <p class="muted tiny tiny-line">{{ t('baselineHub.admin.params.orderHint') }}</p>

        <el-checkbox v-model="paramsComparable" class="confirm">
          {{ t('baselineHub.admin.params.comparable') }}</el-checkbox>
        <p class="muted tiny">
          {{ paramsComparable ? t('baselineHub.admin.params.comparableYes')
            : t('baselineHub.admin.params.comparableNo') }}</p>

        <el-form-item :label="t('baselineHub.admin.params.note')" label-position="top">
          <el-input v-model="paramsNote"
            :placeholder="t('baselineHub.admin.params.notePlaceholder')" />
        </el-form-item>

        <template v-if="paramsPreview">
          <div class="muted tiny section">
            {{ paramsPreview.dry_run ? t('baselineHub.admin.params.wouldChange')
              : t('baselineHub.admin.params.didChange') }}</div>
          <div v-for="row in paramsPreview.tracks" :key="row.slug" class="params-row">
            <div class="params-row-head">
              <span class="mono">{{ row.slug }}</span>
              <span v-if="row.ok" class="ok tiny">{{ row.benchmark_config_hash.slice(0, 8) }}</span>
              <span v-else class="warn tiny">{{ row.error }}</span>
            </div>
            <div v-if="!row.applied.length && row.ok" class="muted tiny">
              {{ t('baselineHub.admin.params.noMove') }}</div>
            <div v-for="(line, i) in row.applied" :key="i" class="mono tiny params-line">
              {{ line }}</div>
          </div>
          <p v-if="paramsPreview.stopped_at" class="warn tiny">
            {{ t('baselineHub.admin.params.stoppedHint', { slug: paramsPreview.stopped_at }) }}</p>
        </template>
      </div>
      <template #footer>
        <el-button @click="paramsOpen = false">{{ t('common.cancel') }}</el-button>
        <el-button :loading="paramsBusy" :disabled="!paramsDirty || !paramsTargets.length"
          @click="previewParams">{{ t('baselineHub.admin.params.preview') }}</el-button>
        <el-button type="primary" :loading="paramsBusy"
          :disabled="!paramsDirty || !paramsTargets.length" @click="applyParams">
          {{ t('baselineHub.admin.params.apply', { n: paramsTargets.length }) }}</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="editOpen" :title="t('baselineHub.admin.edit.title')" width="720px">
      <el-form label-position="top">
        <el-form-item>
          <template #label>
            <span>{{ t('baselineHub.admin.edit.weightsPath') }}</span>
            <InfoHint :width="360">{{ t('baselineHub.admin.edit.weightsPathHint') }}</InfoHint>
          </template>
          <el-input v-model="edit.model_path" class="mono" />
        </el-form-item>
        <div class="muted tiny section">{{ t('baselineHub.admin.edit.images') }}</div>
        <div v-for="(row, i) in edit.images" :key="i" class="image-row">
          <el-select v-model="row.engine" style="width: 110px">
            <el-option value="sglang" label="sglang" />
            <el-option value="vllm" label="vllm" />
          </el-select>
          <el-input v-model="row.image" class="mono" />
          <el-input v-model="row.label" :placeholder="t('baselineHub.admin.newTrack.labelOptional')"
            style="width: 160px" />
          <el-button text class="row-remove" @click="edit.images.splice(i, 1)">−</el-button>
        </div>
        <el-button size="small" class="add"
          @click="edit.images.push({ engine: 'sglang', image: '', label: '' })">
          {{ t('baselineHub.admin.edit.addImage') }}</el-button>
        <div class="grid-2">
          <el-form-item :label="t('baselineHub.admin.edit.rankingMetric')">
            <el-input v-model="edit.ranking_metric" class="mono" />
          </el-form-item>
          <el-form-item :label="t('baselineHub.admin.edit.budget')">
            <el-input-number v-model="edit.max_run_minutes" :min="5" :max="1440" style="width: 100%" />
          </el-form-item>
        </div>
        <el-form-item :label="t('baselineHub.admin.edit.pinnedMachine')">
          <el-select v-model="edit.pinned_machine_id" style="width: 100%">
            <el-option :value="0" :label="t('baselineHub.admin.edit.anyMachine', { gpu: track?.gpu_type ?? '' })" />
            <el-option v-for="m in pinnableMachines" :key="m.id" :value="m.id" :disabled="m.untyped"
              :label="m.untyped ? t('baselineHub.admin.edit.untypedMachine', { name: m.name }) : m.name" />
          </el-select>
          <div class="hint">{{ t('baselineHub.admin.edit.pinnedMachineHint') }}</div>
        </el-form-item>
        <el-form-item :label="t('baselineHub.admin.edit.cookbook')">
          <el-input v-model="edit.cookbook_url" />
        </el-form-item>
        <el-form-item :label="t('baselineHub.admin.edit.notes')">
          <el-input v-model="edit.notes" />
        </el-form-item>
        <el-form-item>
          <template #label>
            <span>{{ t('baselineHub.admin.edit.contestBoard') }}</span>
            <InfoHint :width="380">{{ t('baselineHub.admin.newTrack.contestBoardHint') }}</InfoHint>
          </template>
          <el-input-number v-model="edit.contest_benchmark_id" :min="1" :controls="false"
            :placeholder="t('baselineHub.admin.newTrack.contestBoardNone')" style="width: 220px" />
          <div v-if="track?.contest_error" class="warn tiny-line">{{ track.contest_error }}</div>
        </el-form-item>
        <div class="muted tiny section">
          {{ t('baselineHub.admin.edit.gateSection') }}
          <InfoHint :width="380">{{ t('baselineHub.admin.edit.gateHint') }}</InfoHint>
        </div>
        <div class="grid-3">
          <el-form-item :label="t('baselineHub.admin.edit.ttftCeiling')">
            <el-input-number v-model="edit.ttft_ms" :min="1" :step="1000"
              style="width: 100%" />
          </el-form-item>
          <el-form-item :label="t('baselineHub.admin.edit.ttftPercentile')">
            <el-select v-model="edit.ttft_percentile" style="width: 100%">
              <el-option v-for="p in ['mean', 'p50', 'p90', 'p99']" :key="p"
                :label="p" :value="p" />
            </el-select>
          </el-form-item>
          <el-form-item :label="t('baselineHub.admin.edit.minRequestTps')">
            <el-input-number v-model="edit.min_request_output_tps" :min="0"
              style="width: 100%" />
          </el-form-item>
        </div>
        <p class="muted tiny">{{ t('baselineHub.admin.edit.immutable') }}</p>
      </el-form>
      <template #footer>
        <el-button @click="editOpen = false">{{ t('common.cancel') }}</el-button>
        <el-button type="primary" :loading="editBusy" @click="saveEdit">
          {{ t('baselineHub.admin.edit.save') }}</el-button>
      </template>
    </el-dialog>
  </div>
  <PageLoading v-else :error="page.error" @retry="page.run" />
</template>

<style scoped>
.board-toolbar { display: flex; gap: 12px; margin: 0 0 16px; }
.board-toolbar .el-input { max-width: 360px; }
.board-toolbar .el-select { width: 240px; }
.ranking-value { font-variant-numeric: tabular-nums; }
.board-pagination { margin-top: 20px; justify-content: flex-end; flex-wrap: wrap; gap: 8px; }
.winner-actions { display: flex; flex-direction: column; align-items: flex-end; justify-content: center; gap: 12px; }
.drawer-summary { display: flex; flex-wrap: wrap; gap: 12px; justify-content: space-between; }
.drawer-section { padding: 24px 0; border-bottom: 1px solid var(--autotune-border); }
.drawer-section h2 { font-size: 17px; margin: 0 0 16px; }
.drawer-actions { display: flex; gap: 16px; align-items: center; flex-wrap: wrap; }
.drawer-image { overflow-wrap: anywhere; margin: 18px 0; }
.drawer-scenario, .drawer-quality { padding: 12px 0; }
.drawer-scenario > span { float: right; font-variant-numeric: tabular-nums; }
.drawer-quality > div { display: flex; justify-content: space-between; gap: 16px; }
.drawer-quality p { margin: 4px 0 0; overflow-wrap: anywhere; }
.stale-label { color: #94630d; font-size: 13px; }
@media (max-width: 720px) {
  .board-toolbar { flex-direction: column; }
  .board-toolbar .el-input, .board-toolbar .el-select { width: 100%; max-width: none; }
  .winner-actions { flex-direction: row; align-items: center; justify-content: flex-start; flex-wrap: wrap; }
  .board-pagination { justify-content: center; }
}

.mobile-board, .mobile-pending { display: none; }
@media (max-width: 720px) {
  .board-table:not(.expanded) { display: none; }
  .mobile-board, .mobile-pending { display: grid; gap: 12px; }
  .pending-table { display: none; }
  .mobile-result { background: white; border: 1px solid var(--autotune-border); border-radius: 8px; padding: 16px; overflow-wrap: anywhere; }
  .mobile-result-heading { display: flex; gap: 10px; align-items: baseline; font-weight: 600; }
  .mobile-rank { color: var(--autotune-muted); flex-shrink: 0; }
  .mobile-result-score { display: grid; gap: 2px; margin: 14px 0; }
  .mobile-result-score strong { font-size: 25px; font-variant-numeric: tabular-nums; }
  .mobile-result-footer { display: flex; justify-content: space-between; gap: 12px; font-size: 13px; }
  .mobile-result-actions { display: flex; flex-wrap: wrap; justify-content: flex-end; }
}

.scoring-summary { margin: 16px 0; font-size: 13px; line-height: 1.7; overflow-wrap: anywhere; }
.scoring-summary > span:first-child { color: var(--autotune-muted); margin-right: 8px; }
.scoring-gate { display: block; color: var(--autotune-muted); }
.winner-left { display: grid; grid-template-columns: minmax(0, 1fr) auto; column-gap: 20px; align-items: center; }
.winner-left > .hero-num { grid-column: 2; grid-row: 1 / 3; text-align: right; font-variant-numeric: tabular-nums; }
.winner-left > .hero-label { grid-column: 2; grid-row: 3; text-align: right; }
.winner-left > .quality-line { grid-column: 1; }
.winner-left > .tiny-line, .winner-left > .scenario-details { grid-column: 1 / -1; }
.winner-left > .scenario-details { border: 0; padding: 0; background: transparent; }
@media (max-width: 520px) {
  .winner-left { display: block; }
  .winner-left > .hero-num, .winner-left > .hero-label { text-align: left; }
}

.track-overview { margin: 16px 0; }
.track-overview .facts { margin-top: 12px; }
.scenario-details { margin-top: 12px; }
.winner-right:not([open]) { align-self: start; }

.facts {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(170px, 1fr));
  gap: 14px 22px;
  padding: 14px 18px;
  margin-bottom: 12px;
  background: var(--el-bg-color, #fff);
  border: 1px solid var(--autotune-border);
  border-radius: 8px;
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
  flex: none;
}
.block {
  margin-bottom: 16px;
}
.winner-card {
  border-color: var(--el-color-primary-light-7, #c6e2ff);
}
.winner-grid {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(220px, 320px);
  gap: 28px;
}
@media (max-width: 900px) {
  .winner-grid {
    grid-template-columns: 1fr;
  }
}
.eyebrow {
  color: var(--el-color-primary);
  font-size: 13px;
  font-weight: 700;
  letter-spacing: 0;
}
.winner-name {
  margin-top: 2px;
  font-size: 17px;
  font-weight: 600;
}
.hero-num {
  margin-top: 6px;
  font-size: 34px;
  font-weight: 700;
  line-height: 1.1;
}
.hero-label {
  font-size: 12.5px;
}
.tiny-line {
  margin-top: 6px;
  font-size: 13px;
}
.scenario-row {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  margin-top: 14px;
}
.scenario {
  flex: 1 1 180px;
  padding: 8px 10px;
  border: 1px solid var(--el-border-color-lighter, #ebeef5);
  border-radius: 5px;
}
.scenario-label {
  font-size: 13px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: var(--autotune-muted, #909399);
}
.scenario-tpm {
  font-size: 17px;
  font-weight: 600;
}
.scenario .tiny-line {
  margin-top: 2px;
}
.config-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 8px;
}
.args-block {
  margin: 8px 0;
}
.command {
  margin-top: 8px;
}
.command summary {
  cursor: pointer;
}
.cmd {
  max-height: 200px;
  overflow: auto;
  margin: 6px 0 0;
  padding: 8px 10px;
  border-radius: 4px;
  background: var(--autotune-bg, #f5f7fa);
  font-size: 13px;
  white-space: pre-wrap;
  word-break: break-all;
}
.tabhead {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 24px;
  margin-bottom: 10px;
}
.tabhead-actions {
  display: flex;
  align-items: center;
  gap: 10px;
  flex: none;
}
.tabnote {
  margin: 0;
  max-width: 680px;
  font-size: 12px;
}
.grid-3 {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 12px;
  margin-top: 6px;
}
.dialog-note {
  margin: 0 0 14px;
  font-size: 13px;
}
.medal {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 21px;
  height: 21px;
  border-radius: 50%;
  font-weight: 700;
  font-size: 12px;
  color: #5a4300;
}
.medal-1 {
  background: #f7d774;
}
.medal-2 {
  background: #dcdfe6;
  color: #4a4f57;
}
.medal-3 {
  background: #e6c8a8;
  color: #5c4326;
}
.quality-mod {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 3px;
}
.floor-chip {
  margin-right: 10px;
}
.tiny-label {
  font-size: 12px;
  margin: 8px 0 4px;
}
.gate-inline {
  padding: 1px 7px;
  border-radius: 4px;
  background: var(--el-color-primary-light-9, #ecf5ff);
  color: var(--el-color-primary);
  font-size: 12px;
  font-weight: 600;
}
.quality-line {
  display: flex;
  flex-wrap: wrap;
  gap: 14px;
  margin-top: 10px;
  font-size: 13px;
}
.quality-chip {
  display: inline-flex;
  align-items: baseline;
  gap: 5px;
}
.col-module {
  margin-bottom: 14px;
}
.col-module-head {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 4px;
}
.col-metrics {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: 2px 16px;
}
.col-metric {
  display: flex;
  align-items: center;
  gap: 8px;
}
.col-label {
  width: 150px;
}
.count {
  margin-left: 4px;
  color: var(--autotune-muted, #909399);
  font-size: 13px;
}
.rules {
  display: grid;
  grid-template-columns: 110px 1fr;
  gap: 8px 16px;
  margin: 0;
}
.rules dt {
  color: var(--autotune-muted, #909399);
  font-size: 12px;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  padding-top: 3px;
}
.rules dd {
  margin: 0;
  word-break: break-word;
}
.image-line {
  display: flex;
  gap: 8px;
  align-items: center;
  margin-bottom: 3px;
}
.gate {
  display: inline-block;
  padding: 3px 9px;
  border-radius: 4px;
  background: var(--el-color-primary-light-9, #ecf5ff);
  color: var(--el-color-primary);
  font-weight: 600;
}
.winner {
  font-weight: 700;
}
.missed {
  color: var(--el-color-danger);
}
.tiny-mono {
  font-size: 12px;
}
.tiny {
  font-size: 13px;
  margin-left: 6px;
}
.version {
  margin-left: 6px;
}
.link {
  color: var(--el-color-primary);
  cursor: pointer;
}
.section {
  margin: 0 0 8px;
  text-transform: uppercase;
  letter-spacing: 0.06em;
}
.image-row {
  display: flex;
  gap: 6px;
  align-items: center;
  margin-bottom: 6px;
}
.image-row .row-remove {
  padding: 2px 6px;
  font-size: 16px;
  font-weight: 700;
  color: var(--autotune-muted, #909399);
}
.add {
  margin-bottom: 14px;
}
.grid-2 {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 16px;
}
.current {
  grid-template-columns: 80px 1fr;
  margin-bottom: 14px;
}
.modes {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 4px;
  margin-bottom: 6px;
}
.confirm {
  display: block;
  margin-top: 6px;
}
.inline-change {
  margin-left: 8px;
  font-size: 12px;
}
.warn {
  color: var(--el-color-warning);
}
.ok {
  color: var(--el-color-success);
}
.params-alert {
  margin: 8px 0;
}
/* .mono sets the font on the wrapper; the textarea inherits nothing. */
.yaml-edit :deep(textarea) {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 12px;
  line-height: 1.5;
}
/* One target's outcome. Each track is diffed against its OWN current values,
   so the same roll can move three params here and none there. */
.params-row {
  padding: 6px 0;
  border-top: 1px solid var(--el-border-color-lighter);
}
.params-row-head {
  display: flex;
  gap: 8px;
  align-items: baseline;
}
.params-line {
  color: var(--el-text-color-regular);
  padding-left: 12px;
}
.redline-row {
  display: flex;
  gap: 10px;
  align-items: center;
  padding: 7px 0;
  border-top: 1px solid var(--el-border-color-lighter);
}
.redline-name {
  display: flex;
  gap: 8px;
  align-items: center;
  flex: 1;
  min-width: 0;
}
</style>
