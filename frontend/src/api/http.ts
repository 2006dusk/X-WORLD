// 统一请求层：只经 /v1 前缀访问后端，错误一律收敛为契约 4.4 的形状。
import type { XwError } from '../types/chat'

export class ApiError extends Error {
  readonly code: string
  readonly retryable: boolean
  readonly status: number

  constructor(err: XwError, status: number) {
    super(err.message)
    this.name = 'ApiError'
    this.code = err.code
    this.retryable = err.retryable
    this.status = status
  }
}

const FALLBACK_ERROR: XwError = {
  code: 'network_error',
  message: '无法连接本地后端，请确认 X-WORLD 后端已启动',
  retryable: true,
}

function toApiError(status: number, body: unknown): ApiError {
  if (body && typeof body === 'object') {
    const e = (body as { error?: Partial<XwError> }).error
    if (e && typeof e === 'object') {
      return new ApiError(
        {
          code: typeof e.code === 'string' ? e.code : 'unknown_error',
          message: typeof e.message === 'string' ? e.message : '未知错误',
          retryable: typeof e.retryable === 'boolean' ? e.retryable : false,
        },
        status,
      )
    }
  }
  return new ApiError(
    { code: 'http_error', message: `请求失败（HTTP ${status}）`, retryable: true },
    status,
  )
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response
  try {
    res = await fetch(path, init)
  } catch {
    throw new ApiError(FALLBACK_ERROR, 0)
  }
  if (!res.ok) {
    let body: unknown = null
    try {
      body = await res.json()
    } catch {
      body = null
    }
    throw toApiError(res.status, body)
  }
  return (await res.json()) as T
}

export function getJson<T>(path: string): Promise<T> {
  return request<T>(path)
}

export function postJson<T>(path: string, body: unknown): Promise<T> {
  return request<T>(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
}

export function putJson<T>(path: string, body: unknown): Promise<T> {
  return request<T>(path, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
}

export function deleteJson<T>(path: string): Promise<T> {
  return request<T>(path, { method: 'DELETE' })
}
