import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import vm from 'node:vm'
import * as vue from 'vue'
import { parse, compileScript } from 'vue/compiler-sfc'
import ts from 'typescript'
import { usePoll } from '../src/platform/utils/poll.ts'

const source = readFileSync(new URL('../src/features/autoreport/components/ReportJobs.vue', import.meta.url), 'utf8')
const { descriptor } = parse(source)
const script = compileScript(descriptor, { id: 'report-jobs-test' })
const code = ts.transpileModule(script.content, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
}).outputText

const flush = () => new Promise(resolve => setImmediate(resolve))

function mountJobs(t, load) {
  const timers = new Map()
  const originalWindow = globalThis.window
  const originalDocument = globalThis.document
  const document = new EventTarget()
  document.hidden = false
  globalThis.document = document
  globalThis.window = {
    setInterval(callback) { const id = timers.size + 1; timers.set(id, callback); return id },
    clearInterval(id) { timers.delete(id) },
  }
  const jobs = vue.reactive({
    list: [], open: null,
    async refresh() { jobs.list = await load() },
  })
  const modules = {
    vue,
    '@/plugins/api': {
      usePoll, api: {}, apiErrorText: error => error.message,
      relativeTime: () => '', useI18n: () => ({ t: key => key }),
    },
    'element-plus': { ElMessage: { error() {} } },
    '../store': { jobs, running: job => ['queued', 'running'].includes(job.status) },
  }
  const context = vm.createContext({
    exports: {}, require: name => {
      assert.ok(name in modules, `Unexpected dependency: ${name}`)
      return modules[name]
    },
  })
  vm.runInContext(code, context)
  // Run the complete compiled SFC setup with Vue's actual mount/unmount hooks.
  // Rendering is irrelevant to polling; a custom renderer needs no browser DOM.
  const renderer = vue.createRenderer({
    createComment: () => ({}), insert() {}, remove() {}, parentNode: () => null,
    nextSibling: () => null,
  })
  const app = renderer.createApp({ ...context.exports.default, render: () => null })
  app.mount({})
  let mounted = true
  function unmount() {
    if (!mounted) return
    app.unmount()
    mounted = false
  }
  t.after(() => {
    unmount()
    globalThis.window = originalWindow
    globalThis.document = originalDocument
  })
  return { jobs, timers, unmount, document }
}

test('report jobs recover on the next poll after the first request fails', async t => {
  let calls = 0
  const { jobs, timers } = mountJobs(t, async () => {
    if (++calls === 1) throw new Error('temporary outage')
    return [{ id: 1, status: 'running' }]
  })
  await flush()
  assert.equal(calls, 1)
  assert.equal(timers.size, 1)
  await [...timers.values()][0]()
  assert.equal(calls, 2)
  assert.equal(jobs.list[0].id, 1)
})

test('initial report loading never overlaps polling and unmount stops refreshing', async t => {
  let calls = 0
  let finish
  const { unmount, timers, document } = mountJobs(t, () => {
    calls++
    return new Promise(resolve => { finish = resolve })
  })
  assert.equal(calls, 1)
  assert.equal(timers.size, 1)
  const tick = [...timers.values()][0]
  await tick()
  assert.equal(calls, 1)
  unmount()
  assert.equal(timers.size, 0)
  finish([])
  await flush()
  await tick()
  document.dispatchEvent(new Event('visibilitychange'))
  await flush()
  assert.equal(calls, 1)
  assert.equal(timers.size, 0)
})
