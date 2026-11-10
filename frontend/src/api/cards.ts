// 角色卡资源接口。类型与后端 card.json 的归一化读写结构保持一致。
import { ApiError, getJson, postJson } from './http'

export type CardData = Record<string, unknown> & {
  name?: string
  /** 身份与处境：她是谁、与用户什么关系、当前处境。首句是身份锚点。 */
  identity?: string
  /** 行为与细节：外形、随身细节、行为倾向、她会做什么。 */
  behavior?: string
  /** 说话方式：语气、句长、措辞习惯、明确不做什么。 */
  speech?: string
  /** 角色定位：primary 主要人物，supporting 对话中引入的配角。 */
  role?: string
  /** 与主角的关系程度：相逢 / 相识 / 相熟，不是自由句。 */
  relation?: string
  /** 引入来源：user 是用户带出来的，ai 是模型自己带出来的。 */
  introduced_by?: string
  /** 待完善：AI 还没补全三层设定，或补全失败。 */
  pending?: boolean
  /** 补全失败的原因。 */
  fill_error?: string
  /** 抽到这个名字的那句正文。 */
  intro_evidence?: string
  /** 配角的状态：所在位置、心情、随身物品。 */
  state?: Record<string, unknown>
  scenario?: string
  first_mes?: string
  mes_example?: string
  creator?: string
  character_version?: string
  system_prompt?: string
  post_history_instructions?: string
  alternate_greetings?: string[]
  character_book?: unknown
  tags?: string[]
  creator_notes?: string
}

export interface CardSummary {
  id: string
  name: string
  spec_version: string
  /** 角色定位：primary 主要人物，supporting 对话中引入的配角 */
  role?: string
  /** 待完善：AI 还没补全设定 */
  pending?: boolean
  /** 引入来源：user 用户带出来的，ai 模型带出来的 */
  introduced_by?: string
  /** 与主角的关系程度 */
  relation?: string
  created_at?: string
  updated_at?: string
  /** 角色绑定的地区：他人在哪，切换人物时对话的位置跟着他走 */
  region_id?: string
}

export interface Card extends CardSummary {
  data: CardData
  raw: Record<string, unknown>
}

export interface CardListResponse {
  items: CardSummary[]
}

/** 角色卡的受控标签词表。标签进提示词时是类型约束，写什么就是约束什么。 */
export interface TagVocabularyResponse {
  items: string[]
}

export interface CardInput {
  id?: string
  name: string
  spec_version: string
  data: CardData
  raw?: Record<string, unknown>
}

export interface DeleteResponse {
  ok: boolean
  id: string
}

export type CardExportFormat = 'json' | 'md'

// http.ts 暂未暴露 PUT；在本模块沿用其错误收敛策略，不改主控文件。
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

async function requestCard<T>(path: string, init?: RequestInit): Promise<T> {
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

export function putCardJson<T>(path: string, body: unknown): Promise<T> {
  return requestCard<T>(path, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
}

export function deleteCardJson<T>(path: string): Promise<T> {
  return requestCard<T>(path, { method: 'DELETE' })
}

export function fetchCards(): Promise<CardListResponse> {
  return getJson<CardListResponse>('/v1/cards/')
}

export function fetchCard(id: string): Promise<Card> {
  return getJson<Card>(`/v1/cards/${encodeURIComponent(id)}`)
}

/** 取受控标签词表。编辑器拿它做候选标签，卡片里就不容易各处乱写。 */
export function fetchTagVocabulary(): Promise<TagVocabularyResponse> {
  return getJson<TagVocabularyResponse>('/v1/cards/tags')
}

/** 让模型补全这张配角卡的三层设定与状态。 */
export function autofillCard(id: string, context = ''): Promise<Card> {
  return postJson<Card>(`/v1/cards/${encodeURIComponent(id)}/autofill`, { context })
}

export function createCard(card: CardInput): Promise<Card> {
  return postJson<Card>('/v1/cards/', card)
}

export function updateCard(id: string, card: CardInput): Promise<Card> {
  return putCardJson<Card>(`/v1/cards/${encodeURIComponent(id)}`, card)
}

export function deleteCard(id: string): Promise<DeleteResponse> {
  return deleteCardJson<DeleteResponse>(`/v1/cards/${encodeURIComponent(id)}`)
}

export function importCardJson(jsonText: string): Promise<Card> {
  // 导入端点接收原始 JSON 正文，而不是再包一层 { json: ... }。
  return requestCard<Card>('/v1/cards/import', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json; charset=utf-8' },
    body: jsonText,
  })
}

function errorMessage(body: unknown, status: number): string {
  if (body && typeof body === 'object') {
    const error = (body as { error?: { message?: unknown } }).error
    if (error && typeof error.message === 'string') return error.message
  }
  return `导出失败（HTTP ${status}）`
}

/** 下载后端生成的 JSON 或 Markdown，浏览器只负责保存，不自行拼装卡片。 */
export async function downloadCardExport(
  card: CardSummary,
  format: CardExportFormat,
): Promise<void> {
  let response: Response
  try {
    response = await fetch(
      `/v1/cards/${encodeURIComponent(card.id)}/export?format=${format}`,
    )
  } catch {
    throw new Error('无法连接本地后端，导出未完成')
  }
  if (!response.ok) {
    let body: unknown = null
    try {
      body = await response.json()
    } catch {
      body = null
    }
    throw new Error(errorMessage(body, response.status))
  }
  const blob = await response.blob()
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  const safeName = card.name.replace(/[<>:"/\\|?*]/g, '_').trim() || card.id
  anchor.href = url
  anchor.download = `${safeName}.${format}`
  anchor.style.display = 'none'
  document.body.appendChild(anchor)
  anchor.click()
  anchor.remove()
  window.setTimeout(() => URL.revokeObjectURL(url), 0)
}
