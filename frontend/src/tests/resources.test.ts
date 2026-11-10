// 资源接口与 mock 覆盖测试：确认各视图依赖的端点在 mock 层均可响应。
import { afterEach, beforeEach, describe, it, expect, vi } from 'vitest'
import { mockFetch } from '../mock'
import { fetchCards, fetchCard, importCardJson } from '../api/cards'
import { fetchWorldBooks, fetchWorldBook, matchWorldBook } from '../api/worldbooks'
import { fetchPresets, fetchPreset, fetchPromptGroups } from '../api/presets'
import { fetchMemories, fetchMemoryStats, searchMemories, fetchEntities } from '../api/memory'
import { fetchPlugins, fetchHooks } from '../api/plugins'
import { fetchSettings } from '../api/settings'
import { fetchSessions, fetchMessages } from '../api/sessions'

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

describe('资源接口 mock 覆盖', () => {
  it('角色卡列表与详情', async () => {
    const list = await fetchCards()
    expect(list.items.length).toBeGreaterThan(0)
    const card = await fetchCard(list.items[0].id)
    expect(card.id).toBe(list.items[0].id)
  })

  it('角色卡导入返回归一化结果', async () => {
    const card = await importCardJson(JSON.stringify({ name: '测试', data: {} }))
    expect(card.name).toBeTruthy()
  })

  it('世界书列表、详情与匹配', async () => {
    const list = await fetchWorldBooks()
    expect(list.items.length).toBeGreaterThan(0)
    const book = await fetchWorldBook(list.items[0].id)
    expect(book.entries.length).toBeGreaterThan(0)
    const matched = await matchWorldBook(book.id, { text: '王都', secondary_text: '' })
    expect(Array.isArray(matched.items)).toBe(true)
  })

  it('预设列表、详情与提示组', async () => {
    const list = await fetchPresets()
    expect(list.items.length).toBeGreaterThan(0)
    const preset = await fetchPreset(list.items[0].id)
    expect(preset.prompts.length).toBeGreaterThan(0)
    const groups = await fetchPromptGroups(list.items[0].id)
    expect(groups.all.length).toBe(groups.system.length + groups.others.length)
  })

  it('记忆条目、统计、检索与实体', async () => {
    const list = await fetchMemories({})
    expect(list.items.length).toBeGreaterThan(0)
    const stats = await fetchMemoryStats()
    expect(stats.total).toBeGreaterThan(0)
    const hits = await searchMemories({ query: '地址U' })
    expect(Array.isArray(hits.items)).toBe(true)
    const entities = await fetchEntities()
    expect(Array.isArray(entities.items)).toBe(true)
  })

  it('插件清单与 hook 契约', async () => {
    const plugins = await fetchPlugins()
    expect(plugins.items.length).toBeGreaterThan(0)
    const hooks = await fetchHooks()
    expect(hooks.items.length).toBe(6)
  })

  it('设置列表含全部声明键', async () => {
    const settings = await fetchSettings()
    expect(settings.keys.length).toBeGreaterThan(0)
    expect(settings.items.theme).toBe('dark')
  })

  it('会话列表与消息', async () => {
    const list = await fetchSessions()
    expect(list.items.length).toBeGreaterThan(0)
    const messages = await fetchMessages(list.items[0].id)
    expect(messages.count).toBe(messages.items.length)
  })

  it('会话逐字稿导出为 markdown 文本', async () => {
    const res = await mockFetch('/v1/sessions/s_demo_001/export')
    expect(res.status).toBe(200)
    expect(res.headers.get('content-type')).toContain('text/markdown')
    expect(await res.text()).toContain('# 会话逐字稿')
  })
})
