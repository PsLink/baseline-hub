import { test } from 'node:test'
import assert from 'node:assert/strict'
import { createComparisonReportFlow } from '../src/features/autoreport/comparisonReport.ts'

const pair = { trackSlug: 'demo', baselineRunId: 1, optimizedRunId: 2 }
const done = { id: 8, status: 'done', report_ids: { zh: 9 } }
function setup(overrides = {}) {
  const calls = []
  const api = {
    async get(path) {
      calls.push(path)
      return { data: path === '/agent/v1/reports' || path === '/autoreport/jobs' ? [] : done }
    },
    async post(path, body) { calls.push({ path, body }); return { data: done } },
    ...overrides,
  }
  const flow = createComparisonReportFlow(api, { wait: async () => {} })
  flow.select(pair)
  return { flow, api, calls }
}

test('reuse only the exact baseline and single optimized run, preferring Chinese', async () => {
  const { flow, calls } = setup({
    async get(path) {
      calls.push(path)
      return { data: [
        { id: 10, baseline_run_id: 2, attempt_run_ids: [1], lang: 'zh' },
        { id: 11, baseline_run_id: 1, attempt_run_ids: [2, 3], lang: 'zh' },
        { id: 12, baseline_run_id: 1, attempt_run_ids: [2], lang: 'en' },
        { id: 13, baseline_run_id: 1, attempt_run_ids: [2], lang: 'zh' },
      ] }
    },
  })
  assert.equal(await flow.ensure(), 13)
  assert.equal(flow.state.reportId, 13)
  assert.deepEqual(calls, ['/agent/v1/reports'])
})

test('reuse running report jobs and cache the completed comparison', async () => {
  const { flow, calls } = setup({
    async get(path) {
      calls.push(path)
      if (path === '/agent/v1/reports') return { data: [] }
      if (path === '/autoreport/jobs') return { data: [
        { id: 8, status: 'running', baseline_run_id: 1, attempt_run_ids: [2] },
      ] }
      return { data: done }
    },
  })
  assert.equal(await flow.ensure(), 9)
  assert.equal(await flow.ensure(), 9)
  assert.deepEqual(calls, ['/agent/v1/reports', '/autoreport/jobs', '/autoreport/jobs/8'])
})

test('create a report once for simultaneous manual requests', async () => {
  let finish
  const { flow, calls } = setup({
    post(path, body) {
      calls.push({ path, body })
      return new Promise(resolve => { finish = resolve })
    },
  })
  const first = flow.ensure()
  const second = flow.ensure()
  await new Promise(resolve => setImmediate(resolve))
  assert.equal(calls.filter(call => typeof call === 'object').length, 1)
  assert.deepEqual(calls[2], {
    path: '/autoreport/jobs',
    body: { baseline_run_id: 1, attempt_run_ids: [2], langs: ['en', 'zh'] },
  })
  finish({ data: done })
  assert.deepEqual(await Promise.all([first, second]), [9, 9])
})

test('changing the comparison during lookup prevents an obsolete job from starting', async () => {
  let finish
  const { flow, calls } = setup({
    get(path) { calls.push(path); return new Promise(resolve => { finish = resolve }) },
  })
  const pending = flow.ensure()
  flow.select({ ...pair, optimizedRunId: 3 })
  finish({ data: [] })
  assert.equal(await pending, null)
  assert.equal(flow.state.reportId, null)
  assert.deepEqual(calls, ['/agent/v1/reports'])
})

test('lookup failures never create a report and can recover on another manual request', async () => {
  const { flow, api, calls } = setup({ get: async () => { throw new Error('unavailable') } })
  await assert.rejects(flow.ensure(), /unavailable/)
  assert.equal(calls.length, 0)
  api.get = async () => ({ data: [] })
  assert.equal(await flow.ensure(), 9)
  assert.equal(calls.length, 1)
})

test('failed starts are checked again instead of reusing a rejected promise', async () => {
  let starts = 0
  const { flow } = setup({
    async post() {
      if (++starts === 1) throw new Error('response lost')
      return { data: done }
    },
  })
  await assert.rejects(flow.ensure(), /response lost/)
  assert.equal(await flow.ensure(), 9)
  assert.equal(starts, 2)
})

test('failed jobs require explicit retry of the same job', async () => {
  const failed = { id: 8, status: 'failed', error: 'model unavailable', report_ids: {} }
  const { flow, calls } = setup({
    async get(path) {
      calls.push(path)
      return { data: path === '/autoreport/jobs/8' ? failed : [] }
    },
    async post(path) {
      calls.push(path)
      return { data: path.endsWith('/retry') ? done : failed }
    },
  })
  await assert.rejects(flow.ensure(), /model unavailable/)
  assert.equal(flow.state.failed, true)
  assert.equal(flow.state.reportId, null)
  await assert.rejects(flow.ensure(), /model unavailable/)
  assert.equal(calls.filter(path => path === '/autoreport/jobs/8/retry').length, 0)
  assert.equal(await flow.ensure({ retry: true }), 9)
  assert.equal(flow.state.failed, false)
  assert.equal(calls.at(-1), '/autoreport/jobs/8/retry')
})

test('reopening a failed comparison reuses the task completed elsewhere', async () => {
  const { flow, calls } = setup({
    async post(path) {
      calls.push({ path })
      return { data: { id: 8, status: 'failed', error: 'initial failure', report_ids: {} } }
    },
  })
  await assert.rejects(flow.ensure(), /initial failure/)
  flow.select(null)
  flow.select(pair)
  assert.equal(await flow.ensure(), 9)
  assert.equal(flow.state.reportId, 9)
  assert.equal(flow.state.failed, false)
  assert.equal(calls.filter(call => typeof call === 'object').length, 1)
  assert.equal(calls.at(-1), '/autoreport/jobs/8')
})

test('a cached failed task does not hide a matching report from another task', async () => {
  const failed = { id: 8, status: 'failed', error: 'initial failure', report_ids: {} }
  let completedElsewhere = false
  const { flow, calls } = setup({
    async get(path) {
      calls.push(path)
      if (path === '/autoreport/jobs/8') return { data: failed }
      return { data: completedElsewhere && path === '/agent/v1/reports' ? [
        { id: 10, baseline_run_id: 1, attempt_run_ids: [2], lang: 'zh' },
      ] : [] }
    },
    async post(path) { calls.push(path); return { data: failed } },
  })
  await assert.rejects(flow.ensure(), /initial failure/)
  completedElsewhere = true
  assert.equal(await flow.ensure({ retry: true }), 10)
  assert.equal(flow.state.failed, false)
  assert.equal(calls.filter(path => path === '/autoreport/jobs/8/retry').length, 0)
})

test('retry rechecks a failed task and waits if another page already restarted it', async () => {
  let reads = 0
  const { flow, calls } = setup({
    async get(path) {
      calls.push(path)
      if (path === '/autoreport/jobs/8') return { data: ++reads === 1
        ? { id: 8, status: 'running', report_ids: {} } : done }
      return { data: [] }
    },
    async post(path) {
      calls.push(path)
      return { data: { id: 8, status: 'failed', error: 'initial failure', report_ids: {} } }
    },
  })
  await assert.rejects(flow.ensure(), /initial failure/)
  assert.equal(await flow.ensure({ retry: true }), 9)
  assert.equal(flow.state.failed, false)
  assert.equal(reads, 2)
  assert.equal(calls.filter(path => path === '/autoreport/jobs/8/retry').length, 0)
})

test('a late failed-task refresh cannot assign a report to another comparison', async () => {
  const { flow, api } = setup({
    async post() {
      return { data: { id: 8, status: 'failed', error: 'initial failure', report_ids: {} } }
    },
  })
  await assert.rejects(flow.ensure(), /initial failure/)
  let finish
  api.get = () => new Promise(resolve => { finish = resolve })
  const pending = flow.ensure({ retry: true })
  assert.equal(typeof finish, 'function', 'refresh the failed task before retrying')
  flow.select({ ...pair, optimizedRunId: 3 })
  finish({ data: done })
  assert.equal(await pending, null)
  assert.equal(flow.state.reportId, null)
  assert.equal(flow.state.failed, false)
})

test('an unavailable failed-task refresh never sends a retry', async () => {
  const { flow, api, calls } = setup({
    async post(path) {
      calls.push(path)
      return { data: { id: 8, status: 'failed', error: 'initial failure', report_ids: {} } }
    },
  })
  await assert.rejects(flow.ensure(), /initial failure/)
  api.get = async () => { throw new Error('status unavailable') }
  await assert.rejects(flow.ensure({ retry: true }), /status unavailable/)
  assert.equal(flow.state.reportId, null)
  assert.equal(calls.filter(path => path === '/autoreport/jobs/8/retry').length, 0)
})

test('closing during a poll prevents further requests and report assignment', async () => {
  let finish
  const calls = []
  const paused = createComparisonReportFlow({
    get: async path => { calls.push(path); return { data: [] } },
    post: async () => ({ data: { id: 8, status: 'running' } }),
  }, { wait: () => new Promise(resolve => { finish = resolve }) })
  paused.select(pair)
  const pending = paused.ensure()
  await new Promise(resolve => setImmediate(resolve))
  paused.cancel()
  finish()
  assert.equal(await pending, null)
  assert.equal(paused.state.reportId, null)
  assert.deepEqual(calls, ['/agent/v1/reports', '/autoreport/jobs'])
})

test('a completed job without a report never enables export', async () => {
  const { flow } = setup({ post: async () => ({ data: { ...done, report_ids: {} } }) })
  await assert.rejects(flow.ensure(), /no report/)
  assert.equal(flow.state.reportId, null)
})
