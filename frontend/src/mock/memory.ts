// 记忆 mock 数据。
import type { EntityItem, MemoryHit, MemoryItem, MemoryStats } from '../api/memory'

export const MOCK_MEMORY_ITEMS: MemoryItem[] = [
  {
    id: 'm_001',
    character_id: 'c_demo_a',
    session_id: 's_demo_001',
    kind: 'fact',
    content: '角色K在地址N经营地址U，收藏许多无人认领的磁带。',
    importance: 0.75,
    created_at: '2026-01-10T14:30:00+00:00',
    archived: false,
  },
  {
    id: 'm_002',
    character_id: 'c_demo_a',
    session_id: 's_demo_001',
    kind: 'relation',
    content: '角色K 对 用户 保持克制',
    importance: 0.55,
    created_at: '2026-01-10T14:31:00+00:00',
    archived: false,
  },
]

export const MOCK_MEMORY_HITS: MemoryHit[] = [
  {
    id: 'm_001',
    character_id: 'c_demo_a',
    session_id: 's_demo_001',
    kind: 'fact',
    importance: 0.75,
    score: 1.2841,
    bm25: 2.1402,
    vector: 0,
    approximate: true,
  },
]

export const MOCK_MEMORY_STATS: MemoryStats = {
  total: 2,
  archived: 0,
  active: 2,
  by_kind: { fact: 1, relation: 1 },
  average_importance: 0.65,
}

export const MOCK_ENTITIES: EntityItem[] = [
  { name: '角色K', character_id: 'c_demo_a', attributes: { 职业: '调音师', 地点: '地址N' } },
]

export const memoryMockHandlers: Record<
  string,
  (path: string, method: string, body: unknown) => unknown
> = {
  'GET /v1/memory/stats': () => MOCK_MEMORY_STATS,
  'POST /v1/memory/search': () => ({ items: MOCK_MEMORY_HITS, approximate: true }),
  'GET /v1/memory/entities': () => ({ items: MOCK_ENTITIES }),
  'PUT /v1/memory/entities/:name': (path, _method, body) => ({
    name: decodeURIComponent(path.split('/').pop() ?? ''),
    character_id: 'c_demo_a',
    attributes:
      body && typeof body === 'object' && 'attributes' in body
        ? (body as { attributes: Record<string, unknown> }).attributes
        : {},
  }),
  'POST /v1/memory/extract': () => ({ items: MOCK_MEMORY_ITEMS, count: 2 }),
  'GET /v1/memory/forget-scan': () => ({ items: [], count: 0 }),
  'GET /v1/memory/': () => ({ items: MOCK_MEMORY_ITEMS }),
  'GET /v1/memory/:id': (path) => {
    const id = decodeURIComponent(path.split('/').pop() ?? '')
    return MOCK_MEMORY_ITEMS.find((item) => item.id === id) ?? {
      error: { code: 'memory_not_found', message: '记忆不存在', retryable: false },
    }
  },
  'DELETE /v1/memory/:id': (path) => ({
    ok: true,
    id: decodeURIComponent(path.split('/').pop() ?? ''),
  }),
}
