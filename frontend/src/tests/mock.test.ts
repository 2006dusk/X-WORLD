// Mock 适配层测试：无后端时独立运行，数据形状遵循契约 4 节。
import { describe, it, expect } from 'vitest'
import { mockFetch, buildMockEvents } from '../mock'
import type { ChatRequest } from '../api/chat'

const REQ: ChatRequest = {
  model: 'xw-local-default',
  messages: [{ role: 'user', content: '继续' }],
  session: 's_mock',
  stream: false,
  xw: {
    character_id: 'c_1',
    preset_id: 'p_1',
    world_book_ids: [],
    region_id: '',
    memory_mode: 'hybrid',
    max_context_tokens: 8192,
    auto_turn: '',
  },
}

describe('mock 适配层', () => {
  it('非流式响应符合契约 4.1 形状', async () => {
    const res = await mockFetch('/v1/chat/completions', {
      method: 'POST',
      body: JSON.stringify(REQ),
    })
    const body = await res.json()
    expect(res.status).toBe(200)
    expect(body.choices[0].message.role).toBe('assistant')
    expect(body.usage.budget.limit).toBe(8192)
    expect(body.xw.context_report.template).toBe('chatml')
    expect(Array.isArray(body.xw.context_report.segments)).toBe(true)
  })

  it('流式响应为 SSE 且含六种事件名', async () => {
    const res = await mockFetch('/v1/chat/completions', {
      method: 'POST',
      body: JSON.stringify({ ...REQ, stream: true }),
    })
    const text = await res.text()
    expect(res.headers.get('content-type')).toBe('text/event-stream')
    for (const ev of ['context', 'delta', 'usage', 'done']) {
      expect(text).toContain(`event: ${ev}`)
    }
  })

  it('模型清单 / 装配预览端点可用', async () => {
    const models = await (await mockFetch('/v1/models')).json()
    expect(Array.isArray(models)).toBe(true)
    expect(models[0]).toHaveProperty('context_length')
    const preview = await (await mockFetch('/v1/prompt-preview?session=x')).json()
    expect(preview.template).toBe('chatml')
    expect(Array.isArray(preview.messages)).toBe(true)
  })

  it('buildMockEvents 顺序固定且字段完整', () => {
    const events = buildMockEvents({ ...REQ, model: 'xw-local-thinking' })
    const types = events.map((e: { type: string }) => e.type)
    expect(types).toEqual(['context', 'reasoning', 'delta', 'usage', 'state', 'done'])
  })
})
