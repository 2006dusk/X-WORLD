// 记忆接口：条目列表、混合检索、统计、实体、抽取与遗忘扫描。
// 检索排序与命中分数由后端给出，前端不做二次排序。
import { deleteJson, getJson, postJson, putJson } from './http'

/** 记忆条目 */
export interface MemoryItem {
  id: string
  character_id: string
  session_id?: string | null
  kind: string
  content: string
  importance: number
  created_at?: string
  last_access_at?: string
  archived?: boolean
  meta?: Record<string, unknown>
}

/** 检索命中 */
export interface MemoryHit {
  id: string
  character_id?: string | null
  session_id?: string | null
  kind: string
  importance: number
  score: number
  bm25: number
  vector: number
  approximate: boolean
}

/** 统计 */
export interface MemoryStats {
  total: number
  archived: number
  active: number
  by_kind: Record<string, number>
  average_importance: number
}

/** 实体状态 */
export interface EntityItem {
  name: string
  character_id: string
  attributes: Record<string, unknown>
}

export interface MemoryQuery {
  character_id?: string
  session_id?: string
  kind?: string
  archived?: boolean
}

function query(params: MemoryQuery): string {
  const parts: string[] = []
  if (params.character_id) parts.push(`character_id=${encodeURIComponent(params.character_id)}`)
  if (params.session_id) parts.push(`session_id=${encodeURIComponent(params.session_id)}`)
  if (params.kind) parts.push(`kind=${encodeURIComponent(params.kind)}`)
  if (params.archived) parts.push('archived=true')
  return parts.length > 0 ? `?${parts.join('&')}` : ''
}

export function fetchMemories(params: MemoryQuery): Promise<{ items: MemoryItem[] }> {
  return getJson<{ items: MemoryItem[] }>(`/v1/memory/${query(params)}`)
}

export function fetchMemoryStats(characterId?: string): Promise<MemoryStats> {
  const suffix = characterId ? `?character_id=${encodeURIComponent(characterId)}` : ''
  return getJson<MemoryStats>(`/v1/memory/stats${suffix}`)
}

export function searchMemories(payload: {
  query: string
  character_id?: string
  session_id?: string
  kind?: string
  top_k?: number
  bm25_weight?: number
  vector_weight?: number
  include_archived?: boolean
}): Promise<{ items: MemoryHit[]; approximate: boolean }> {
  return postJson<{ items: MemoryHit[]; approximate: boolean }>('/v1/memory/search', payload)
}

export function fetchEntities(characterId?: string): Promise<{ items: EntityItem[] }> {
  const suffix = characterId ? `?character_id=${encodeURIComponent(characterId)}` : ''
  return getJson<{ items: EntityItem[] }>(`/v1/memory/entities${suffix}`)
}

export function saveEntity(
  name: string,
  payload: { character_id?: string; attributes: Record<string, unknown> },
): Promise<EntityItem> {
  return putJson<EntityItem>(`/v1/memory/entities/${encodeURIComponent(name)}`, payload)
}

export function extractMemories(payload: {
  session_id: string
  character_id?: string
}): Promise<{ items: MemoryItem[]; count: number }> {
  return postJson<{ items: MemoryItem[]; count: number }>('/v1/memory/extract', payload)
}

export function forgetScan(params: {
  character_id?: string
  threshold?: number
  days?: number
}): Promise<{ items: MemoryItem[]; count: number }> {
  const parts: string[] = []
  if (params.character_id) parts.push(`character_id=${encodeURIComponent(params.character_id)}`)
  if (params.threshold !== undefined) parts.push(`threshold=${params.threshold}`)
  if (params.days !== undefined) parts.push(`days=${params.days}`)
  const suffix = parts.length > 0 ? `?${parts.join('&')}` : ''
  return getJson<{ items: MemoryItem[]; count: number }>(`/v1/memory/forget-scan${suffix}`)
}

export function archiveMemory(
  id: string,
  characterId?: string,
): Promise<{ ok: boolean; id: string }> {
  const suffix = characterId ? `?character_id=${encodeURIComponent(characterId)}` : ''
  return deleteJson<{ ok: boolean; id: string }>(
    `/v1/memory/${encodeURIComponent(id)}${suffix}`,
  )
}
