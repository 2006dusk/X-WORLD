// 世界书 mock 数据。worldbooksMocks 为固定路径字典，动态 handler 负责条目 id 与匹配路径。
import type {
  WorldBook,
  WorldBookListResponse,
  WorldEntry,
  WorldMatchResponse,
} from '../api/worldbooks'

export const MOCK_WORLD_ENTRIES: WorldEntry[] = [
  {
    id: 'e_gate',
    keys: ['王都', '北门'],
    content: '王都北门在日落时关闭，雨后石板路会映出灯塔的光。',
    priority: 80,
    position: 0,
    constant: false,
    selective: false,
    recursion: 0,
    secondary_keys: [],
    case_sensitive: false,
    match_regex: false,
    comment: '城门',
  },
  {
    id: 'e_night',
    keys: ['王都'],
    content: '深夜的王都实行宵禁，只有持铜牌的信使可以穿行。',
    priority: 100,
    position: 0,
    constant: false,
    selective: true,
    recursion: 2,
    secondary_keys: ['深夜', '宵禁'],
    case_sensitive: false,
    match_regex: false,
    comment: '夜间规则',
  },
  {
    id: 'e_constant',
    keys: [],
    content: '故事发生在一座终年多雨的沿海王国。',
    priority: 50,
    position: 1,
    constant: true,
    selective: false,
    recursion: 0,
    secondary_keys: [],
    case_sensitive: false,
    match_regex: false,
    comment: '世界基调',
  },
  {
    id: 'e_regex',
    keys: ['Rain\\w*'],
    content: '英文语境中，雨名也是旧港灯塔守护人的姓氏。',
    priority: 30,
    position: 0,
    constant: false,
    selective: false,
    recursion: 0,
    secondary_keys: [],
    case_sensitive: true,
    match_regex: true,
    comment: '大小写与正则示例',
  },
]

export const MOCK_WORLD_BOOK: WorldBook = {
  id: 'w_old_town',
  name: '地址N设定',
  enabled: true,
  updated_at: '2026-01-10T14:30:00+00:00',
  entries: MOCK_WORLD_ENTRIES,
}

export const MOCK_WORLD_MATCH: WorldMatchResponse = {
  items: ['e_night', 'e_constant', 'e_gate'],
  entries: [MOCK_WORLD_ENTRIES[1], MOCK_WORLD_ENTRIES[2], MOCK_WORLD_ENTRIES[0]],
}

export const worldbooksMocks: Record<string, unknown> = {
  '/v1/world-books/': {
    items: [{
      id: MOCK_WORLD_BOOK.id,
      name: MOCK_WORLD_BOOK.name,
      enabled: MOCK_WORLD_BOOK.enabled,
      updated_at: MOCK_WORLD_BOOK.updated_at,
    }],
  } satisfies WorldBookListResponse,
  '/v1/world-books/w_old_town': MOCK_WORLD_BOOK,
  '/v1/world-books/w_old_town/match': MOCK_WORLD_MATCH,
}

export const worldbooksMockHandlers: Record<
  string,
  (path: string, method: string, body: unknown) => unknown
> = {
  'GET /v1/world-books/': () => worldbooksMocks['/v1/world-books/'],
  'GET /v1/world-books/:id': () => MOCK_WORLD_BOOK,
  'PUT /v1/world-books/:id': (_path, _method, body) => ({
    ...MOCK_WORLD_BOOK,
    ...(body && typeof body === 'object' ? body : {}),
  }),
  'POST /v1/world-books/:id/match': () => MOCK_WORLD_MATCH,
  'POST /v1/world-books/:id/entries': (_path, _method, body) => ({
    ...(body && typeof body === 'object' ? body : {}),
    id: 'e_mock_new',
  }),
}
