import { test } from 'node:test'
import assert from 'node:assert/strict'
import { effectScope, nextTick } from 'vue'
import { createRouter, createMemoryHistory } from 'vue-router'
import { createUrlState, queryString, queryBoolean, queryPage, queryChoice } from '../src/platform/composables/pageState.ts'

const flush = async () => { await nextTick(); await new Promise(resolve => setImmediate(resolve)) }
async function setup(t, url) {
  const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/tracks', component: {} }] })
  await router.push(url)
  const scope = effectScope()
  const state = scope.run(() => createUrlState(router, {
    q: queryString(), mine: queryBoolean(false), page: queryPage(), size: queryChoice('20', ['20', '50']),
  }))
  t.after(() => scope.stop())
  return { router, state }
}

test('URL state restores filters and pagination and preserves unrelated parameters', async t => {
  const { router, state } = await setup(t, '/tracks?q=Qwen&mine=1&page=3&source=shared')
  assert.equal(state.q, 'Qwen'); assert.equal(state.mine, true); assert.equal(state.page, 3)
  state.q = 'Llama'; state.page = 2
  await flush()
  assert.equal(router.currentRoute.value.query.q, 'Llama')
  assert.equal(router.currentRoute.value.query.page, '2')
  assert.equal(router.currentRoute.value.query.source, 'shared')
  state.q = ''; state.mine = false; state.page = 1
  await flush()
  assert.deepEqual(router.currentRoute.value.query, { source: 'shared' })
})

test('URL changes restore state without overwriting pagination and validate malformed values', async t => {
  const { router, state } = await setup(t, '/tracks?page=-2&mine=garbage&size=400&q=a&q=b')
  assert.equal(state.page, 1); assert.equal(state.mine, false); assert.equal(state.size, '20'); assert.equal(state.q, '')
  await router.push('/tracks?q=shared&page=7&size=50&mine=1')
  await flush()
  assert.equal(state.q, 'shared'); assert.equal(state.page, 7); assert.equal(state.size, '50'); assert.equal(state.mine, true)
  router.back(); await flush()
  assert.equal(state.page, 1); assert.equal(state.q, '')
})

test('query-only navigation keeps scroll while path navigation and history restore use their positions', async () => {
  const { navigationScroll } = await import('../src/platform/composables/pageState.ts')
  assert.equal(navigationScroll('/tracks', '/tracks', null), false)
  assert.deepEqual(navigationScroll('/tracks/a', '/tracks', null), { top: 0 })
  const saved = { left: 0, top: 580 }
  assert.equal(navigationScroll('/tracks', '/tracks', saved), saved)
})

test('parent URL and scroll are restored only after returned page data is ready', async t => {
  const { createRenderer, h, ref } = await import('vue')
  const { RouterView } = await import('vue-router')
  const { usePageScroll, rememberedPage } = await import('../src/platform/composables/pageState.ts')
  const originalWindow = globalThis.window
  const scrolls = []
  globalThis.window = { scrollY: 0, scrollTo: position => scrolls.push(position) }
  const ready = ref(false)
  const List = { setup() { usePageScroll(() => ready.value); return () => null } }
  const router = createRouter({ history: createMemoryHistory(), routes: [
    { path: '/remembered-list', component: List }, { path: '/detail', component: { render: () => null } },
  ] })
  await router.push('/remembered-list?q=Qwen&page=3')
  const renderer = createRenderer({
    createComment: () => ({}), insert() {}, remove() {}, parentNode: () => null, nextSibling: () => null,
  })
  const app = renderer.createApp({ render: () => h(RouterView) })
  app.use(router); app.mount({})
  t.after(() => { app.unmount(); globalThis.window = originalWindow })
  assert.equal(scrolls.length, 0)
  ready.value = true; await flush()
  globalThis.window.scrollY = 640
  await router.push('/detail'); await flush()
  assert.equal(rememberedPage('/remembered-list'), '/remembered-list?q=Qwen&page=3')
  ready.value = false
  const beforeReturn = scrolls.length
  await router.push(rememberedPage('/remembered-list')); await flush()
  assert.equal(scrolls.length, beforeReturn)
  ready.value = true; await flush()
  assert.deepEqual(scrolls.at(-1), { top: 640, behavior: 'instant' })
})
