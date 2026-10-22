import { reactive } from 'vue'

export interface User { id: number; username: string; role: string; contributor: string }
export interface Upstream { id: number | null; username: string | null; connected: boolean }
export interface Session { user: User; csrf?: string; upstream?: Upstream }
export const apiBase = `${import.meta.env?.BASE_URL || '/'}api`
export const auth = reactive({ user: null as User | null, csrf: '', upstream: null as Upstream | null })

export class ApiError extends Error {
  status: number
  code: string
  constructor(message: string, status = 0, code = '') { super(message); this.status = status; this.code = code }
}

export function setSession(result: Session) {
  auth.user = result.user
  if (result.csrf !== undefined) auth.csrf = result.csrf
  auth.upstream = result.upstream || null
}

export async function api<T = any>(path: string, method = 'GET', body?: unknown): Promise<T> {
  let response: Response
  try { response = await fetch(`${apiBase}${path}`, {
    method,
    credentials: 'same-origin',
    headers: {
      'Content-Type': 'application/json',
      ...(auth.csrf ? { 'X-CSRF-Token': auth.csrf } : {}),
    },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  }) } catch { throw new ApiError('连接中断，请检查网络后重试', 0, 'network_error') }
  const text = await response.text()
  let result: any = null
  if (text) {
    try { result = JSON.parse(text) }
    catch { throw new ApiError('服务返回了无效响应，请稍后重试', response.status, 'invalid_response') }
  }
  if (!response.ok) {
    if (response.status === 401) { auth.user = null; auth.csrf = ''; auth.upstream = null }
    if (result?.code === 'upstream_auth_required' && auth.upstream) auth.upstream.connected = false
    const detail = typeof result?.detail === 'string' ? result.detail
      : Array.isArray(result?.detail) ? result.detail.map((d: { msg: string }) => d.msg).join('；')
      : '请求未完成，请检查输入'
    throw new ApiError(detail, response.status, result?.code || '')
  }
  return result as T
}

export async function refreshAuth() {
  setSession(await api<Session>('/auth/me'))
}
