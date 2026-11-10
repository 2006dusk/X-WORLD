// 会话、世界区域与角色解耦的前端逻辑测试。
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { useChatStore } from '../stores/chat'
import { createSession, updateSession } from '../api/sessions'

const SUMMARY = {
  id: 's_conv',
  character_id: 'c_demo_a',
  preset_id: 'p_builtin_rp',
  created_at: '2026-11-06T00:00:00+00:00',
  message_count: 4,
  scope: 'c_demo_a',
  characters: ['c_demo_a', 'c_demo_b'],
  world_book_ids: ['w_demo'],
  region_id: 'e_region_001',
  title: '',
}

const EMPTY_STATE = {
  location: '',
  position: '',
  items: [],
  scene_items: [],
  map: '',
  time: '',
  updated_at: null,
}

function stubFetch(): ReturnType<typeof vi.fn> {
  const fn = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input)
    if (url.includes('/messages')) {
      return new Response(
        JSON.stringify({ head_id: 'm1', items: [], tree: [], labels: {}, count: 0 }),
        { status: 200, headers: { 'Content-Type': 'application/json' } },
      )
    }
    if (url.includes('/v1/sessions/s_conv')) {
      return new Response(JSON.stringify(SUMMARY), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      })
    }
    if (url.includes('/v1/state')) {
      return new Response(JSON.stringify(EMPTY_STATE), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      })
    }
    return new Response(JSON.stringify(SUMMARY), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    })
  })
  vi.stubGlobal('fetch', fn)
  return fn
}

describe('会话、世界区域与角色', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('打开会话时按会话头恢复世界、区域与主角色', async () => {
    stubFetch()
    const chat = useChatStore()
    await chat.openSession('s_conv')
    expect(chat.session).toBe('s_conv')
    expect(chat.xw.world_book_ids).toEqual(['w_demo'])
    expect(chat.xw.region_id).toBe('e_region_001')
    expect(chat.xw.character_id).toBe('c_demo_a')
    expect(chat.sessionMeta?.characters).toEqual(['c_demo_a', 'c_demo_b'])
  })

  it('@ 换角色只换发言者，世界与区域不动', async () => {
    stubFetch()
    const chat = useChatStore()
    await chat.openSession('s_conv')
    await chat.setSpeaker('c_demo_b')
    expect(chat.xw.character_id).toBe('c_demo_b')
    expect(chat.session).toBe('s_conv')
    expect(chat.xw.world_book_ids).toEqual(['w_demo'])
    expect(chat.xw.region_id).toBe('e_region_001')
  })

  it('新建会话带上世界与区域', async () => {
    const fn = stubFetch()
    await createSession('c_demo_a', 'p_builtin_rp', ['w_demo'], 'e_region_001')
    const [, init] = fn.mock.calls[0]
    const body = JSON.parse(String((init as RequestInit).body))
    expect(body.world_book_ids).toEqual(['w_demo'])
    expect(body.region_id).toBe('e_region_001')
    expect(body.character_id).toBe('c_demo_a')
  })

  it('会话元信息更新走 PUT', async () => {
    const fn = stubFetch()
    await updateSession('s_conv', { region_id: 'e_region_002', title: '夜谈' })
    const [input, init] = fn.mock.calls[0]
    expect(String(input)).toContain('/v1/sessions/s_conv')
    expect((init as RequestInit).method).toBe('PUT')
    const body = JSON.parse(String((init as RequestInit).body))
    expect(body.region_id).toBe('e_region_002')
    expect(body.title).toBe('夜谈')
  })
})
