// 调试接口：装配预览、分词器探测、模型与 profile 清单。
// 装配预览只取后端返回的 context_report 与 messages，前端不参与 prompt 拼装。
import { getJson } from './http'
import type {
  ModelInfo,
  PreviewMessage,
  Profile,
  TokenizerListResponse,
} from '../types/chat'

/** 装配预览响应，契约 4.2 */
export interface PreviewResult {
  segments: Array<{
    name: string
    tokens: number
    entries?: string[] | null
    hits?: number | null
  }>
  template: string
  tokenizer: string
  truncated: boolean
  messages: PreviewMessage[]
}

export interface PreviewQuery {
  session?: string
  character_id?: string
  preset_id?: string
  world_book_ids?: string
  user_input?: string
}

function buildQuery(query: PreviewQuery): string {
  const parts: string[] = []
  for (const [key, value] of Object.entries(query)) {
    if (value === undefined || value === '') continue
    parts.push(`${key}=${encodeURIComponent(value)}`)
  }
  return parts.length > 0 ? `?${parts.join('&')}` : ''
}

export function fetchPreview(query: PreviewQuery): Promise<PreviewResult> {
  return getJson<PreviewResult>(`/v1/prompt-preview${buildQuery(query)}`)
}

export function fetchModels(): Promise<ModelInfo[]> {
  return getJson<ModelInfo[]>('/v1/models')
}

export function fetchProfiles(): Promise<Profile[]> {
  return getJson<Profile[]>('/v1/profiles')
}

export function fetchTokenizers(): Promise<TokenizerListResponse> {
  return getJson<TokenizerListResponse>('/v1/tokenizers')
}
