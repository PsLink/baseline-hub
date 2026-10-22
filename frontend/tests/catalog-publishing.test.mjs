import { test } from 'node:test'
import assert from 'node:assert/strict'
import { effectScope, ref } from 'vue'
import { useCatalogPublication } from '../src/platform/catalog/useCatalogPublication.ts'

function setup(t, request) {
  const active = ref(true)
  const scope = effectScope()
  const publication = scope.run(() => useCatalogPublication(request, active))
  t.after(() => scope.stop())
  return { ...publication, active }
}
const settle = () => new Promise(resolve => setImmediate(resolve))

test('authorization is checked on opening and returning to a publication', async t => {
  let authorized = false
  const calls = []
  const p = setup(t, async path => {
    calls.push(path)
    return { login: 'user', publishing_authorized: authorized }
  })
  await settle()
  assert.equal(p.authorized.value, false)
  authorized = true
  p.active.value = false
  await settle()
  p.active.value = true
  await settle()
  assert.equal(p.authorized.value, true)
  assert.equal(calls.length, 2)
})

test('publication cannot be prepared without authorization', async t => {
  const calls = []
  const p = setup(t, async path => {
    calls.push(path)
    return { publishing_authorized: false }
  })
  await settle()
  await p.preview({ variants: [1] })
  assert.deepEqual(calls, ['/auth/github/identity'])
  assert.equal(p.state.job, null)
})

test('closing and reopening keeps the reviewed job and submits the same immutable ID', async t => {
  const calls = []
  const p = setup(t, async path => {
    calls.push(path)
    if (path === '/auth/github/identity') return { publishing_authorized: true }
    return { id: 'reviewed', status: path.endsWith('/submit') ? 'queued' : 'preview', files: [] }
  })
  await settle()
  await p.preview({ variants: [1] })
  p.active.value = false
  await settle()
  p.active.value = true
  await settle()
  assert.equal(p.state.job.id, 'reviewed')
  await p.submit()
  assert.equal(p.state.job.status, 'queued')
  assert.ok(calls.includes('/catalog-export/publications/reviewed/submit'))
})

test('late identity checks cannot overwrite a newer authorization result', async t => {
  let finish
  let count = 0
  const p = setup(t, () => ++count === 1 ? new Promise(resolve => { finish = resolve })
    : Promise.resolve({ publishing_authorized: true }))
  await p.loadIdentity()
  finish({ publishing_authorized: false })
  await settle()
  assert.equal(p.authorized.value, true)
})

test('returning focus after GitHub authorization automatically refreshes identity', async t => {
  const target = new EventTarget()
  globalThis.window = target
  t.after(() => { delete globalThis.window })
  let authorized = false
  const p = setup(t, async () => ({ publishing_authorized: authorized }))
  await settle()
  assert.equal(p.authorized.value, false)
  authorized = true
  target.dispatchEvent(new Event('focus'))
  await settle()
  assert.equal(p.authorized.value, true)
})
