import { test, afterEach } from 'node:test'
import assert from 'node:assert/strict'
import { AxiosError } from 'axios'
import { api } from '../src/platform/api/client.ts'
import { auth } from '../src/client.ts'

const original = api.defaults.adapter
afterEach(() => {
  api.defaults.adapter = original
  auth.user = null; auth.csrf = ''; auth.upstream = null
})
function login() {
  auth.user = { id: 1, username: 'alice', role: 'user', contributor: '' }
  auth.csrf = 'hub-csrf'
  auth.upstream = { id: 2, username: 'alice', connected: true }
}
test('original components use the Hub proxy with CSRF and no browser JWT', async () => {
  login()
  let captured
  api.defaults.adapter = async config => {
    captured = config
    return { config, status: 200, statusText: 'OK', headers: {}, data: { engine: 'sglang' } }
  }
  const response = await api.post('/baseline-hub/parse', { command: 'sglang serve /model' })
  assert.equal(captured.baseURL, '/api/platform/api')
  assert.equal(captured.withCredentials, true)
  assert.equal(captured.headers.get('X-CSRF-Token'), 'hub-csrf')
  assert.equal(captured.headers.get('Authorization'), undefined)
  assert.equal(response.data.engine, 'sglang')
})
test('backend authorization expiry preserves Hub identity and marks reconnect needed', async () => {
  login()
  api.defaults.adapter = async config => {
    throw new AxiosError('Reconnect', 'ERR_BAD_REQUEST', config, {}, {
      config, status: 409, headers: {}, data: { code: 'upstream_auth_required' },
    })
  }
  await assert.rejects(api.get('/baseline-hub/tracks'))
  assert.equal(auth.user.username, 'alice')
  assert.equal(auth.csrf, 'hub-csrf')
  assert.equal(auth.upstream.connected, false)
})
test('expired Hub session clears local UI identity', async () => {
  login()
  api.defaults.adapter = async config => {
    throw new AxiosError('Sign in', 'ERR_BAD_REQUEST', config, {}, {
      config, status: 401, headers: {}, data: { detail: 'Sign in required' },
    })
  }
  await assert.rejects(api.get('/baseline-hub/tracks'))
  assert.equal(auth.user, null)
  assert.equal(auth.csrf, '')
})
test('a lost submit response never retries a mutating request', async () => {
  login()
  let calls = 0
  api.defaults.adapter = async config => {
    calls++
    throw new AxiosError('Network lost', 'ERR_NETWORK', config)
  }
  await assert.rejects(api.post('/baseline-hub/tracks/qwen/submissions', { name: 'x' }))
  assert.equal(calls, 1)
})
