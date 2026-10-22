import { computed, onScopeDispose, ref, watch } from 'vue'
import type { Ref, WritableComputedRef } from 'vue'
import type { BaselineSubmission, BaselineTrack, FlagSpec } from '../types.ts'

interface SubmissionDraftDependencies {
  open: Ref<boolean> | WritableComputedRef<boolean>
  track: Readonly<Ref<BaselineTrack | null>>
  resubmitOf: Readonly<Ref<BaselineSubmission | null>>
  flagCatalog: Readonly<Ref<Record<string, FlagSpec[]>>>
  api: {
    get: (url: string, options?: { params: Record<string, unknown> }) => Promise<{ data: any }>
    post: (url: string, body: unknown) => Promise<{ data: any }>
  }
  t: (key: string, params?: Record<string, unknown>) => string
  notify: { error: (message: string) => unknown; warning: (message: string) => unknown }
  errorText: (error: unknown, fallback: string) => string
  onSubmitted: (id: number) => void
}

/** A dialog-scoped submission draft. UI edits are reconciled before sending;
 *  closing or disposing the scope makes all outstanding results obsolete. */
export function useSubmissionDraft(dependencies: SubmissionDraftDependencies) {
  const { open, track, resubmitOf, flagCatalog, api, t, notify, errorText, onSubmitted } = dependencies
  /** Contest credentials are sent only for tracks that mirror to a board. */
  const contestBoard = computed(() => track.value?.contest_benchmark_id ?? null)
  const hasSavedToken = ref(false)
  /** What this entry will be published as if the field is left blank — the name
   *  saved on the account, else the username. Shown as the placeholder so the
   *  answer is visible without typing anything. */
  const publishedAs = ref('')
  /** The settings most entries never touch, collapsed until opened. */
  const advanced = ref<string[]>([])
  /** Under Advanced: the parsed config as rows, or the same as three JSON dicts. */
  const asJson = ref(false)

  const busy = ref(false)
  const uncertain = ref(false)
  const validationTarget = ref('')
  const validationMessage = ref('')
  const validationRevision = ref(0)
  function revealValidation(field: string, message: string) {
    const group = ['args', 'env', 'vols'].includes(field) ? 'launch'
      : ['nodes', 'gpu', 'placement'].includes(field) ? 'placement'
      : field === 'measurement' ? 'measurement'
      : ['publishing'].includes(field) ? 'publishing' : ''
    if (group && !advanced.value.includes(group)) advanced.value = [...advanced.value, group]
    if (['args', 'env', 'vols'].includes(field)) asJson.value = true
    validationTarget.value = field
    validationMessage.value = message
    validationRevision.value++
  }
  let generation = 0
  let disposed = false
  const isCurrent = (epoch: number) => !disposed && open.value && epoch === generation
  let parseSeq = 0
  let commandDirty = false
  const parsing = ref(false)
  /** What the last compile dropped, rewrote or could not carry — never silent. */
  const parseReport = ref<{
    platform_managed: string[]
    warnings: string[]
    normalized: string[]
  } | null>(null)
  /** Track mode: what the track overrode in the paste, and whether the pasted
   *  image is one of the verified ones. */
  const trackReport = ref<{
    image_verified: boolean
    image_suggestion: string
    replaced: string[]
  } | null>(null)

  const emptyForm = () => ({
    name: '',
    is_baseline: false,
    served_model_name: '',
    model_path: '',
    engine: 'sglang',
    image: '',
    gpu_type: '',
    gpu_count: 1,
    machine_count: 1,
    precision: '',
    service_port: 28200,
    benchmark_slug: '',
    target_metric: '',
    max_run_minutes: 150,
    share_machine: false,
    contest_api_token: '',
    remember_contest_token: false,
    contributor: '',
    remember_contributor: false,
    notes: '',
    command: '',
    argsText: '{}',
    envText: '{}',
    volumesText: '{}',
  })
  const form = ref(emptyForm())

  type ArgRow = { key: string; mode: 'text' | 'bool'; value: string; on: boolean }
  type KV = { key: string; value: string }
  type Vol = { host: string; target: string }
  const argRows = ref<ArgRow[]>([])
  const envRows = ref<KV[]>([])
  const volRows = ref<Vol[]>([])
  const jsonBad = ref({ args: false, env: false, vols: false })
  const baseline = ref('')
  let draftContext = ''
  const initialized = ref(false)
  let resumeCommand = false
  const fingerprint = () => JSON.stringify([form.value, argRows.value, envRows.value, volRows.value])
  const dirty = computed(() => initialized.value && fingerprint() !== baseline.value)
  const contextKey = () => JSON.stringify([track.value?.id, track.value?.benchmark_config_hash,
    resubmitOf.value?.id, resubmitOf.value?.version])
  function discardDraft() {
    cancelSession()
    initialized.value = false
    if (open.value) prime()
  }
  const commandFocused = ref(false)
  const focusedPane = ref<'' | 'args' | 'env' | 'vols'>('')

  /** Track mode: the engine follows the chosen verified image. */
  watch(
    () => form.value.image,
    (image) => {
      const entry = track.value?.images.find((i) => i.image === image)
      if (entry) form.value.engine = entry.engine
    },
  )

  /** Cards the config occupies (tp × dp × pp), read off the rows. In track
   *  mode this IS the hardware ask; the server derives the same number. */
  const cardsFromRows = computed(() => {
    let cards = 1
    for (const key of ['tp', 'dp', 'pp']) {
      const row = argRows.value.find((r) => r.key.trim() === key)
      const n = row && row.mode === 'text' ? parseInt(row.value, 10) : NaN
      if (!Number.isNaN(n) && n > 0) cards *= n
    }
    return cards
  })

  /** Multi-node is sglang-only until vLLM's Ray/external_launcher bootstrap is
   *  built, so the nodes control appears only where it could actually run. */
  const supportsNodes = computed(() => form.value.engine === 'sglang')
  const nodeCount = computed(() => Math.max(1, form.value.machine_count || 1))
  /** Cards each machine holds: the world divided by the node count. `gpu_count`
   *  is the PER-MACHINE ask, which is exactly what the server expects. */
  const perNodeCards = computed(() => {
    if (nodeCount.value <= 1) return cardsFromRows.value
    return cardsFromRows.value % nodeCount.value === 0
      ? cardsFromRows.value / nodeCount.value
      : 0
  })
  const nodesBad = computed(
    () =>
      supportsNodes.value &&
      nodeCount.value > 1 &&
      (cardsFromRows.value % nodeCount.value !== 0 || perNodeCards.value < 1),
  )

  /** The parsed config at a glance, above the fold: what will actually run. */
  const parsedArgs = computed(() => rowsToArgs())
  // ---------------------------------------------------------------- sync engine

  const lookup = computed(() => {
    const known = new Set<string>()
    const switches = new Set<string>()
    const alias: Record<string, string> = {}
    for (const spec of flagCatalog.value[form.value.engine] ?? []) {
      known.add(spec.key)
      if (spec.kind === 'switch') switches.add(spec.key)
      for (const a of spec.aliases) {
        known.add(a)
        alias[a] = spec.key
      }
    }
    return { known, switches, alias }
  })

  function isKnown(key: string): boolean {
    return !lookup.value.known.size || lookup.value.known.has(key.trim())
  }

  function onArgKeyBlur(r: ArgRow) {
    const canonical = lookup.value.alias[r.key.trim()]
    if (canonical) r.key = canonical
    if (lookup.value.switches.has(r.key.trim()) && r.mode === 'text' && r.value === '') {
      r.mode = 'bool'
      r.on = true
    }
    onUiEdit()
  }

  function rowsToArgs(): Record<string, unknown> {
    const out: Record<string, unknown> = {}
    for (const r of argRows.value) {
      if (!r.key.trim()) continue
      out[r.key.trim()] = r.mode === 'bool' ? r.on : r.value === '' ? true : r.value
    }
    return out
  }
  function argsToRows(args: Record<string, unknown>): ArgRow[] {
    return Object.entries(args).map(([k, v]) =>
      typeof v === 'boolean'
        ? { key: k, mode: 'bool' as const, value: '', on: v }
        : { key: k, mode: 'text' as const, value: String(v), on: true },
    )
  }
  function rowsToDict(rows: KV[]): Record<string, string> {
    const out: Record<string, string> = {}
    for (const r of rows) if (r.key.trim()) out[r.key.trim()] = r.value
    return out
  }
  function dictToRows(d: Record<string, unknown>): KV[] {
    return Object.entries(d).map(([k, v]) => ({ key: k, value: v == null ? '' : String(v) }))
  }
  function rowsToVols(): Record<string, string> {
    const out: Record<string, string> = {}
    for (const r of volRows.value) if (r.host.trim() && r.target.trim())
      out[r.host.trim()] = r.target.trim()
    return out
  }
  function volsToRows(d: Record<string, unknown>): Vol[] {
    return Object.entries(d).map(([host, target]) => ({ host, target: String(target ?? '') }))
  }

  /** The config as one runnable docker run line. Platform-managed flags are
   *  absent (each substrate sets its own); the weights ride the /model mount. */
  function buildCommand(): string {
    const f = form.value
    const q = (s: string) => (/[\s"']/.test(s) ? JSON.stringify(s) : s)
    const lines = ['docker run']
    for (const r of envRows.value)
      if (r.key.trim()) lines.push(`  -e ${r.key.trim()}=${q(r.value)}`)
    for (const r of volRows.value)
      if (r.host.trim() && r.target.trim()) lines.push(`  -v ${r.host.trim()}:${r.target.trim()}`)
    if (f.model_path.trim()) lines.push(`  -v ${f.model_path.trim()}:/model`)
    lines.push(`  ${f.image.trim() || '<image>'}`)
    let serve = f.engine === 'vllm' ? '  vllm serve /model' : '  sglang serve --model-path /model'
    if (f.served_model_name.trim()) serve += ` --served-model-name ${q(f.served_model_name.trim())}`
    serve += ` --host 0.0.0.0 --port ${f.service_port}`
    lines.push(serve)
    for (const r of argRows.value) {
      if (!r.key.trim()) continue
      const flag = '--' + r.key.trim().replace(/_/g, '-')
      if (r.mode === 'bool') {
        lines.push(r.on ? `  ${flag}` : `  --no-${flag.slice(2)}`)
      } else {
        lines.push(r.value === '' ? `  ${flag}` : `  ${flag} ${q(r.value)}`)
      }
    }
    return lines.join(' \\\n')
  }

  function syncTextsFromRows() {
    if (focusedPane.value !== 'args') {
      form.value.argsText = JSON.stringify(rowsToArgs(), null, 2)
      jsonBad.value.args = false
    }
    if (focusedPane.value !== 'env') {
      form.value.envText = JSON.stringify(rowsToDict(envRows.value), null, 2)
      jsonBad.value.env = false
    }
    if (focusedPane.value !== 'vols') {
      form.value.volumesText = JSON.stringify(rowsToVols(), null, 2)
      jsonBad.value.vols = false
    }
  }

  function syncCommandFromState() {
    if (commandFocused.value) return
    const hasAnything = form.value.image.trim() || argRows.value.some((r) => r.key.trim())
    form.value.command = hasAnything ? buildCommand() : ''
  }

  function onUiEdit() {
    invalidateCommandParse()
    syncTextsFromRows()
    syncCommandFromState()
  }

  function parseDict(text: string): Record<string, unknown> | null {
    const parsed = JSON.parse(text || '{}')
    if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed)) return null
    return parsed as Record<string, unknown>
  }

  const timers: Record<string, ReturnType<typeof setTimeout>> = {}
  function invalidateCommandParse() {
    parseSeq++
    commandDirty = false
    parsing.value = false
    clearTimeout(timers.command)
  }
  function debounced(name: string, ms: number, fn: () => void) {
    clearTimeout(timers[name])
    timers[name] = setTimeout(fn, ms)
  }

  function onArgsText() {
    invalidateCommandParse()
    debounced('args', 350, () => {
      try {
        const parsed = parseDict(form.value.argsText)
        if (!parsed) throw new Error()
        argRows.value = argsToRows(parsed)
        jsonBad.value.args = false
        syncCommandFromState()
      } catch {
        jsonBad.value.args = true
      }
    })
  }
  function onEnvText() {
    invalidateCommandParse()
    debounced('env', 350, () => {
      try {
        const parsed = parseDict(form.value.envText)
        if (!parsed) throw new Error()
        envRows.value = dictToRows(parsed)
        jsonBad.value.env = false
        syncCommandFromState()
      } catch {
        jsonBad.value.env = true
      }
    })
  }
  function onVolumesText() {
    invalidateCommandParse()
    debounced('vols', 350, () => {
      try {
        const parsed = parseDict(form.value.volumesText)
        if (!parsed) throw new Error()
        volRows.value = volsToRows(parsed)
        jsonBad.value.vols = false
        syncCommandFromState()
      } catch {
        jsonBad.value.vols = true
      }
    })
  }

  function onCommandInput() {
    commandDirty = true
    parseSeq++
    debounced('command', 600, parseCommand)
  }

  /** Server-side compile of the pasted command. In track mode the server
   *  answers with the track's fixed values already in place and a report of
   *  what it replaced; the image is taken only when it is a verified one. */
  async function parseCommand(): Promise<boolean> {
    const command = form.value.command.trim()
    if (!command) return false
    parsing.value = true
    const seq = ++parseSeq
    try {
      const { data } = await api.post('/baseline-hub/parse', {
        command,
        track: track.value?.slug ?? '',
      })
      if (seq !== parseSeq || !open.value) return false
      if (data.engine) form.value.engine = data.engine
      if (data.cards > 0) form.value.gpu_count = data.cards
      const pastedImage = String(data.image ?? '').trim()
      if (track.value) {
        trackReport.value = data.track ?? null
        if (pastedImage && data.track?.image_verified) form.value.image = pastedImage
      } else if (pastedImage) {
        form.value.image = pastedImage
      }
      if ((data.model_path ?? '').trim()) form.value.model_path = data.model_path.trim()
      if (data.served_model_name && !resubmitOf.value)
        form.value.served_model_name = data.served_model_name
      if (data.service_port) form.value.service_port = data.service_port
      argRows.value = argsToRows(data.engine_args ?? {})
      envRows.value = dictToRows(data.extra_env ?? {})
      volRows.value = volsToRows(data.extra_volumes ?? {})
      parseReport.value = {
        platform_managed: data.platform_managed ?? [],
        warnings: data.warnings ?? [],
        normalized: data.normalized ?? [],
      }
      syncTextsFromRows()
      commandDirty = false
      return true
    } catch (error: any) {
      if (seq !== parseSeq || !open.value) return false
      const message = errorText(error, t('baselineHub.submit.parseFailed'))
      revealValidation('command', message)
      notify.error(message)
      return false
    } finally {
      if (seq === parseSeq) parsing.value = false
    }
  }

  function onCommandBlur() {
    commandFocused.value = false
    clearTimeout(timers.command)
    const command = form.value.command.trim()
    if (!command) {
      syncCommandFromState()
      return
    }
    if (!commandDirty) return
    parseCommand().then((ok) => {
      if (ok) syncCommandFromState()
    })
  }

  function onPaneBlur(_pane: 'args' | 'env' | 'vols') {
    focusedPane.value = ''
    // Debounce may not yet have updated rows. Preserve the latest text;
    // submit() synchronously validates and flushes all three JSON panes.
  }

  function useSuggestedImage() {
    if (!trackReport.value?.image_suggestion) return
    form.value.image = trackReport.value.image_suggestion
    trackReport.value = { ...trackReport.value, image_verified: true, image_suggestion: '' }
    onUiEdit()
  }

  // ------------------------------------------------------------------- dialog

  function refreshIdentity(epoch: number) {
    if (track.value) {
      api.get('/auth/me')
        .then(({ data }) => {
          if (!isCurrent(epoch)) return
          hasSavedToken.value = !!data.has_contest_token
          publishedAs.value = data.contest_contributor || data.username || ''
        })
        .catch(() => { if (isCurrent(epoch)) hasSavedToken.value = false })
    }
  }

  /** Seed a new context; reopening the same dialog retains its in-memory draft. */
  function prime() {
    commandDirty = false
    uncertain.value = false
    argRows.value = []
    envRows.value = []
    volRows.value = []
    entries.value = []
    commandFocused.value = false
    focusedPane.value = ''
    const epoch = generation
    const t = track.value
    form.value = emptyForm()
    // Only to word the token field ("saved — leave blank" vs "enter one"); the
    // token itself is never sent back to us, so there is nothing to prefill.
    hasSavedToken.value = false
    publishedAs.value = ''
    copiedFrom.value = null
    fromId.value = null
    advanced.value = []
    validationTarget.value = ''
    validationMessage.value = ''
    asJson.value = false
    refreshIdentity(epoch)
    if (t) {
      form.value.served_model_name = t.served_model_name
      form.value.model_path = t.model_path
      form.value.gpu_type = t.gpu_type
      form.value.precision = t.precision
      form.value.benchmark_slug = t.benchmark_slug
      form.value.target_metric = t.ranking_metric
      form.value.max_run_minutes = t.max_run_minutes
      if (t.images.length === 1) form.value.image = t.images[0].image
      form.value.engine = t.images[0]?.engine ?? 'sglang'
    }
    if (resubmitOf.value) fill(resubmitOf.value)
    else void loadEntries()
    // Match the verified image synchronously before recording a clean baseline.
    const selectedImage = track.value?.images.find(image => image.image === form.value.image)
    if (selectedImage) form.value.engine = selectedImage.engine
    parseReport.value = null
    trackReport.value = null
    jsonBad.value = { args: false, env: false, vols: false }
    syncTextsFromRows()
    syncCommandFromState()
    draftContext = contextKey()
    initialized.value = true
    baseline.value = fingerprint()
  }

  /** Copy an entry's config into the form. Inside a track only what an entrant
   *  chooses is copied — the image (when still verified) and the launch config;
   *  the track's fixed fields stay the track's. */
  function fill(s: BaselineSubmission) {
    const f = form.value
    if (track.value) {
      if (track.value.images.some((i) => i.image === s.image)) f.image = s.image
    } else {
      Object.assign(f, {
        served_model_name: s.served_model_name,
        model_path: s.model_path,
        engine: s.engine,
        image: s.image,
        gpu_type: s.gpu_type,
        gpu_count: s.gpu_count,
        precision: s.precision,
        benchmark_slug: s.benchmark_slug,
        target_metric: String((s.objective ?? {}).target_metric ?? ''),
        max_run_minutes: s.max_run_minutes,
      })
    }
    Object.assign(f, {
      name: s.name,
      is_baseline: !!s.is_baseline,
      machine_count: s.machine_count || 1,
      service_port: s.service_port,
      share_machine: s.share_machine,
      notes: s.notes,
    })
    argRows.value = argsToRows(s.engine_args ?? {})
    envRows.value = dictToRows(s.extra_env ?? {})
    volRows.value = volsToRows(s.extra_volumes ?? {})
  }

  // -- start from an earlier entry ---------------------------------------------

  /** Entries to start from: this track's, or every entry on the free-form path. */
  const entries = ref<BaselineSubmission[]>([])
  const fromId = ref<number | null>(null)
  const copiedFrom = ref<{ id: number; name: string } | null>(null)

  async function loadEntries() {
    const epoch = generation
    try {
      const params = track.value ? { track_id: track.value.id } : {}
      const { data } = await api.get('/baseline-hub/submissions', { params })
      if (isCurrent(epoch)) entries.value = data
    } catch {
      if (isCurrent(epoch)) entries.value = []
    }
  }

  function entryLabel(s: BaselineSubmission): string {
    return `#${s.id} · ${s.name} v${s.version} · ${s.status}`
  }

  function startFrom(id: number) {
    const s = entries.value.find((e) => e.id === id)
    if (!s) return
    Object.values(timers).forEach(clearTimeout)
    invalidateCommandParse()
    focusedPane.value = ''
    commandFocused.value = false
    fill(s)
    form.value.name = `${s.name} (copy)`
    form.value.notes = ''
    copiedFrom.value = { id: s.id, name: `${s.name} v${s.version}` }
    parseReport.value = null
    trackReport.value = null
    jsonBad.value = { args: false, env: false, vols: false }
    syncTextsFromRows()
    syncCommandFromState()
    fromId.value = null
  }

  async function submit() {
    if (!isCurrent(generation) || busy.value || uncertain.value) return
    const epoch = generation
    validationTarget.value = ''
    validationMessage.value = ''
    busy.value = true
    // A click may immediately follow blur or a JSON keystroke. Flush the
    // current input before reading rows; never submit the previous config.
    Object.values(timers).forEach(clearTimeout)
    try {
      if (commandDirty && !(await parseCommand())) return
      if (!isCurrent(epoch)) return
      // Text remains authoritative across JSON → Rows switches too: a pending
      // debounce must not be discarded just because its pane became hidden.
      {
        const read = (value: string) => {
          try { return parseDict(value) } catch { return null }
        }
        const args = read(form.value.argsText)
        const env = read(form.value.envText)
        const vols = read(form.value.volumesText)
        jsonBad.value = { args: !args, env: !env, vols: !vols }
        if (!args || !env || !vols) {
          const field = !args ? 'args' : !env ? 'env' : 'vols'
          const message = t('baselineHub.submit.jsonValidation')
          revealValidation(field, message)
          notify.error(message)
          return
        }
        argRows.value = argsToRows(args)
        envRows.value = dictToRows(env)
        volRows.value = volsToRows(vols)
      }
      await submitPrepared()
    } finally {
      if (isCurrent(epoch)) busy.value = false
    }
  }

  async function submitPrepared() {
    const epoch = generation
    if (jsonBad.value.args || jsonBad.value.env || jsonBad.value.vols) {
      notify.error(t('baselineHub.submit.jsonValidation'))
      return
    }
    const f = form.value
    if (nodesBad.value) {
      revealValidation('nodes', t('baselineHub.submit.nodesBad'))
      notify.error(t('baselineHub.submit.nodesBad'))
      return
    }
    if (!track.value && nodeCount.value > 1 && f.gpu_count !== perNodeCards.value) {
      revealValidation('gpu', t('baselineHub.submit.gpuAskBad', { n: perNodeCards.value }))
      notify.error(t('baselineHub.submit.gpuAskBad', { n: perNodeCards.value }))
      return
    }
    const config = {
      engine_args: rowsToArgs(),
      extra_env: rowsToDict(envRows.value),
      extra_volumes: rowsToVols(),
      service_port: f.service_port,
      machine_count: supportsNodes.value ? nodeCount.value : 1,
      share_machine: f.share_machine,
      notes: f.notes,
      // The published name applies with or without a contest — LLMBench lists
      // every submission under it. Blank = the saved name, else the username.
      ...(track.value
        ? {
            contributor: f.contributor.trim(),
            remember_contributor: f.remember_contributor,
            is_baseline: f.is_baseline,
          }
        : {}),
      // The token only does something where a board exists. Blank = fall back to
      // the saved one; with none, the entry is measured but not forwarded.
      ...(contestBoard.value
        ? {
            contest_api_token: f.contest_api_token.trim(),
            remember_contest_token: f.remember_contest_token,
          }
        : {}),
    }
    busy.value = true
    try {
      let data: { id: number }
      if (resubmitOf.value) {
        // Inside a track only the config may change; the fixed fields are not
        // even sent, so the server never has to refuse them.
        const body = track.value
          ? { name: f.name.trim(), image: f.image.trim(), ...config }
          : {
              name: f.name.trim(),
              model_path: f.model_path.trim(),
              image: f.image.trim(),
              gpu_type: f.gpu_type,
              gpu_count: f.gpu_count,
              precision: f.precision.trim(),
              benchmark_slug: f.benchmark_slug.trim(),
              target_metric: f.target_metric.trim(),
              max_run_minutes: f.max_run_minutes,
              ...config,
            }
        data = (await api.post(`/baseline-hub/submissions/${resubmitOf.value.id}/resubmit`, body)).data
      } else if (track.value) {
        data = (
          await api.post(`/baseline-hub/tracks/${track.value.slug}/submissions`, {
            name: f.name.trim(),
            image: f.image.trim(),
            ...config,
          })
        ).data
      } else {
        data = (
          await api.post('/baseline-hub/submissions', {
            name: f.name.trim(),
            served_model_name: f.served_model_name.trim(),
            model_path: f.model_path.trim(),
            engine: f.engine,
            image: f.image.trim(),
            gpu_type: f.gpu_type,
            gpu_count: f.gpu_count,
            precision: f.precision.trim(),
            benchmark_slug: f.benchmark_slug.trim(),
            target_metric: f.target_metric.trim(),
            max_run_minutes: f.max_run_minutes,
            ...config,
          })
        ).data
      }
      if (!isCurrent(epoch)) return
      busy.value = false
      initialized.value = false
      open.value = false
      onSubmitted(data.id)
    } catch (error: any) {
      if (!isCurrent(epoch)) return
      if (!error.response || error.response.status >= 500) {
        uncertain.value = true
        notify.warning(t('baselineHub.submit.unconfirmedResponse'))
      }
      const message = errorText(error, t('baselineHub.submit.failed'))
      const detail = error.response?.data?.detail
      const fieldMap: Record<string, string> = {
        name: 'name', image: 'image', model_path: 'model_path', engine_args: 'args',
        extra_env: 'env', extra_volumes: 'vols', machine_count: 'nodes',
        gpu_count: 'gpu', service_port: 'placement', contributor: 'publishing',
        contest_api_token: 'publishing', benchmark_slug: 'measurement', target_metric: 'measurement', max_run_minutes: 'measurement',
      }
      const field = Array.isArray(detail)
        ? detail.flatMap((item: { loc?: unknown[] }) => (item.loc ?? []).map(String))
          .map((key: string) => fieldMap[key]).find(Boolean)
        : undefined
      revealValidation(field || '', message)
      notify.error(message)
    } finally {
      if (isCurrent(epoch)) busy.value = false
    }
  }

  function cancelSession() {
    generation++
    invalidateCommandParse()
    Object.values(timers).forEach(clearTimeout)
    busy.value = false
  }

  watch(open, (isOpen) => {
    if (!isOpen) resumeCommand = commandDirty
    cancelSession()
    commandFocused.value = false
    focusedPane.value = ''
    if (isOpen) {
      if (!initialized.value || draftContext !== contextKey()) prime()
      else {
        commandDirty = resumeCommand
        refreshIdentity(generation)
        if (!resubmitOf.value) void loadEntries()
      }
    }
  }, { immediate: true, flush: 'sync' })

  onScopeDispose(() => {
    disposed = true
    cancelSession()
  })

  return {
    dirty, discardDraft, contestBoard, hasSavedToken, publishedAs, advanced, asJson,
    busy, uncertain, validationTarget, validationMessage, validationRevision, revealValidation, parsing, parseReport, trackReport, form,
    argRows, envRows, volRows, jsonBad, commandFocused, focusedPane,
    cardsFromRows, supportsNodes, nodeCount, perNodeCards, nodesBad, parsedArgs,
    entries, fromId, copiedFrom,
    isKnown, onArgKeyBlur, onUiEdit, onArgsText, onEnvText, onVolumesText,
    onCommandInput, onCommandBlur, onPaneBlur, useSuggestedImage,
    entryLabel, startFrom, submit,
  }
}
