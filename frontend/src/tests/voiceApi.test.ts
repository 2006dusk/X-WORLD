// 角色声线接口测试：清单、设置与清除都要能过 mock 层。
import { afterEach, beforeEach, describe, it, expect, vi } from 'vitest'
import { mockFetch } from '../mock'
import { deleteVoice, fetchVoices, putVoice } from '../api/voices'

beforeEach(() => {
  vi.stubGlobal(
    'fetch',
    (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url
      return mockFetch(url, init)
    },
  )
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('角色声线接口 mock 覆盖', () => {
  it('清单、设置与清除', async () => {
    const list = await fetchVoices()
    expect(list.items.length).toBeGreaterThan(0)
    expect(list.vectors.length).toBeGreaterThan(0)
    expect(list.max_scale).toBeGreaterThan(0)
    const first = list.items[0]
    const saved = await putVoice(first.character_id, list.vectors[0].name, 0.3)
    expect(saved.ok).toBe(true)
    const removed = await deleteVoice(first.character_id)
    expect(removed.ok).toBe(true)
  })
})