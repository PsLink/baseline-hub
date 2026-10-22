import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import vm from 'node:vm'
import * as vue from 'vue'
import { parse, compileScript } from 'vue/compiler-sfc'
import ts from 'typescript'
import { useCatalogExport } from '../src/platform/catalog/useCatalogExport.ts'
import { useCatalogPublication } from '../src/platform/catalog/useCatalogPublication.ts'

const filename = fileURLToPath(new URL('../src/platform/components/CatalogExportDialog.vue', import.meta.url))
const { descriptor } = parse(readFileSync(filename, 'utf8'), { filename })
const script = compileScript(descriptor, { id: 'catalog-export-lifecycle-test' })
const code = ts.transpileModule(script.content, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
}).outputText

test('the real catalog dialog prepares a single immutable GitHub review and invalidates edits', async t => {
  const calls = [], events = []
  const modules = {
    vue,
    './CatalogPublicationReview.vue': {},
    '../../client': { api: async (path, method, body) => {
      calls.push({ path, method, body })
      if (path === '/auth/github/identity') return { login: 'user', publishing_authorized: true }
      return { id: 'review', status: path.endsWith('/submit') ? 'queued' : 'preview', files: [] }
    } },
    '../api/client': { api: {
      get: async path => {
        calls.push(path)
        return { data: [{ id: 9, baseline_run_id: 1, attempt_run_ids: [2], lang: 'zh' }] }
      },
      post: async (path, body) => {
        calls.push({ path, body })
        return { data: {
          name: 'demo', hf: 'Org/Model', existing: true, valid: true, files: [], variants: [], tags: [], errors: [], warnings: [], details: [],
        } }
      },
    } },
    '../utils/apiError': { apiErrorText: error => error.message },
    '../utils/clipboard': { copyText() {} },
    '../i18n': { useI18n: () => ({ t: key => key, locale: vue.ref('zh') }) },
    '../catalog/useCatalogExport': { useCatalogExport },
    '../catalog/useCatalogPublication': { useCatalogPublication },
  }
  const context = vm.createContext({
    exports: {}, require: name => {
      assert.ok(name in modules, `Unexpected dependency: ${name}`)
      return modules[name]
    },
  })
  vm.runInContext(code, context)
  const renderer = vue.createRenderer({
    createComment: () => ({}), insert() {}, remove() {}, parentNode: () => null,
    nextSibling: () => null,
  })
  const app = renderer.createApp({ ...context.exports.default, render: () => null }, {
    modelValue: true, trackSlug: 'demo',
    candidates: [{ run_id: 1, label: 'base' }, { run_id: 2, label: 'opt' }],
    initial: [{ run_id: 1, role: 'baseline' }, { run_id: 2, role: 'optimized' }],
    'onUpdate:modelValue': value => events.push(value),
  })
  app.mount({})
  t.after(() => app.unmount())
  const state = app._instance.setupState
  await new Promise(resolve => setImmediate(resolve))
  assert.equal(calls.some(c => c.path === '/autoreport/jobs'), false)
  assert.equal(state.ready, true)
  await state.preparePublication()
  assert.equal(state.step, 'review')
  assert.equal(state.state.job.id, 'review')
  assert.equal(calls.find(c => c.path === '/catalog-export/publication-previews').body.report_id, 9)
  assert.equal(calls.some(c => c.path?.endsWith('/submit')), false)
  state.includeVolumes = true
  assert.equal(state.preview, null)
  assert.equal(state.step, 'configure')
  assert.equal(state.state.job, null)
  await state.submit()
  assert.equal(calls.some(c => c.path?.endsWith('/submit')), false)
  await state.preparePublication()
  await state.submit()
  assert.equal(state.state.job.status, 'queued')
  assert.equal(state.submitted, true)
  state.open = false
  assert.deepEqual(events, [false])
})
