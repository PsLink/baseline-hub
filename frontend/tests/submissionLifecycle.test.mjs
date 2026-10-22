import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import vm from 'node:vm'
import * as vue from 'vue'
import { parse, compileScript } from 'vue/compiler-sfc'
import ts from 'typescript'
import { useSubmissionDraft } from '../src/features/hub/composables/useSubmissionDraft.ts'

const flush = () => new Promise(resolve => setImmediate(resolve))

async function mountDialog(t, options = {}, behavior = {}) {
  let leaveGuard
  const confirmations = []
  const calls = [], events = []
  const source = readFileSync(new URL('../src/features/hub/components/BaselineSubmitDialog.vue', import.meta.url), 'utf8')
  const { descriptor } = parse(source)
  const script = compileScript(descriptor, { id: 'submission-lifecycle-test' })
  const code = ts.transpileModule(script.content, {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
  }).outputText
  const originalWindow = globalThis.window
  globalThis.window = globalThis
  const modules = {
    vue,
    '@element-plus/icons-vue': { CopyDocument: {} },
    'vue-router': { onBeforeRouteLeave(guard) { leaveGuard = guard } },
    'element-plus': { ElMessageBox: { confirm: async (...args) => { confirmations.push(args); if (behavior.reject) throw new Error('cancel') } }, ElMessage: { error() {}, warning() {} } },
    '@/plugins/api': {
      api: { get: async () => ({ data: [] }), post: async (url, body) => {
        calls.push({ url, body }); return { data: { id: 23 } }
      } },
      apiErrorText: error => error.message,
      ConfigChips: {}, InfoHint: {}, useI18n: () => ({ t: key => key, locale: vue.ref('en') }),
    },
    '../types': {},
  }
  modules['../composables/useSubmissionDraft'] = { useSubmissionDraft }
  const context = vm.createContext({ exports: {}, window: globalThis.window, require: name => {
    assert.ok(name in modules, `Unexpected dependency: ${name}`)
    return modules[name]
  } })
  vm.runInContext(code, context)
  const renderer = vue.createRenderer({
    createComment: () => ({}), insert() {}, remove() {}, parentNode: () => null,
    nextSibling: () => null,
  })
  const app = renderer.createApp({ ...context.exports.default, render: () => null }, {
    modelValue: false, gpuTypes: [], flagCatalog: {}, ...options,
    'onUpdate:modelValue': value => events.push(['update:modelValue', value]),
    onSubmitted: id => events.push(['submitted', id]),
  })
  app.mount({})
  t.after(() => { app.unmount(); globalThis.window = originalWindow })
  return { state: app._instance.setupState, props: app._instance.props, calls, events, confirmations, leave: () => leaveGuard() }
}

test('dialog wrapper preserves a modified draft after closing and reopening', async t => {
  const { state, props } = await mountDialog(t)
  props.modelValue = true
  await flush()
  state.argRows.push({ key: 'tp', mode: 'text', value: '8', on: true })
  state.onUiEdit()
  props.modelValue = false
  await flush()
  props.modelValue = true
  await flush()
  assert.equal(state.argRows.length, 1)
  assert.match(state.form.command, /--tp 8/)
  assert.equal(state.dirty, true)
})


test('dialog wrapper initializes mounted-open props and wires submission events', async t => {
  const { state, calls, events } = await mountDialog(t, {
    modelValue: true,
    resubmitOf: {
      id: 9, name: 'Versioned', version: 3, engine: 'sglang', image: 'sglang:image',
      served_model_name: 'model', model_path: '/model', gpu_type: 'gpu', gpu_count: 2,
      precision: 'bf16', benchmark_slug: 'bench', max_run_minutes: 40,
      service_port: 28200, notes: '', engine_args: { tp: 2 },
    },
  })
  assert.equal(state.title, 'Evolve Versioned to v4')
  assert.equal(state.form.name, 'Versioned')
  await state.submit()
  assert.equal(calls[0].url, '/baseline-hub/submissions/9/resubmit')
  assert.equal(calls[0].body.engine_args.tp, '2')
  assert.deepEqual(events, [['update:modelValue', false], ['submitted', 23]])
})


test('close confirmation preserves editing on cancel and retains the draft on acceptance', async t => {
  const behavior = { reject: true }
  const { state, props, events, confirmations } = await mountDialog(t, {}, behavior)
  // Evaluate while closed, as the real template does, before opening it.
  assert.equal(state.dirty, false)
  props.modelValue = true
  await flush()
  state.form.name = 'Unfinished'
  assert.equal(state.dirty, true)
  await state.requestClose()
  assert.equal(events.length, 0)
  behavior.reject = false
  await state.requestClose()
  assert.deepEqual(events, [['update:modelValue', false]])
  assert.equal(confirmations.length, 2)
  assert.equal(state.form.name, 'Unfinished')
})

test('route guard handles retained drafts, busy requests and successful submissions', async t => {
  const behavior = { reject: true }
  const { state, leave } = await mountDialog(t, { modelValue: true }, behavior)
  assert.equal(await leave(), true)
  state.form.name = 'Draft'
  assert.equal(await leave(), false)
  behavior.reject = false
  assert.equal(await leave(), true)
  state.busy = true
  assert.equal(await leave(), false)
  state.busy = false
  await state.submit()
  assert.equal(await leave(), true)
})

test('explicit discard asks for confirmation and clears the draft', async t => {
  const { state, events } = await mountDialog(t, { modelValue: true })
  state.form.name = 'Draft'
  await state.discardAndClose()
  assert.equal(state.dirty, false)
  assert.equal(state.form.name, '')
  assert.deepEqual(events, [['update:modelValue', false]])
})
