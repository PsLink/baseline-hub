import { test } from 'node:test'
import assert from 'node:assert/strict'
import { effectScope, ref } from 'vue'
import { useSubmissionDraft } from '../src/features/hub/composables/useSubmissionDraft.ts'

const flush = () => new Promise(resolve => setImmediate(resolve))
const deferred = () => {
  let resolve, reject
  const promise = new Promise((yes, no) => { resolve = yes; reject = no })
  return { promise, resolve, reject }
}
const entry = (overrides = {}) => ({
  id: 7, name: 'Original', version: 2, status: 'succeeded', engine: 'sglang',
  image: 'old:image', served_model_name: 'old-model', model_path: '/old', gpu_type: 'old-gpu',
  gpu_count: 8, precision: 'fp8', benchmark_slug: 'old-bench', max_run_minutes: 45,
  machine_count: 1, service_port: 30000, share_machine: true, notes: 'old notes',
  engine_args: { tp: 8 }, extra_env: { DEBUG: '1' }, extra_volumes: { '/cache': '/cache' },
  ...overrides,
})
const track = () => ({
  id: 3, slug: 'race', name: 'Race', served_model_name: 'fixed-model', model_path: '/fixed',
  gpu_type: 'fixed-gpu', precision: 'bf16', benchmark_slug: 'fixed-bench', ranking_metric: 'tps',
  max_run_minutes: 60, images: [{ image: 'verified:image', engine: 'sglang' }],
  contest_benchmark_id: 42,
})

function setup(t, options = {}) {
  const calls = [], submitted = [], errors = [], warnings = []
  const open = ref(options.open ?? true)
  const trackRef = ref(options.track ?? null)
  const resubmitOf = ref(options.resubmitOf ?? null)
  const scope = effectScope()
  const draft = scope.run(() => useSubmissionDraft({
    open, track: trackRef, resubmitOf, flagCatalog: ref(options.flagCatalog ?? {}),
    api: {
      get: options.get ?? (async () => ({ data: [] })),
      async post(url, body) {
        calls.push({ url, body })
        if (options.post) return options.post(url, body)
        return { data: url.endsWith('/parse') ? { engine_args: { tp: 8 } } : { id: 99 } }
      },
    },
    t: key => key,
    notify: { error: value => errors.push(value), warning: value => warnings.push(value) },
    errorText: (error, fallback) => error.message || fallback,
    onSubmitted: id => submitted.push(id),
  }))
  t.after(() => scope.stop())
  return { ...draft, open, trackRef, resubmitOf, calls, submitted, errors, warnings, dispose: () => scope.stop() }
}

function inputCommand(draft, command = 'docker run image sglang serve --tp 8') {
  draft.form.value.command = command
  draft.onCommandInput()
}

test('immediate submit waits for current command and prevents a second submit', async t => {
  const compile = deferred()
  const d = setup(t, { post: url => url.endsWith('/parse') ? compile.promise : { data: { id: 9 } } })
  inputCommand(d)
  const pending = d.submit()
  assert.equal(d.busy.value, true)
  await d.submit()
  assert.equal(d.calls.length, 1)
  compile.resolve({ data: { engine_args: { tp: 8 } } })
  await pending
  assert.equal(d.calls.length, 2)
  assert.equal(d.calls[1].body.engine_args.tp, '8')
  assert.deepEqual(d.submitted, [9])
  assert.equal(d.busy.value, false)
})

test('failed or superseded compilation never sends a submission', async t => {
  const d = setup(t, { post: async () => { throw new Error('bad command') } })
  inputCommand(d)
  await d.submit()
  assert.equal(d.calls.length, 1)
  assert.equal(d.busy.value, false)
  assert.deepEqual(d.errors, ['bad command'])
})

test('closing during compilation prevents a submission', async t => {
  const compile = deferred()
  const d = setup(t, { post: () => compile.promise })
  inputCommand(d)
  const pending = d.submit()
  d.open.value = false
  compile.resolve({ data: { engine_args: { tp: 8 } } })
  await pending
  assert.equal(d.calls.length, 1)
  assert.deepEqual(d.submitted, [])
})

test('immediate JSON edits are flushed before reading submission rows', async t => {
  const d = setup(t)
  d.asJson.value = true
  d.focusedPane.value = 'args'
  d.form.value.argsText = '{"tp":8}'
  d.form.value.envText = '{"NCCL_DEBUG":"INFO"}'
  d.form.value.volumesText = '{"/cache":"/cache"}'
  d.onArgsText()
  d.onPaneBlur('args')
  await d.submit()
  assert.deepEqual(d.calls[0].body.engine_args, { tp: '8' })
  assert.deepEqual(d.calls[0].body.extra_env, { NCCL_DEBUG: 'INFO' })
  assert.deepEqual(d.calls[0].body.extra_volumes, { '/cache': '/cache' })
  assert.equal(d.jsonBad.value.args, false)
})

test('advanced row edits invalidate older command compilation', async t => {
  const compile = deferred()
  const d = setup(t, { post: () => compile.promise })
  inputCommand(d)
  const pending = d.submit()
  d.argRows.value = [{ key: 'tp', mode: 'text', value: '2', on: true }]
  d.onUiEdit()
  compile.resolve({ data: { engine_args: { tp: 16 } } })
  await pending
  assert.equal(d.parsing.value, false)
  assert.equal(d.argRows.value[0].value, '2')
  assert.equal(d.calls.length, 1)
})

test('switching from JSON to rows before debounce still flushes new JSON', async t => {
  const d = setup(t)
  d.asJson.value = true
  d.form.value.argsText = '{"tp":16}'
  d.onArgsText()
  d.asJson.value = false
  await d.submit()
  assert.equal(d.calls[0].body.engine_args.tp, '16')
})

test('invalid JSON blocks submission even before debounce validation runs', async t => {
  const d = setup(t)
  d.asJson.value = true
  d.form.value.argsText = '{'
  d.onArgsText()
  await d.submit()
  assert.equal(d.calls.length, 0)
  assert.equal(d.busy.value, false)
  assert.equal(d.errors.length, 1)
})

test('unconfirmed responses block retries until the user acknowledges checking records', async t => {
  const d = setup(t, { post: async () => { throw new Error('connection lost') } })
  await d.submit()
  assert.equal(d.uncertain.value, true)
  assert.equal(d.warnings.length, 1)
  await d.submit()
  assert.equal(d.calls.length, 1)
  d.uncertain.value = false
  await d.submit()
  assert.equal(d.calls.length, 2)
})

test('server errors are uncertain but validation errors permit retry', async t => {
  for (const [status, uncertain] of [[503, true], [422, false]]) {
    const d = setup(t, { post: async () => { throw { response: { status } } } })
    await d.submit()
    assert.equal(d.uncertain.value, uncertain)
  }
})

test('resubmission initializes immediately and uses version endpoint without immutable identity', async t => {
  const d = setup(t, { resubmitOf: entry() })
  assert.equal(d.form.value.name, 'Original')
  assert.equal(d.argRows.value[0].value, '8')
  await d.submit()
  const { url, body } = d.calls[0]
  assert.equal(url, '/baseline-hub/submissions/7/resubmit')
  assert.equal(body.model_path, '/old')
  assert.equal(body.engine, undefined)
  assert.equal(body.served_model_name, undefined)
})

test('track copies keep fixed fields and verified image while copying launch config', async t => {
  const source = entry()
  const d = setup(t, { track: track(), get: async url => ({ data: url === '/auth/me' ? { username: 'Ada', has_contest_token: true } : [source] }) })
  await flush()
  d.startFrom(7)
  assert.equal(d.form.value.name, 'Original (copy)')
  assert.equal(d.form.value.notes, '')
  assert.equal(d.form.value.model_path, '/fixed')
  assert.equal(d.form.value.image, 'verified:image')
  assert.equal(d.hasSavedToken.value, true)
  assert.equal(d.publishedAs.value, 'Ada')
  assert.equal(d.copiedFrom.value.id, 7)
  await d.submit()
  const { url, body } = d.calls[0]
  assert.equal(url, '/baseline-hub/tracks/race/submissions')
  assert.equal(body.engine_args.tp, '8')
  assert.equal(body.gpu_count, undefined)
  assert.equal(body.model_path, undefined)
  assert.equal(body.contest_api_token, '')
})

test('track resubmission sends only editable configuration', async t => {
  const d = setup(t, { track: track(), resubmitOf: entry({ image: 'verified:image' }) })
  await d.submit()
  assert.equal(d.calls[0].url, '/baseline-hub/submissions/7/resubmit')
  assert.equal(d.calls[0].body.model_path, undefined)
  assert.equal(d.calls[0].body.image, 'verified:image')
})

test('copying replaces pending command compilation and free-form configuration', async t => {
  const compile = deferred()
  const d = setup(t, { get: async () => ({ data: [entry()] }), post: () => compile.promise })
  await flush()
  inputCommand(d)
  const pending = d.submit()
  d.startFrom(7)
  compile.resolve({ data: { image: 'late:image', engine_args: { tp: 16 } } })
  await pending
  assert.equal(d.form.value.image, 'old:image')
  assert.equal(d.argRows.value[0].value, '8')
  assert.equal(d.calls.length, 1)
})

test('node validation uses world size and per-node GPU ask', async t => {
  const d = setup(t)
  d.form.value.machine_count = 3
  d.form.value.argsText = '{"tp":8}'
  await d.submit()
  assert.equal(d.calls.length, 0)
  d.form.value.machine_count = 2
  await d.submit()
  assert.equal(d.calls.length, 0)
  d.form.value.gpu_count = 4
  await d.submit()
  assert.equal(d.calls[0].body.machine_count, 2)
  assert.equal(d.calls[0].body.gpu_count, 4)
})

test('explicit discard ignores an old compile and clears rows and focus', async t => {
  const compile = deferred()
  const d = setup(t, { post: () => compile.promise })
  inputCommand(d)
  d.commandFocused.value = true
  d.focusedPane.value = 'args'
  const pending = d.submit()
  d.discardDraft()
  d.open.value = false
  d.open.value = true
  compile.resolve({ data: { image: 'late:image', engine_args: { tp: 16 } } })
  await pending
  assert.equal(d.form.value.image, '')
  assert.deepEqual(d.argRows.value, [])
  assert.equal(d.commandFocused.value, false)
  assert.equal(d.focusedPane.value, '')
  assert.equal(d.busy.value, false)
})

test('late submission success or error cannot close or poison a reopened draft', async t => {
  for (const fail of [false, true]) {
    const response = deferred()
    const d = setup(t, { post: () => response.promise })
    const pending = d.submit()
    d.discardDraft()
    d.open.value = false
    d.open.value = true
    if (fail) response.reject(new Error('late outage'))
    else response.resolve({ data: { id: 88 } })
    await pending
    assert.equal(d.open.value, true)
    assert.equal(d.uncertain.value, false)
    assert.deepEqual(d.submitted, [])
    assert.deepEqual(d.errors, [])
  }
})

test('late account and source-entry reads cannot overwrite a new session', async t => {
  const account = deferred(), entries = deferred()
  let reads = 0
  const d = setup(t, { track: track(), get: url => {
    if (++reads <= 2) return url === '/auth/me' ? account.promise : entries.promise
    return Promise.resolve({ data: url === '/auth/me' ? { username: 'New' } : [] })
  } })
  d.open.value = false
  d.open.value = true
  await flush()
  account.resolve({ data: { username: 'Old', has_contest_token: true } })
  entries.resolve({ data: [entry()] })
  await flush()
  assert.equal(d.publishedAs.value, 'New')
  assert.equal(d.hasSavedToken.value, false)
  assert.deepEqual(d.entries.value, [])
})

test('scope disposal cancels debounces and ignores in-flight responses', async t => {
  const compile = deferred()
  const d = setup(t, { post: () => compile.promise })
  inputCommand(d)
  const pending = d.submit()
  d.dispose()
  compile.resolve({ data: { image: 'late:image', engine_args: { tp: 16 } } })
  await pending
  await new Promise(resolve => setTimeout(resolve, 650))
  assert.equal(d.calls.length, 1)
  assert.equal(d.form.value.image, '')
  assert.deepEqual(d.submitted, [])
})


test('scope disposal cancels command and JSON debounces before they run', async t => {
  const d = setup(t)
  inputCommand(d)
  d.form.value.envText = '{"LATE":"1"}'
  d.onEnvText()
  d.dispose()
  await new Promise(resolve => setTimeout(resolve, 650))
  assert.equal(d.calls.length, 0)
  assert.deepEqual(d.envRows.value, [])
})

test('an obsolete request cannot clear the busy state of a new submission', async t => {
  const old = deferred(), current = deferred()
  let requests = 0
  const d = setup(t, { post: () => ++requests === 1 ? old.promise : current.promise })
  const first = d.submit()
  d.open.value = false
  d.open.value = true
  const second = d.submit()
  old.resolve({ data: { id: 1 } })
  await first
  assert.equal(d.busy.value, true)
  assert.deepEqual(d.submitted, [])
  current.resolve({ data: { id: 2 } })
  await second
  assert.equal(d.busy.value, false)
  assert.deepEqual(d.submitted, [2])
})

test('invalid JSON reveals its exact advanced group before sending anything', async t => {
  const d = setup(t)
  d.form.value.envText = '{invalid'
  await d.submit()
  assert.deepEqual(d.advanced.value, ['launch'])
  assert.equal(d.asJson.value, true)
  assert.equal(d.validationTarget.value, 'env')
  assert.equal(d.jsonBad.value.env, true)
  assert.equal(d.calls.length, 0)
})

test('invalid multi-node placement reveals resource settings', async t => {
  const d = setup(t, { track: track() })
  d.form.value.machine_count = 3
  d.form.value.argsText = '{"tp":8}'
  await d.submit()
  assert.equal(d.validationTarget.value, 'nodes')
  assert.deepEqual(d.advanced.value, ['placement'])
  assert.equal(d.calls.length, 0)
})

test('server field validation opens the corresponding settings without marking submission uncertain', async t => {
  const d = setup(t, { post: async () => {
    const error = new Error('Invalid environment')
    error.response = { status: 422, data: { detail: [{ loc: ['body', 'extra_env'], msg: 'invalid' }] } }
    throw error
  } })
  await d.submit()
  assert.equal(d.validationTarget.value, 'env')
  assert.deepEqual(d.advanced.value, ['launch'])
  assert.equal(d.uncertain.value, false)
})


test('draft changes survive reopening only in the same track and explicit discard resets them', async t => {
  const d = setup(t, { track: track() })
  assert.equal(d.dirty.value, false)
  d.form.value.name = 'My draft'
  assert.equal(d.dirty.value, true)
  d.open.value = false
  d.open.value = true
  assert.equal(d.form.value.name, 'My draft')
  d.discardDraft()
  assert.equal(d.form.value.name, '')
  assert.equal(d.dirty.value, false)
  d.form.value.name = 'Other draft'
  d.open.value = false
  d.trackRef.value = { ...track(), id: 99, slug: 'different' }
  d.open.value = true
  assert.equal(d.form.value.name, '')
})

test('reopening a command draft still compiles before sending and success clears it', async t => {
  const d = setup(t)
  inputCommand(d)
  d.open.value = false
  d.open.value = true
  await d.submit()
  assert.match(d.calls[0].url, /\/parse$/)
  assert.equal(d.calls.at(-1).body.engine_args.tp, '8')
  d.open.value = true
  assert.equal(d.form.value.command, '')
  assert.equal(d.dirty.value, false)
})


test('closing and reopening cannot clear an unconfirmed submission outcome', async t => {
  const d = setup(t, { post: async () => { throw new Error('lost response') } })
  await d.submit()
  d.open.value = false
  d.open.value = true
  assert.equal(d.uncertain.value, true)
  await d.submit()
  assert.equal(d.calls.length, 1)
})

test('a prefilled version with a different verified engine starts clean', async t => {
  const d = setup(t, { track: { ...track(), images: [
    { image: 'sglang:image', engine: 'sglang' }, { image: 'vllm:image', engine: 'vllm' },
  ] }, resubmitOf: entry({ image: 'vllm:image', engine: 'vllm' }) })
  await flush()
  assert.equal(d.form.value.engine, 'vllm')
  assert.equal(d.dirty.value, false)
})
