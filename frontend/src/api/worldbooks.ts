// 世界书资源与匹配测试接口。匹配、排序与递归均由后端负责。
import { ApiError, getJson, postJson } from './http'

export interface WorldBookSummary {
  id: string
  name: string
  enabled: boolean
  updated_at?: string
}

export type WorldEntry = Record<string, unknown> & {
  id: string
  keys: string[]
  content: string
  priority: number
  position: number
  constant: boolean
  selective: boolean
  recursion: number
  secondary_keys?: string[]
  case_sensitive?: boolean
  match_regex?: boolean
  comment?: string
  _meta?: Record<string, unknown>
  /** 活引用：指向卡片库里的角色卡。卡片改了，正文由后端重算。 */
  card_ref?: { card_id: string; name: string } | null
  /** 条目类型：entry 普通条目，region 世界区域 */
  kind?: 'entry' | 'region'
  /** 区域名。区域条目必填。 */
  name?: string
}

/** 世界里的一个区域 */
export interface WorldRegion {
  id: string
  book_id: string
  name: string
  content: string
}

export interface WorldBook extends WorldBookSummary {
  entries: WorldEntry[]
}

export interface WorldBookListResponse {
  items: WorldBookSummary[]
}

export interface WorldBookInput {
  id?: string
  name: string
  enabled: boolean
  entries?: WorldEntry[]
}

export type WorldEntryInput = Omit<WorldEntry, 'id'> & { id?: string }

export interface WorldMatchRequest {
  text: string
  secondary_text: string
}

export interface WorldMatchResponse {
  items: string[]
  entries: WorldEntry[]
}

export interface WorldDeleteResponse {
  ok: boolean
  id: string
}

function toRequestError(status: number, body: unknown): ApiError {
  if (body && typeof body === 'object') {
    const error = (body as {
      error?: { code?: unknown; message?: unknown; retryable?: unknown }
    }).error
    if (error && typeof error === 'object') {
      return new ApiError(
        {
          code: typeof error.code === 'string' ? error.code : 'unknown_error',
          message: typeof error.message === 'string' ? error.message : '未知错误',
          retryable: typeof error.retryable === 'boolean' ? error.retryable : false,
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

// PUT / DELETE 与 http.ts 的 JSON 请求封装同款，局部实现以遵守文件边界。
async function requestWorld<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response
  try {
    response = await fetch(path, init)
  } catch {
    throw new ApiError(
      {
        code: 'network_error',
        message: '无法连接本地后端，请确认 X-WORLD 后端已启动',
        retryable: true,
      },
      0,
    )
  }
  if (!response.ok) {
    let body: unknown = null
    try {
      body = await response.json()
    } catch {
      body = null
    }
    throw toRequestError(response.status, body)
  }
  return (await response.json()) as T
}

export function putWorldJson<T>(path: string, body: unknown): Promise<T> {
  return requestWorld<T>(path, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
}

export function deleteWorldJson<T>(path: string): Promise<T> {
  return requestWorld<T>(path, { method: 'DELETE' })
}

const bookPath = (id: string): string => `/v1/world-books/${encodeURIComponent(id)}`

export function fetchWorldBooks(): Promise<WorldBookListResponse> {
  return getJson<WorldBookListResponse>('/v1/world-books/')
}

export function fetchWorldBook(id: string): Promise<WorldBook> {
  return getJson<WorldBook>(bookPath(id))
}

export function createWorldBook(book: WorldBookInput): Promise<WorldBook> {
  return postJson<WorldBook>('/v1/world-books/', book)
}

export function updateWorldBook(id: string, book: WorldBookInput): Promise<WorldBook> {
  return putWorldJson<WorldBook>(bookPath(id), book)
}

export function deleteWorldBook(id: string): Promise<WorldDeleteResponse> {
  return deleteWorldJson<WorldDeleteResponse>(bookPath(id))
}

export function createWorldEntry(
  bookId: string,
  entry: WorldEntryInput,
): Promise<WorldEntry> {
  return postJson<WorldEntry>(`${bookPath(bookId)}/entries`, entry)
}

export function updateWorldEntry(
  bookId: string,
  entryId: string,
  entry: WorldEntryInput,
): Promise<WorldEntry> {
  return putWorldJson<WorldEntry>(
    `${bookPath(bookId)}/entries/${encodeURIComponent(entryId)}`,
    entry,
  )
}

export function deleteWorldEntry(
  bookId: string,
  entryId: string,
): Promise<WorldDeleteResponse> {
  return deleteWorldJson<WorldDeleteResponse>(
    `${bookPath(bookId)}/entries/${encodeURIComponent(entryId)}`,
  )
}

/** 列出世界书里的区域条目。 */
export function fetchWorldRegions(bookId: string): Promise<{ items: WorldRegion[] }> {
  return getJson<{ items: WorldRegion[] }>(`${bookPath(bookId)}/regions`)
}

export function matchWorldBook(
  bookId: string,
  request: WorldMatchRequest,
): Promise<WorldMatchResponse> {
  return postJson<WorldMatchResponse>(`${bookPath(bookId)}/match`, request)
}
