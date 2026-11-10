// 关键逻辑测试：流式事件归组、预算条计算、错误形状。
// 运行：npm run test
import { describe, it, expect } from 'vitest'
import { chatStream } from '../api/chat'
import type { ChatRequest } from '../api/chat'
import { useChatStore } from '../stores/chat'
import { createPinia, setActivePinia } from 'pinia'
import { buildMockEvents, buildMockSseStream } from '../mock'

const REQ: ChatRequest = {
  model: 'xw-local-thinking',
  messages: [{ role: 'user', content: '继续' }],
  session: 's_test',
  stream: true,
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

describe('流式事件归组', () => {
  it('六事件按固定顺序累积到同一条 assistant 消息', () => {
    setActivePinia(createPinia())
    const chat = useChatStore()
    chat.messages.push({
      id: 'a1',
      role: 'assistant',
      content: '',
      reasoning: '',
      usage: null,
      contextReport: null,
      structure: null,
      interrupted: false,
      error: null,
      createdAt: 0,
    })
    const events = buildMockEvents(REQ)
    // 固定顺序：context → reasoning → delta → usage → state → done
    expect(events.map((e) => e.type)).toEqual([
      'context',
      'reasoning',
      'delta',
      'usage',
      'state',
      'done',
    ])
    for (const ev of events) chat.applyStreamEvent(ev)
    const tail = chat.messages[chat.messages.length - 1]
    expect(tail.content.length).toBeGreaterThan(0)
    expect(tail.reasoning.length).toBeGreaterThan(0)
    expect(tail.usage?.budget.used).toBe(4127)
    expect(chat.contextReport?.segments.length).toBeGreaterThan(0)
    expect(chat.worldState?.location).toBe('地址N·地址U二楼')
    expect(chat.worldState?.items.length).toBe(2)
    expect(chat.status).toBe('done')
  })

  it('delta 与 reasoning 增量追加而非覆盖', () => {
    setActivePinia(createPinia())
    const chat = useChatStore()
    chat.messages.push({
      id: 'a2',
      role: 'assistant',
      content: '',
      reasoning: '',
      usage: null,
      contextReport: null,
      structure: null,
      interrupted: false,
      error: null,
      createdAt: 0,
    })
    chat.applyStreamEvent({ type: 'delta', content: '你' })
    chat.applyStreamEvent({ type: 'delta', content: '好' })
    chat.applyStreamEvent({ type: 'reasoning', content: '思' })
    chat.applyStreamEvent({ type: 'reasoning', content: '考' })
    const tail = chat.messages[chat.messages.length - 1]
    expect(tail.content).toBe('你好')
    expect(tail.reasoning).toBe('思考')
  })

  it('replace 事件清空已显示正文，供重采样结果接管', () => {
    setActivePinia(createPinia())
    const chat = useChatStore()
    chat.messages.push({
      id: 'a4',
      role: 'assistant',
      content: '照搬了上一轮的内容',
      reasoning: '',
      usage: null,
      contextReport: null,
      structure: null,
      interrupted: false,
      error: null,
      createdAt: 0,
    })
    chat.applyStreamEvent({ type: 'replace', reason: 'repetition' })
    const tail = chat.messages[chat.messages.length - 1]
    expect(tail.content).toBe('')
  })

  it('error 事件标记可重试并进入 error 状态', () => {
    setActivePinia(createPinia())
    const chat = useChatStore()
    chat.messages.push({
      id: 'a3',
      role: 'assistant',
      content: '部分内容',
      reasoning: '',
      usage: null,
      contextReport: null,
      structure: null,
      interrupted: false,
      error: null,
      createdAt: 0,
    })
    chat.applyStreamEvent({
      type: 'error',
      code: 'context_overflow',
      message: '超出上下文',
      retryable: true,
    })
    const tail = chat.messages[chat.messages.length - 1]
    expect(chat.status).toBe('error')
    expect(tail.interrupted).toBe(true)
    expect(tail.error?.retryable).toBe(true)
    // 已生成内容保留，供续写
    expect(tail.content).toBe('部分内容')
  })
})

describe('SSE 解析', () => {
  it('收到 done 后连接关闭不算流意外结束', async () => {
    const events: unknown[] = []
    const errors: { message: string; retryable: boolean }[] = []
    const body = new ReadableStream<Uint8Array>({
      start(controller) {
        const encoder = new TextEncoder()
        controller.enqueue(
          encoder.encode('event: context\ndata: {"segments":[],"template":"chatml","tokenizer":"approx"}\n\n'),
        )
        controller.enqueue(encoder.encode('event: delta\ndata: {"content":"你好"}\n\n'))
        controller.enqueue(encoder.encode('event: done\ndata: {"finish_reason":"stop"}\n\n'))
        controller.close()
      },
    })
    const originalFetch = globalThis.fetch
    globalThis.fetch = (async () =>
      new Response(body, { status: 200 })) as typeof globalThis.fetch
    try {
      await chatStream(REQ, {
        onEvent: (ev) => events.push(ev),
        onTransportError: (err) => errors.push(err),
      })
      // 等 pump 读完流
      await new Promise((resolve) => setTimeout(resolve, 50))
    } finally {
      globalThis.fetch = originalFetch
    }
    expect(events.some((e) => (e as { type: string }).type === 'done')).toBe(true)
    // 服务端正常收尾：不该报传输层错误
    expect(errors).toHaveLength(0)
  })

  it('未收到终止事件就断开时报流意外结束', async () => {
    const errors: { message: string; retryable: boolean }[] = []
    const body = new ReadableStream<Uint8Array>({
      start(controller) {
        const encoder = new TextEncoder()
        controller.enqueue(encoder.encode('event: delta\ndata: {"content":"半句"}\n\n'))
        controller.close()
      },
    })
    const originalFetch = globalThis.fetch
    globalThis.fetch = (async () =>
      new Response(body, { status: 200 })) as typeof globalThis.fetch
    try {
      await chatStream(REQ, {
        onEvent: () => undefined,
        onTransportError: (err) => errors.push(err),
      })
      await new Promise((resolve) => setTimeout(resolve, 50))
    } finally {
      globalThis.fetch = originalFetch
    }
    expect(errors).toHaveLength(1)
    expect(errors[0].message).toBe('流意外结束')
  })

  it('buildMockSseStream 产生合法帧结构', () => {
    const sse = buildMockSseStream(REQ)
    expect(sse).toContain('event: context')
    expect(sse).toContain('event: reasoning')
    expect(sse).toContain('event: delta')
    expect(sse).toContain('event: usage')
    expect(sse).toContain('event: done')
    // 每帧以空行结束
    const frames = sse.trim().split('\n\n')
    for (const f of frames) {
      expect(f.startsWith('event: ')).toBe(true)
      expect(f).toContain('\ndata: ')
    }
    // 无思考模型不产出 reasoning 事件
    const plain = buildMockSseStream({ ...REQ, model: 'xw-local-default' })
    expect(plain).not.toContain('event: reasoning')
  })
})

describe('预算条计算', () => {
  it('usage.budget 字段齐全且比例正确', () => {
    setActivePinia(createPinia())
    const chat = useChatStore()
    const usage = {
      prompt_tokens: 4127,
      completion_tokens: 186,
      reasoning_tokens: 0,
      cached_tokens: 3072,
      budget: { limit: 8192, reserved_output: 1024, used: 4127 },
    }
    chat.applyStreamEvent({ type: 'usage', usage })
    expect(chat.lastUsage?.budget.limit).toBe(8192)
    expect(chat.lastUsage?.budget.reserved_output).toBe(1024)
    expect(chat.lastUsage?.budget.used).toBe(4127)
    // used / limit ≈ 50.4%
    const pct = (chat.lastUsage!.budget.used / chat.lastUsage!.budget.limit) * 100
    expect(pct).toBeGreaterThan(50)
    expect(pct).toBeLessThan(51)
  })
})
