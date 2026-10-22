import { computed, onScopeDispose, ref, toRef, watch, type Ref } from 'vue'
import type { AxiosInstance } from 'axios'
import type { CatalogExportPreview, CatalogInput } from '../api/client.ts'
import { createComparisonReportFlow } from '../../features/autoreport/comparisonReport.ts'

export interface CatalogCandidate {
  run_id: number
  /** What the row is called here, and in the provenance lines. */
  label: string
  /** A short note next to the label: a score, a rank. */
  detail?: string
  /** Track baseline options stay public; only owned runs can be optimized. */
  canOptimize?: boolean
}
interface Row extends CatalogCandidate {
  picked: boolean
  role: string
  /** Variant id override; empty = derived (<engine>-<parallelism>-<card>-<role>). */
  id: string
  /** The image tag to publish when the run's image was named by digest alone. */
  imageTag: string
}

export interface CatalogExportProps {
  modelValue: boolean
  candidates: CatalogCandidate[]
  initial: { run_id: number; role: string }[]
  modelName?: string
  trackSlug?: string
  fixedBaselineRunId?: number | null
}

/** Own the export draft, report readiness, preview validity and publication preparation. */
export function useCatalogExport(props: CatalogExportProps, options: {
  open: Ref<boolean>
  api: Pick<AxiosInstance, 'get' | 'post' | 'put'>
  t: (key: string, params?: Record<string, unknown>) => string
  text: (zh: string, en: string) => string
  errorText: (error: unknown, fallback: string) => string
  wait?: () => Promise<void>
}) {
  const { api, t, text, errorText } = options
  const baselineRun = ref<number | null>(null)
  const optimizedRun = ref<number | null>(null)
  const reports = createComparisonReportFlow(api, { text, wait: options.wait })
  const reportStatus = toRef(reports.state, 'status')
  const reportFailed = toRef(reports.state, 'failed')
  let loadSequence = 0
  let applying = false
  let alive = true
  const open = options.open
  const existingModel = ref(false)
  onScopeDispose(() => { alive = false; loadSequence++; reports.cancel() })

  const rows = ref<Row[]>([])
  const name = ref('')
  const hf = ref('')
  const displayName = ref('')
  const description = ref('')
  const family = ref('')
  const tags = ref('')
  const license = ref('')
  const homepage = ref('')
  const defaultVariant = ref('')
  const includeEnv = ref(true)
  const includeVolumes = ref(false)
  /** null = automatic: 90 minutes, raised to 3x the measured load when longer. */
  const startupMinutes = ref<number | null>(null)
  /** Empty = the node label of the card each config was measured on. */
  const gpuProduct = ref('')
  /** A saved report comparing exactly these runs; a tuning result needs one. */
  const reportId = ref<number | null>(null)
  const advanced = ref<string[]>([])
  /** Env var -> recipe (included) | site (left out), for this export: a call on
   *  a name the rules got wrong or do not know. */
  const envCategories = ref<Record<string, string>>({})
  /** Save those calls for every later export too. */
  const rememberCalls = ref(true)

  const loading = ref(false)
  const error = ref('')
  const preview = ref<CatalogExportPreview | null>(null)
  const hfSuggestion = ref('')
  const catalogStatus = ref('')

  const picked = computed(() => rows.value.filter((r) => r.picked))
  const baselineCandidates = computed(() => rows.value.filter(r => r.run_id !== optimizedRun.value))
  const optimizedCandidates = computed(() => rows.value.filter(r =>
    r.canOptimize !== false && r.run_id !== baselineRun.value))
  const ready = computed(() => props.trackSlug
    ? rows.value.some(r => r.run_id === baselineRun.value)
      && optimizedCandidates.value.some(r => r.run_id === optimizedRun.value)
      && (!props.fixedBaselineRunId || baselineRun.value === props.fixedBaselineRunId)
    : picked.value.length > 0)
  function selectAvailablePair() {
    const initialBaseline = props.initial.find(row => row.role === 'baseline')?.run_id
    const initialOptimized = props.initial.find(row => row.role === 'optimized')?.run_id
    const preferredBaseline = props.fixedBaselineRunId ?? baselineRun.value ?? initialBaseline
    const canOptimize = (id: number | null | undefined) => id !== props.fixedBaselineRunId
      && rows.value.some(row => row.run_id === id && row.canOptimize !== false)
    // Reserve an owned run for optimized, even if the shared track pair used it as baseline.
    optimizedRun.value = [optimizedRun.value, initialOptimized].find(canOptimize)
      ?? rows.value.find(row => canOptimize(row.run_id) && row.run_id !== preferredBaseline)?.run_id
      ?? rows.value.find(row => canOptimize(row.run_id))?.run_id ?? null
    const canBaseline = (id: number | null | undefined) => id !== optimizedRun.value
      && rows.value.some(row => row.run_id === id)
    baselineRun.value = props.fixedBaselineRunId ?? [baselineRun.value, initialBaseline].find(canBaseline)
      ?? rows.value.find(row => canBaseline(row.run_id))?.run_id ?? null
    if (!canBaseline(baselineRun.value)) baselineRun.value = null
  }
  function syncPair() {
    if (!props.trackSlug) return
    for (const row of rows.value) {
      row.role = row.run_id === baselineRun.value ? 'baseline' : row.run_id === optimizedRun.value ? 'optimized' : ''
      row.picked = !!row.role
    }
  }
  function changePair() {
    applying = true
    syncPair()
    reportId.value = null
    reportFailed.value = false
    reportStatus.value = ''
    preview.value = null
    applying = false
    selectComparison()
    invalidate()
    void load()
  }
  /** Refresh editable selections; callers retain frozen reviews until returning to edit. */
  function refreshCandidates() {
    const old = new Map(rows.value.map(row => [row.run_id, row]))
    const initial = new Map(props.initial.map(row => [row.run_id, row.role]))
    rows.value = props.candidates.map(candidate => ({
      ...candidate,
      picked: old.get(candidate.run_id)?.picked ?? initial.has(candidate.run_id),
      role: old.get(candidate.run_id)?.role ?? initial.get(candidate.run_id) ?? '',
      id: old.get(candidate.run_id)?.id ?? '',
      imageTag: old.get(candidate.run_id)?.imageTag ?? '',
    }))
    if (props.trackSlug) {
      const previousPair = `${baselineRun.value}:${optimizedRun.value}`
      selectAvailablePair()
      if (previousPair !== `${baselineRun.value}:${optimizedRun.value}`) changePair()
      else syncPair()
    }
  }

  /** The form's metadata fields matter only for a model new to the catalog. */
  const isNew = computed(() => !existingModel.value)

  /** A freshly ticked row gets a role: the baseline if none is picked yet,
   *  otherwise another optimized config. */
  function onTick(row: Row) {
    if (row.picked && !row.role) {
      row.role = picked.value.some((r) => r !== row && r.role === 'baseline') ? 'optimized' : 'baseline'
    }
  }

  function body() {
    // The baseline first, then the rest in list order.
    const order = [...picked.value].sort(
      (a, b) => Number(b.role === 'baseline') - Number(a.role === 'baseline'),
    )
    const list = (text: string) => text.split(',').map((s) => s.trim()).filter(Boolean)
    return {
      variants: order.map((r) => ({
        run_id: r.run_id, role: r.role, label: r.label, id: r.id.trim(), image_tag: r.imageTag.trim(),
      })),
      name: name.value,
      hf: hf.value,
      display_name: displayName.value,
      description: description.value,
      family: family.value,
      tags: list(tags.value),
      license: license.value,
      homepage: homepage.value,
      default_variant: defaultVariant.value,
      include_env: includeEnv.value,
      include_volumes: includeVolumes.value,
      startup_minutes: startupMinutes.value || null,
      gpu_product: list(gpuProduct.value),
      report_id: reportId.value || null,
      ...(props.trackSlug ? { track_slug: props.trackSlug } : {}),
      env_categories: envCategories.value,
    }
  }

  async function generateReport(retry = false) {
    if (!alive || !open.value || loading.value || !ready.value) return
    const current = ++loadSequence
    loading.value = true
    error.value = ''
    try {
      const id = await reports.ensure({ retry })
      if (!id || current !== loadSequence || !open.value || !alive) return
      applying = true
      reportId.value = id
      applying = false
      return await load()
    } catch (e) {
      if (current === loadSequence) error.value = errorText(e, t('catalogExport.failed'))
    } finally {
      if (current === loadSequence) loading.value = false
    }
  }

  async function load() {
    if (!alive || !open.value) return
    const current = ++loadSequence
    if (!ready.value) {
      loading.value = false
      reportStatus.value = ''
      preview.value = null
      return
    }
    loading.value = true
    error.value = ''
    try {
      const { data } = await api.post<CatalogExportPreview>('/catalog-export/preview', body())
      if (current !== loadSequence || !open.value) return
      applying = true
      try {
        existingModel.value = data.existing
        hfSuggestion.value = data.hf_suggestion ?? ''
        catalogStatus.value = data.catalog
        preview.value = data
        // Adopt what the server derived, so the form shows what the files say and
        // every drafted field can be edited. A published model's confirmed HF
        // identity is retained for publishing to a target with a different index.
        // Suggestions for new models still require explicit confirmation.
        if (!name.value) name.value = data.name
        if (data.existing && !hf.value) hf.value = data.hf
        if (!displayName.value) displayName.value = data.display_name
        if (!description.value) description.value = data.description
        if (!family.value) family.value = data.family
        if (!tags.value) tags.value = (data.tags ?? []).join(', ')
        if (defaultVariant.value && !variantIds.value.includes(defaultVariant.value)) {
          defaultVariant.value = ''
        }
      } finally {
        applying = false
      }
      return true
    } catch (e) {
      if (current === loadSequence) {
        error.value = errorText(e, t('catalogExport.failed'))
        preview.value = null
      }
    } finally {
      if (current === loadSequence) loading.value = false
    }
  }

  const variantIds = computed(() => (preview.value?.variants ?? []).map((v) => v.id))

  /** Every env var and mount once: the variants of one export usually share them. */
  const inputs = computed(() => {
    const seen = new Map<string, CatalogInput>()
    for (const v of preview.value?.variants ?? []) {
      for (const i of v.inputs) {
        if (i.category === 'platform') continue
        seen.set(`${i.kind}:${i.name}`, i)
      }
    }
    return [...seen.values()].sort((a, b) => a.kind.localeCompare(b.kind) || a.name.localeCompare(b.name))
  })
  /** Env vars no rule recognizes and nobody has decided: the one question asked. */
  const questions = computed(() =>
    inputs.value.filter((i) => i.kind === 'env' && i.category === 'unknown' && !envCategories.value[i.name]))
  const kept = (i: CatalogInput) => i.action !== 'left out'
  const summary = computed(() => {
    const count = (kind: string, inc: boolean) =>
      inputs.value.filter((i) => i.kind === kind && kept(i) === inc).length
    return t('catalogExport.inputsSummary', {
      envIn: count('env', true), envOut: count('env', false),
      mountIn: count('mount', true), mountOut: count('mount', false),
    })
  })

  /** Include an env var in the export, or leave it out; saved for later exports
   *  too unless "remember" is off. */
  async function decide(name: string, include: boolean) {
    if (!alive || !open.value) return
    const category = include ? 'recipe' : 'site'
    envCategories.value = { ...envCategories.value, [name]: category }
    const current = loadSequence
    if (rememberCalls.value) {
      try {
        await api.put(`/catalog-export/env-categories/${encodeURIComponent(name)}`, { category })
      } catch (e) {
        if (current === loadSequence && alive) error.value = errorText(e, t('catalogExport.failed'))
      }
    }
    if (current === loadSequence && alive && open.value) await load()
  }

  function useSuggestion() {
    hf.value = hfSuggestion.value
  }

  /** Only an up-to-date, explicitly confirmed draft may reach GitHub preview. */
  async function prepare() {
    if (!alive || !open.value || loading.value || !ready.value) return null
    const loaded = props.trackSlug ? await generateReport(reportFailed.value) : await load()
    if (!loaded || !alive || !open.value || !preview.value) return null
    if (isNew.value && !hf.value.trim()) {
      error.value = text('请确认新模型的 Hugging Face ID。', 'Confirm the new model’s Hugging Face ID.')
      return null
    }
    if (questions.value.length) {
      error.value = text('请确认未识别的环境变量是否公开。', 'Decide whether to publish the unrecognized environment variables.')
      return null
    }
    return preview.value.valid ? body() : null
  }

  function selectComparison() {
    reports.select(props.trackSlug && baselineRun.value && optimizedRun.value ? {
      trackSlug: props.trackSlug,
      baselineRunId: baselineRun.value,
      optimizedRunId: optimizedRun.value,
    } : null)
  }

  function invalidate() {
    loadSequence++
    preview.value = null
    loading.value = false
    reports.cancel()
  }

  watch(() => JSON.stringify(body()), () => {
    if (!applying) invalidate()
  }, { flush: 'sync' })

  let initialized = false
  watch(
    () => props.modelValue,
    (isOpen) => {
      loadSequence++
      loading.value = false
      reports.cancel()
      if (!isOpen) return
      if (initialized) return
      initialized = true
      applying = true
      existingModel.value = false
      const initial = new Map(props.initial.map((p) => [p.run_id, p.role]))
      rows.value = props.candidates.map((c) => ({
        ...c,
        picked: initial.has(c.run_id),
        role: initial.get(c.run_id) ?? '',
        id: '',
        imageTag: '',
      }))
      for (const field of [name, hf, displayName, description, family, tags, license, homepage]) {
        field.value = ''
      }
      gpuProduct.value = ''
      reportId.value = null
      reportStatus.value = ''
      reportFailed.value = false
      baselineRun.value = props.fixedBaselineRunId ?? props.initial.find(p => p.role === 'baseline')?.run_id ?? null
      optimizedRun.value = props.initial.find(p => p.role === 'optimized')?.run_id ?? null
      if (props.trackSlug) selectAvailablePair()
      syncPair()
      startupMinutes.value = null
      defaultVariant.value = ''
      envCategories.value = {}
      advanced.value = []
      preview.value = null
      error.value = ''
      applying = false
      selectComparison()
      void load()
    },
    { immediate: true, flush: 'sync' },
  )
  return {
    baselineRun, optimizedRun, reportStatus, reportFailed, rows, name,
    hf, displayName, description, family, tags, license, homepage,
    defaultVariant, includeEnv, includeVolumes, startupMinutes, gpuProduct, reportId, advanced,
    envCategories, rememberCalls, loading, error, preview,
    picked, ready, baselineCandidates, optimizedCandidates, changePair, isNew, onTick, body,
    generateReport, load, refreshCandidates, variantIds, inputs, questions, kept, summary,
    decide, useSuggestion, prepare, hfSuggestion, catalogStatus,
  }
}
