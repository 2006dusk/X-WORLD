// 会话接口：会话列表、创建、消息读写、分支切换与逐字稿导出。
import { deleteJson, getJson, postJson } from './http'
import type { SessionSnapshot } from '../types/chat'

/** 会话摘要 */
export interface SessionSummary {
  id: string
  /** 主角色：新建对话时定下的角色 */
  character_id: string
  preset_id: string | null
  created_at: string | null
  message_count: number
  /** 分叉数：这段对话分出去几支。列表页显示它，不显示会话 id。 */
  branch_count: number
  /** 会话文件所在的作用域目录 */
  scope: string
  /** 会话涉及的角色，主角色排第一个 */
  characters: string[]
  /** 对话所在的世界 */
  world_book_ids: string[]
  /** 对话所在的世界区域：世界书里的一条区域条目 */
  region_id: string
  /** 会话标题，暂由界面填写 */
  title: string
}

/** 会话元信息更新载荷 */
export interface SessionMetaInput {
  character_id?: string
  preset_id?: string
  title?: string
  world_book_ids?: string[]
  region_id?: string
  characters?: string[]
}

/** 会话消息 */
export interface SessionMessage {
  role: string
  content: string
  created_at?: string
  /** 结构化回合的块内容 */
  blocks?: Record<string, string> | null
}

export interface MessageListResponse {
  items: SessionMessage[]
  count: number
}

export function fetchSessions(characterId?: string): Promise<{ items: SessionSummary[] }> {
  const query = characterId ? `?character_id=${encodeURIComponent(characterId)}` : ''
  return getJson<{ items: SessionSummary[] }>(`/v1/sessions/${query}`)
}

/**
 * 新建会话。
 *
 * worldBookIds 是对话所在的世界；regionId 是这段对话所在的区域；characterId
 * 是主角色。三者都写进会话头，会话列表按世界与区域分组。
 */
export function createSession(
  characterId: string,
  presetId?: string,
  worldBookIds?: string[],
  regionId?: string,
): Promise<SessionSummary> {
  return postJson<SessionSummary>('/v1/sessions/', {
    character_id: characterId,
    preset_id: presetId ?? null,
    world_book_ids: worldBookIds ?? [],
    region_id: regionId ?? '',
  })
}

/** 更新会话元信息：世界位置、在场角色、主角色与标题。 */
export function updateSession(
  id: string,
  meta: SessionMetaInput,
): Promise<SessionSummary> {
  return putSessionJson<SessionSummary>(
    `/v1/sessions/${encodeURIComponent(id)}`,
    meta,
  )
}

// PUT 与 http.ts 的 JSON 请求封装同款，局部实现以遵守文件边界。
async function putSessionJson<T>(path: string, body: unknown): Promise<T> {
  let response: Response
  try {
    response = await fetch(path, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
  } catch {
    throw new Error('无法连接本地后端，请确认 X-WORLD 后端已启动')
  }
  if (!response.ok) {
    let payload: unknown = null
    try {
      payload = await response.json()
    } catch {
      payload = null
    }
    const message =
      payload && typeof payload === 'object'
        ? (payload as { error?: { message?: unknown } }).error?.message
        : undefined
    throw new Error(typeof message === 'string' ? message : `请求失败（HTTP ${response.status}）`)
  }
  return (await response.json()) as T
}

export function fetchSession(id: string): Promise<SessionSummary> {
  return getJson<SessionSummary>(`/v1/sessions/${encodeURIComponent(id)}`)
}

export function deleteSession(id: string): Promise<{ ok: boolean; id: string }> {
  return deleteJson<{ ok: boolean; id: string }>(`/v1/sessions/${encodeURIComponent(id)}`)
}

export function fetchMessages(id: string): Promise<MessageListResponse> {
  return getJson<MessageListResponse>(`/v1/sessions/${encodeURIComponent(id)}/messages`)
}

/** 当前分支路径与整棵分支树，见契约 4.5。 */
export function fetchSessionSnapshot(
  id: string,
  characterId?: string,
): Promise<SessionSnapshot> {
  const query = characterId ? `?path=1&tree=1&character_id=${encodeURIComponent(characterId)}` : '?path=1&tree=1'
  return getJson<SessionSnapshot>(
    `/v1/sessions/${encodeURIComponent(id)}/messages${query}`,
  )
}

/** 切换到指定分支叶子，返回切换后的路径与树。 */
export function switchBranch(
  id: string,
  leafId: string,
  characterId?: string,
): Promise<SessionSnapshot> {
  return postJson<SessionSnapshot>(`/v1/sessions/${encodeURIComponent(id)}/switch`, {
    leaf_id: leafId,
    character_id: characterId ?? null,
  })
}

/**
 * 删除一条分支的一段。
 *
 * nodeIds 是要删的节点 id 集合：前端按折叠后的分组算出这一段含哪几个节点。
 * 删的是支线的一段，不是整段对话：别的支线照旧留着，被删节点下面的幸存
 * 子节点重挂到最近的幸存祖先上。返回删完后的当前路径与分支树。
 */
export function deleteBranch(
  id: string,
  nodeIds: string[],
  characterId?: string,
): Promise<SessionSnapshot> {
  const nodes = encodeURIComponent(nodeIds.join(','))
  const suffix = characterId ? `&character_id=${encodeURIComponent(characterId)}` : ''
  return deleteJson<SessionSnapshot>(
    `/v1/sessions/${encodeURIComponent(id)}/branches?nodes=${nodes}${suffix}`,
  )
}

export function appendMessage(
  id: string,
  message: { role: string; content: string },
): Promise<SessionMessage> {
  return postJson<SessionMessage>(
    `/v1/sessions/${encodeURIComponent(id)}/messages`,
    message,
  )
}

/** 下载后端导出的逐字稿，浏览器只负责保存。 */
export async function downloadSessionMarkdown(id: string): Promise<void> {
  let response: Response
  try {
    response = await fetch(`/v1/sessions/${encodeURIComponent(id)}/export?format=md`)
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
    const message =
      body && typeof body === 'object'
        ? (body as { error?: { message?: unknown } }).error?.message
        : undefined
    throw new Error(typeof message === 'string' ? message : `导出失败（HTTP ${response.status}）`)
  }
  const blob = await response.blob()
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = `${id}.md`
  anchor.style.display = 'none'
  document.body.appendChild(anchor)
  anchor.click()
  anchor.remove()
  window.setTimeout(() => URL.revokeObjectURL(url), 0)
}
