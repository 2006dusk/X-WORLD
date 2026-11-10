// 角色卡 mock 数据。cardsMocks 为固定路径字典；cardsMockHandlers 供动态 id 路由使用。
import type { Card, CardListResponse } from '../api/cards'

export const MOCK_CARD: Card = {
  id: 'c_demo_a',
  name: '角色K',
  spec_version: 'v1',
  created_at: '2026-01-08T09:00:00+00:00',
  updated_at: '2026-01-10T14:30:00+00:00',
  data: {
    name: '角色K',
    identity: '角色K是地址N「地址O」的调音师，与用户是旧识；今晚她守在棚里等一盘没人来取的磁带。',
    behavior: '个子高，穿洗旧的毛衣，指节有摆弄磁带留下的划痕。说话前习惯先把手里的活停下。提到声音时会抬眼，会主动去翻器材架。',
    speech: '短句为主，语气平，几乎不用感叹号。不解释自己的心思，不总结道理，不给用户下指令。',
    mes_example: '<START>\n{{user}}：这盘磁带还能修吗？\n{{char}}：先让我听听它还记得什么。',
    creator_notes: '用于本地界面演示。',
    tags: ['旧城', '声音'],
  },
  raw: {},
}

export const cardsMocks: Record<string, unknown> = {
  '/v1/cards/': { items: [MOCK_CARD] } satisfies CardListResponse,
  '/v1/cards/c_demo_a': MOCK_CARD,
}

/** 动态路径处理器不依赖主控的 mockFetch 实现，可由适配层按需选用。 */
export const cardsMockHandlers: Record<
  string,
  (path: string, method: string, body: unknown) => unknown
> = {
  'GET /v1/cards/': () => cardsMocks['/v1/cards/'],
  'GET /v1/cards/:id': (path) => {
    const id = decodeURIComponent(path.slice('/v1/cards/'.length))
    return id === MOCK_CARD.id ? MOCK_CARD : {
      error: { code: 'card_not_found', message: '角色卡不存在', retryable: false },
    }
  },
  'PUT /v1/cards/:id': (path, _method, body) => ({
    ...MOCK_CARD,
    ...(body && typeof body === 'object' ? body : {}),
    id: decodeURIComponent(path.slice('/v1/cards/'.length)),
  }),
  'POST /v1/cards/import': (_path, _method, body) => ({
    ...MOCK_CARD,
    id: 'c_imported',
    ...(body && typeof body === 'object' ? body : {}),
  }),
}
