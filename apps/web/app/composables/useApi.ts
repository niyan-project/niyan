import type { ApiErrorBody } from '~/types/api'

export class NiyanApiError extends Error {
  status: number
  code: string

  constructor(status: number, body?: Partial<ApiErrorBody>) {
    super(body?.detail || 'The request could not be completed.')
    this.name = 'NiyanApiError'
    this.status = status
    this.code = body?.code || 'request_failed'
  }
}

type ApiOptions = Parameters<typeof $fetch>[1]
type ApiMethod = 'GET' | 'POST' | 'PATCH' | 'DELETE'

export function useApi() {
  const csrfToken = useState<string | null>('csrf-token', () => null)

  async function ensureCsrf() {
    if (csrfToken.value) return csrfToken.value
    const response = await $fetch<{ csrf_token: string }>('/api/v1/auth/csrf', { credentials: 'include' })
    csrfToken.value = response.csrf_token
    return response.csrf_token
  }

  function resetCsrf() {
    csrfToken.value = null
  }

  async function request<T>(url: string, options: ApiOptions = {}) {
    const method = String(options.method || 'GET').toUpperCase() as ApiMethod
    const headers = new Headers(options.headers as HeadersInit | undefined)
    if (!['GET', 'HEAD', 'OPTIONS'].includes(method)) headers.set('X-CSRFToken', await ensureCsrf())
    try {
      return await $fetch<T>(url, { ...options, method, headers, credentials: 'include' })
    } catch (error: unknown) {
      const candidate = error as { status?: number, statusCode?: number, data?: ApiErrorBody }
      throw new NiyanApiError(candidate.statusCode || candidate.status || 500, candidate.data)
    }
  }

  return {
    ensureCsrf,
    resetCsrf,
    get: <T>(url: string, options: ApiOptions = {}) => request<T>(url, { ...options, method: 'GET' }),
    post: <T>(url: string, body?: unknown, options: ApiOptions = {}) => request<T>(url, { ...options, method: 'POST', body: body as never }),
    patch: <T>(url: string, body?: unknown, options: ApiOptions = {}) => request<T>(url, { ...options, method: 'PATCH', body: body as never }),
    delete: <T>(url: string, options: ApiOptions = {}) => request<T>(url, { ...options, method: 'DELETE' })
  }
}
