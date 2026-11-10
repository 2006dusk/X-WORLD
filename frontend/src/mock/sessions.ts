// 会话 mock 数据。
import type { SessionMessage, SessionSummary } from '../api/sessions'

export const MOCK_SESSIONS: SessionSummary[] = [
  {
    id: 's_demo_001',
    character_id: 'c_demo_a',
    preset_id: 'p_default',
    created_at: '2026-01-10T14:30:00+00:00',
    message_count: 2,
    branch_count: 1,
    scope: 'c_demo_a',
    characters: ['c_demo_a'],
    world_book_ids: ['w_demo'],
    region_id: '',
    title: '',
  },
]

export const MOCK_SESSION_MESSAGES: SessionMessage[] = [
  { role: 'user', content: '这盘磁带还能修吗？', created_at: '2026-01-10T14:30:10+00:00' },
  {
    role: 'assistant',
    content: '先让我听听它还记得什么。',
    created_at: '2026-01-10T14:30:20+00:00',
  },
]

export const sessionsMockHandlers: Record<
  string,
  (path: string, method: string, body: unknown) => unknown
> = {
  'GET /v1/sessions/': () => ({ items: MOCK_SESSIONS }),
  'POST /v1/sessions/': () => MOCK_SESSIONS[0],
  'GET /v1/sessions/:id/messages': () => ({
    items: MOCK_SESSION_MESSAGES,
    count: MOCK_SESSION_MESSAGES.length,
  }),
  'POST /v1/sessions/:id/messages': (_path, _method, body) => ({
    role: 'user',
    content:
      body && typeof body === 'object' && 'content' in body
        ? String((body as { content: unknown }).content)
        : '',
    created_at: '2026-01-10T14:31:00+00:00',
  }),
  'GET /v1/sessions/:id': (path) => {
    const id = decodeURIComponent(path.split('/').pop() ?? '')
    return MOCK_SESSIONS.find((item) => item.id === id) ?? {
      error: { code: 'session_not_found', message: '会话不存在', retryable: false },
    }
  },
  'DELETE /v1/sessions/:id': (path) => ({
    ok: true,
    id: decodeURIComponent(path.split('/').pop() ?? ''),
  }),
  'PUT /v1/sessions/:id': (_path, _method, body) => {
    const patch =
      body && typeof body === 'object' ? (body as Partial<SessionSummary>) : {}
    return { ...MOCK_SESSIONS[0], ...patch }
  },
}
