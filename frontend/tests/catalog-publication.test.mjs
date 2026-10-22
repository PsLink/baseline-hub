import { test } from 'node:test'
import assert from 'node:assert/strict'
import { createPublicationFlow } from '../src/publication.ts'

function setup(request) {
  const state = { job: null, error: '', busy: false, bodyKey: '', reviewedKey: '' }
  return { state, flow: createPublicationFlow(request, state) }
}

test('editing the selection invalidates a reviewed diff and blocks submit', async () => {
  const calls = []
  const { flow, state } = setup(async (path) => {
    calls.push(path)
    return { id: 'one', status: 'preview', files: [{ diff: '+new' }] }
  })
  flow.change({ variants: [1] })
  await flow.preview({ variants: [1] })
  assert.equal(state.job.id, 'one')
  flow.change({ variants: [2] })
  await flow.submit()
  assert.equal(state.job, null)
  assert.equal(calls.length, 1)
})

test('lost submit response retains the same reviewed task for safe retry', async () => {
  let attempts = 0
  const calls = []
  const { flow, state } = setup(async path => {
    calls.push(path)
    if (path.endsWith('/submit') && attempts++ === 0) throw new Error('lost response')
    return { id: 'one', status: path.endsWith('/submit') ? 'queued' : 'preview', files: [] }
  })
  const body = { variants: [1] }
  flow.change(body)
  await flow.preview(body)
  await flow.submit()
  assert.equal(state.job.id, 'one')
  assert.match(state.error, /lost response/)
  await flow.submit()
  assert.equal(state.job.status, 'queued')
  assert.equal(calls.filter(path => path.endsWith('/one/submit')).length, 2)
})

test('late preview response cannot replace the new selection', async () => {
  let resolve
  const { flow, state } = setup(() => new Promise(done => { resolve = done }))
  flow.change({ variants: [1] })
  const pending = flow.preview({ variants: [1] })
  flow.change({ variants: [2] })
  resolve({ id: 'old', status: 'preview' })
  await pending
  assert.equal(state.job, null)
})

test('poll only active tasks and retain the returned PR link', async () => {
  let calls = 0
  const { flow, state } = setup(async () => {
    calls++
    return { id: 'one', status: 'succeeded', pr_url: 'https://github.com/a/b/pull/1' }
  })
  state.job = { id: 'one', status: 'queued' }
  await flow.poll()
  await flow.poll()
  assert.equal(calls, 1)
  assert.equal(state.job.pr_url, 'https://github.com/a/b/pull/1')
})

test('status polling preserves the immutable review without fetching files again', async () => {
  const calls = []
  const { flow, state } = setup(async path => {
    calls.push(path)
    return { id: 'one', status: 'succeeded', pr_url: 'https://github.com/a/b/pull/1', pr_number: 1, error: '' }
  })
  const files = [{ path: 'models/demo/base.yaml', kind: 'variant', diff: '+reviewed' }]
  state.job = {
    id: 'one', status: 'queued', title: 'Reviewed title', body: 'Reviewed description',
    repository: 'a/b', base_sha: 'frozen-base', warnings: ['reviewed warning'], files,
  }
  await flow.poll()
  assert.deepEqual(calls, ['/catalog-export/publications/one/status'])
  assert.equal(state.job.status, 'succeeded')
  assert.equal(state.job.files, files)
  assert.equal(state.job.title, 'Reviewed title')
  assert.equal(state.job.body, 'Reviewed description')
  assert.equal(state.job.base_sha, 'frozen-base')
  assert.equal(state.job.repository, 'a/b')
  assert.deepEqual(state.job.warnings, ['reviewed warning'])
})

test('a late status response cannot replace a new review of the same publication', async () => {
  let resolve
  const { flow, state } = setup(path => {
    if (path.includes('/publications/')) return new Promise(done => { resolve = done })
    return Promise.resolve({ id: 'one', status: 'preview', files: [{ diff: '+current' }] })
  })
  flow.change({ variants: [1] })
  state.job = { id: 'one', status: 'queued' }
  const pending = flow.poll()
  flow.change({ variants: [2] })
  await flow.preview({ variants: [2] })
  resolve({ id: 'one', status: 'succeeded' })
  await pending
  assert.equal(state.job.status, 'preview')
  assert.equal(state.job.files[0].diff, '+current')
})
