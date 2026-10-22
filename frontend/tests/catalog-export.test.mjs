import { test } from 'node:test'
import assert from 'node:assert/strict'
import { effectScope, reactive, toRef } from 'vue'
import { useCatalogExport } from '../src/platform/catalog/useCatalogExport.ts'

const document = (overrides = {}) => ({
  valid: true, existing: false, name: 'demo', hf: 'Org/Model', hf_suggestion: 'Guess/Model',
  display_name: 'Demo', description: '', family: '', tags: [],
  files: [{ path: 'models/demo/base.yaml', kind: 'variant', text: 'reviewed' }],
  variants: [{ id: 'base', inputs: [] }], errors: [], warnings: [], details: [],
  ...overrides,
})
function setup(t, { track = false, get, post } = {}) {
  const calls = []
  const props = reactive({
    modelValue: false, trackSlug: track ? 'demo' : undefined,
    candidates: [{ run_id: 1, label: 'base' }, { run_id: 2, label: 'opt' }],
    initial: [{ run_id: 1, role: 'baseline' }, { run_id: 2, role: 'optimized' }],
  })
  const api = {
    async get(path) { calls.push({ path }); return get ? get(path) : { data: [] } },
    async post(path, body) {
      calls.push({ path, body })
      if (post) return post(path, body)
      return { data: path === '/catalog-export/preview' ? document()
        : { id: 8, status: 'done', report_ids: { zh: 9 } } }
    },
    async put() {},
  }
  const scope = effectScope()
  const flow = scope.run(() => useCatalogExport(props, {
    open: toRef(props, 'modelValue'), api, t: key => key, text: zh => zh,
    errorText: error => error.message,
    wait: async () => {},
  }))
  t.after(() => scope.stop())
  const settle = () => new Promise(resolve => setImmediate(resolve))
  async function open() { props.modelValue = true; await settle() }
  return { flow, props, calls, api, scope, open, settle }
}

test('a sole owned run defaults to optimized while any board entry can be baseline', async t => {
  const { flow, props, open } = setup(t, { track: true })
  props.candidates = [
    { run_id: 1, label: 'mine', canOptimize: true },
    { run_id: 2, label: 'other', canOptimize: false },
    { run_id: 3, label: 'another baseline', canOptimize: false },
  ]
  await open()
  assert.equal(flow.optimizedRun.value, 1)
  assert.equal(flow.baselineRun.value, 2)
  assert.equal(flow.ready.value, true)
  assert.deepEqual(flow.baselineCandidates.value.map(row => row.run_id), [2, 3])
  assert.deepEqual(flow.optimizedCandidates.value.map(row => row.run_id), [1])
  flow.baselineRun.value = 3
  flow.changePair()
  assert.deepEqual(flow.body().variants.map(row => [row.run_id, row.role]), [
    [3, 'baseline'], [1, 'optimized'],
  ])
})

test('a fixed shared baseline leaves only owned runs available as optimized', async t => {
  const { flow, props, open } = setup(t, { track: true })
  props.fixedBaselineRunId = 1
  props.candidates = [
    { run_id: 1, label: 'reference', canOptimize: false },
    { run_id: 2, label: 'mine', canOptimize: true },
    { run_id: 3, label: 'other', canOptimize: false },
  ]
  props.initial = [{ run_id: 3, role: 'optimized' }]
  await open()
  assert.equal(flow.baselineRun.value, 1)
  assert.equal(flow.optimizedRun.value, 2)
  assert.deepEqual(flow.optimizedCandidates.value.map(row => row.run_id), [2])
})

test('refresh removes optimized selections that are no longer owned', async t => {
  const { flow, props, open } = setup(t, { track: true })
  await open()
  props.candidates = [
    { run_id: 1, label: 'baseline', canOptimize: false },
    { run_id: 2, label: 'no longer mine', canOptimize: false },
  ]
  flow.refreshCandidates()
  assert.equal(flow.optimizedRun.value, null)
  assert.equal(flow.ready.value, false)
  assert.deepEqual(flow.optimizedCandidates.value, [])
})

for (const existing of [true, false]) {
  test(`adopt confirmed HF only when the catalog model exists=${existing}`, async t => {
    const { flow, open } = setup(t, { post: async () => ({ data: document({ existing }) }) })
    await open()
    assert.equal(flow.hf.value, existing ? 'Org/Model' : '')
    assert.equal(flow.name.value, 'demo')
  })
}

test('track publications discover metadata before explicit report preparation', async t => {
  const { flow, open, calls } = setup(t, { track: true })
  await open()
  assert.equal(calls.length, 1)
  assert.ok(flow.preview.value)
  await flow.generateReport()
  assert.equal(flow.reportId.value, 9)
  assert.equal(flow.preview.value.valid, true)
  const preview = calls.find(call => call.path === '/catalog-export/preview' && call.body.report_id)
  assert.equal(preview.body.report_id, 9)
  assert.equal(preview.body.track_slug, 'demo')
  await flow.generateReport()
  assert.equal(calls.filter(call => call.path === '/autoreport/jobs' && call.body).length, 1)
})

test('editing draft fields invalidates the preview until preparation', async t => {
  const { flow, open } = setup(t)
  await open()
  flow.hf.value = 'Other/Model'
  assert.equal(flow.preview.value, null)
  const body = await flow.prepare()
  assert.equal(body.hf, 'Other/Model')
})

test('a pending preview cannot overwrite edited fields', async t => {
  let finish
  const { flow, props, open, settle } = setup(t, {
    post: () => new Promise(resolve => { finish = resolve }),
  })
  await open()
  flow.hf.value = 'Edited/Model'
  finish({ data: document({ existing: true }) })
  await settle()
  assert.equal(flow.preview.value, null)
  assert.equal(flow.hf.value, 'Edited/Model')
  assert.equal(flow.loading.value, false)
  props.modelValue = false
})

test('closing and reopening ignores the previous dialog preview response', async t => {
  const completions = []
  const { flow, props, open, settle } = setup(t, {
    post: () => new Promise(resolve => { completions.push(resolve) }),
  })
  await open()
  props.modelValue = false
  await settle()
  await open()
  completions[0]({ data: document({ name: 'obsolete' }) })
  await settle()
  assert.equal(flow.name.value, '')
  const reload = flow.load()
  completions[1]({ data: document({ name: 'current' }) })
  await reload
  await settle()
  assert.equal(flow.name.value, 'current')
})

test('simultaneous generation actions do not create duplicate report jobs', async t => {
  let finish
  const { flow, open, calls, settle } = setup(t, {
    track: true,
    get: () => new Promise(resolve => { finish = resolve }),
  })
  await open()
  const pending = flow.generateReport()
  await flow.generateReport()
  assert.equal(calls.filter(c => !c.body).length, 1)
  // Cancel before resolving; no obsolete request may start a report.
  flow.optimizedRun.value = 1
  flow.changePair()
  finish({ data: [] })
  await pending
  await settle()
  assert.equal(calls.filter(c => c.path === '/autoreport/jobs').length, 0)
  assert.equal(flow.reportId.value, null)
})

test('disposing the export stops late results from mutating state or starting work', async t => {
  let finish
  const { flow, open, scope, settle } = setup(t, {
    post: () => new Promise(resolve => { finish = resolve }),
  })
  await open()
  scope.stop()
  finish({ data: document() })
  await settle()
  assert.equal(flow.preview.value, null)
  assert.equal(flow.name.value, '')
})

test('a late environment decision cannot reload or report errors in a reopened dialog', async t => {
  const { flow, props, api, open, settle, calls } = setup(t)
  let fail
  api.put = () => new Promise((_resolve, reject) => { fail = reject })
  await open()
  const pending = flow.decide('TOKEN', false)
  props.modelValue = false
  await settle()
  await open()
  fail(new Error('obsolete environment decision'))
  await pending
  await settle()
  assert.equal(flow.error.value, '')
  assert.equal(calls.filter(call => call.path === '/catalog-export/preview').length, 1)
})

test('unified preparation resolves reports and returns only an explicitly confirmed draft', async t => {
  const { flow, open, calls } = setup(t, { track: true })
  await open()
  assert.equal(calls.some(c => c.path === '/autoreport/jobs'), false)
  assert.equal(await flow.prepare(), null)
  flow.hf.value = 'Confirmed/Model'
  const body = await flow.prepare()
  assert.equal(body.report_id, 9)
  assert.equal(body.hf, 'Confirmed/Model')
  assert.equal(calls.some(c => c.path.endsWith('/submit')), false)
})

test('unknown environment variables block publication until explicitly decided', async t => {
  const { flow, open } = setup(t, {
    post: async (_path, body) => ({ data: document({ existing: true, variants: [{ id: 'base', inputs:
      body.env_categories.CUSTOM ? [] : [{ kind: 'env', name: 'CUSTOM', category: 'unknown', action: 'left out' }],
    }] }) }),
  })
  await open()
  assert.equal(await flow.prepare(), null)
  await flow.decide('CUSTOM', false)
  const body = await flow.prepare()
  assert.equal(body.env_categories.CUSTOM, 'site')
})

test('closing during preparation never returns an obsolete draft', async t => {
  const { flow, props, api, open } = setup(t)
  await open()
  flow.hf.value = 'Confirmed/Model'
  let finish
  api.post = () => new Promise(resolve => { finish = resolve })
  const pending = flow.prepare()
  props.modelValue = false
  finish({ data: document() })
  assert.equal(await pending, null)
})

test('refreshing an editable draft adopts new candidates and a changed fixed baseline', async t => {
  const { flow, props, open, settle } = setup(t, { track: true })
  await open()
  flow.hf.value = 'Confirmed/Model'
  props.modelValue = false
  props.candidates = [{ run_id: 3, label: 'new base' }, { run_id: 4, label: 'new opt' }]
  props.initial = [{ run_id: 3, role: 'baseline' }, { run_id: 4, role: 'optimized' }]
  props.fixedBaselineRunId = 3
  props.modelValue = true
  flow.refreshCandidates()
  await settle()
  assert.deepEqual(flow.body().variants.map(v => v.run_id), [3, 4])
  assert.equal(flow.baselineRun.value, 3)
  assert.equal(flow.hf.value, 'Confirmed/Model')
  assert.equal(flow.reportId.value, null)
})

test('retrying preparation retries a failed report task instead of starting a duplicate', async t => {
  const { flow, open, calls } = setup(t, {
    track: true,
    get: async path => ({ data: path === '/autoreport/jobs/8' ? { id: 8, status: 'failed' } : [] }),
    post: async path => ({ data: path === '/catalog-export/preview' ? document({ existing: true })
      : path.endsWith('/retry') ? { id: 8, status: 'done', report_ids: { zh: 9 } }
      : { id: 8, status: 'failed', error: 'report failed' } }),
  })
  await open()
  assert.equal(await flow.prepare(), null)
  assert.equal(flow.reportFailed.value, true)
  const body = await flow.prepare()
  assert.equal(body.report_id, 9)
  assert.equal(calls.filter(c => c.path === '/autoreport/jobs' && c.body).length, 1)
  assert.equal(calls.filter(c => c.path === '/autoreport/jobs/8/retry').length, 1)
})
