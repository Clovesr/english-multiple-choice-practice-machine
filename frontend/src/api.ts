const API_ROOT = '/api'

export async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  const headers = new Headers(options.headers)
  if (!(options.body instanceof FormData)) {
    headers.set('Content-Type', 'application/json')
  }
  const response = await fetch(`${API_ROOT}${path}`, { ...options, headers })
  if (!response.ok) {
    let message = `${response.status} ${response.statusText}`
    let detail: unknown = null
    let code = ''
    let details: Record<string, unknown> = {}
    try {
      const data = await response.json()
      if (data && typeof data.code === 'string' && typeof data.message === 'string') {
        // 契约式错误：{ code, message, details, recoverable }（API_CONTRACT §1）
        code = data.code
        message = data.message
        details = data.details ?? {}
        detail = data
      } else {
        detail = data.detail
        message = typeof detail === 'string'
          ? detail
          : (detail as any)?.message || JSON.stringify(detail)
      }
    } catch {
      // Keep status text.
    }
    const error = new Error(message) as ApiError
    error.status = response.status
    error.detail = detail
    error.code = code
    error.details = details
    throw error
  }
  if (response.status === 204) return undefined as T
  const contentType = response.headers.get('content-type') ?? ''
  if (!contentType.includes('application/json')) {
    // 未实现的 /api 路径会被 SPA 兜底路由用 index.html(200) 吞掉（KI-7）；
    // 视为接口缺失，让调用方走"未就绪"降级分支。
    const error = new Error('接口不存在或未上线') as ApiError
    error.status = 404
    error.code = 'endpoint_missing'
    throw error
  }
  return response.json()
}

export type ApiError = Error & {
  status?: number
  detail?: unknown
  code?: string
  details?: Record<string, unknown>
}

export const get = <T>(path: string) => api<T>(path)
export const post = <T>(path: string, body?: unknown) =>
  api<T>(path, { method: 'POST', body: body === undefined ? undefined : JSON.stringify(body) })
export const put = <T>(path: string, body?: unknown) =>
  api<T>(path, { method: 'PUT', body: body === undefined ? undefined : JSON.stringify(body) })
export const patch = <T>(path: string, body?: unknown) =>
  api<T>(path, { method: 'PATCH', body: body === undefined ? undefined : JSON.stringify(body) })
export const del = <T>(path: string) => api<T>(path, { method: 'DELETE' })
