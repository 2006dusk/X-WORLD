// 自动回合测试：用户这一轮不说话，由 AI 自己往下走。
import { beforeEach, describe, it, expect, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { chatStream } from '../api/chat'
import type { ChatRequest } from '../api/chat'
import { useChatStore } from '../stores/chat'

vi.mock('../api/chat', () => ({
  chatComplete: vi.fn(),
  chatStream: vi.fn(async () => () => undefined),
}))

const mockedChatStream = vi.mocked(chatStream)

function lastRequest(): ChatRequest {
  const calls = mockedChatStream.mock.calls
  expect(calls.length).toBeGreaterThan(0)
  return calls[calls.length - 1][0]
}

beforeEach(() => {
  setActivePinia(createPinia())
  mockedChatStream.mockClear()
  mockedChatStream.mockResolvedValue(() => undefined)
})

describe('自动回合', () => {
  it('continue 模式带上 auto_turn，且这一轮没有用户消息', async () => {
    const chat = useChatStore()
    chat.session = 's_test'

    await chat.autoTurn('continue')

    expect(mockedChatStream).toHaveBeenCalledTimes(1)
    const req = lastRequest()
    expect(req.xw.auto_turn).toBe('continue')
    // 用户没说话：请求里没有新的用户输入，气泡里也不多出一条空消息。
    expect(req.messages.filter((m) => m.role === 'user')).toHaveLength(0)
    expect(chat.messages.filter((m) => m.role === 'user')).toHaveLength(0)
  })

  it('describe 模式带上 auto_turn', async () => {
    const chat = useChatStore()
    chat.session = 's_test'

    await chat.autoTurn('describe')

    expect(lastRequest().xw.auto_turn).toBe('describe')
  })

  it('没有会话时不发请求', async () => {
    const chat = useChatStore()
    chat.session = ''

    await chat.autoTurn('continue')

    expect(mockedChatStream).not.toHaveBeenCalled()
  })

  it('普通发送的 auto_turn 为空串', async () => {
    const chat = useChatStore()
    chat.session = 's_test'

    await chat.send('说点什么')

    expect(lastRequest().xw.auto_turn).toBe('')
  })
})
