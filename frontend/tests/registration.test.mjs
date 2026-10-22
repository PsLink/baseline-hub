import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import vm from 'node:vm'
import ts from 'typescript'

function setup() {
  const vue = readFileSync(new URL('../src/Login.vue', import.meta.url), 'utf8')
  const script = vue.match(/<script setup lang="ts">([\s\S]*?)<\/script>/)[1]
    .replace(/^import .*$/gm, '')
  const calls = [], redirects = []
  let session
  const context = vm.createContext({
    ref: value => ({ value }),
    onMounted: () => {},
    useRoute: () => ({ query: {} }),
    useRouter: () => ({ push: async path => redirects.push(path) }),
    api: async (...args) => { calls.push(args); return { user: { id: 1 }, csrf: 'csrf' } },
    setSession: value => { session = value },
  })
  vm.runInContext(ts.transpileModule(script + `
globalThis.form = { username, password, confirmation, registering, error, busy, switchMode, submit }`, {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
  }).outputText, context)
  return { form: context.form, calls, redirects, session: () => session }
}

test('registration confirmation prevents requests and switching clears passwords', async () => {
  const { form, calls } = setup()
  form.switchMode()
  form.password.value = 'secret123'
  form.confirmation.value = 'different'
  await form.submit()
  assert.equal(calls.length, 0)
  assert.match(form.error.value, /不一致/)
  form.switchMode()
  assert.equal(form.password.value, '')
  assert.equal(form.confirmation.value, '')
  assert.equal(form.error.value, '')
})

test('register and login select their endpoints and establish a session', async () => {
  for (const register of [false, true]) {
    const { form, calls, redirects, session } = setup()
    if (register) form.switchMode()
    form.username.value = 'alice'
    form.password.value = form.confirmation.value = 'secret123'
    await form.submit()
    assert.equal(calls.length, 1)
    assert.equal(calls[0][0], register ? '/auth/register' : '/auth/login')
    assert.equal(calls[0][1], 'POST')
    assert.equal(calls[0][2].username, 'alice')
    assert.equal(session().user.id, 1)
    assert.deepEqual(redirects, ['/'])
    assert.equal(form.password.value, '')
    assert.equal(form.busy.value, false)
  }
})
