// 轮次操作测试：重新生成与改写都针对指定的一轮。
import { beforeEach, describe, it, expect, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { chatStream } from '../api/chat'
import type { ChatRequest } from '../api/chat'
import { useChatStore } from '../stores/chat'
import type { DisplayMessage } from '../stores/chat'

vi.mock('../api/chat', () => ({
  chatComplete: vi.fn(),
  chatStream: vi.fn(async () => () => undefined),
}))

const mockedChatStream = vi.mocked(chatStream)

function message(id: string, role: DisplayMessage['role'], content: string): DisplayMessage {
  return {
    id,
    role,
    content,
    reasoning: '',
    usage: null,
    contextReport: null,
    structure: null,
    interrupted: false,
    error: null,
    createdAt: 0,
  }
}

function seedTurn(): ReturnType<typeof useChatStore> {
  const chat = useChatStore()
  chat.messages.push(
    message('u_turn', 'user', '原始输入'),
    message('a_turn', 'assistant', '旧回复'),
  )
  return chat
}

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

describe('轮次操作', () => {
  it('regenerate 指定要换掉的那条回复，并带上 target_id', async () => {
    const chat = seedTurn()

    await chat.regenerate('a_turn')

    expect(mockedChatStream).toHaveBeenCalledTimes(1)
    const req = lastRequest()
    expect(req.turn_action).toBe('regenerate')
    expect(req.target_id).toBe('a_turn')
    // 旧回复已从当前路径移除，末尾是 generate 新建的空 assistant 占位。
    expect(req.messages).toEqual([
      { role: 'user', content: '原始输入' },
      { role: 'assistant', content: '' },
    ])
    expect(chat.messages.some((m) => m.id === 'a_turn')).toBe(false)
    expect(chat.messages.filter((m) => m.role === 'user')).toHaveLength(1)
    expect(chat.messages[0]).toMatchObject({ id: 'u_turn', role: 'user' })
  })

  it('regenerate 中间某一轮时，其后的几轮离开当前路径', async () => {
    const chat = useChatStore()
    chat.messages.push(
      message('u_1', 'user', '第一问'),
      message('a_1', 'assistant', '第一答'),
      message('u_2', 'user', '第二问'),
      message('a_2', 'assistant', '第二答'),
    )

    await chat.regenerate('a_1')

    expect(lastRequest().target_id).toBe('a_1')
    // 被换掉的那条回复与其后的几轮都不在当前路径上，只剩第一问与新的占位
    expect(chat.messages).toHaveLength(2)
    expect(chat.messages[0].id).toBe('u_1')
    expect(chat.messages[1].role).toBe('assistant')
    expect(
      chat.messages.some((m) => m.id === 'a_1' || m.id === 'u_2' || m.id === 'a_2'),
    ).toBe(false)
  })

  it('revise 改写指定的那条用户输入，并带上 target_id', async () => {
    const chat = seedTurn()

    await chat.revise('u_turn', '改过的输入')

    expect(chat.messages[0].content).toBe('改过的输入')
    expect(chat.messages.some((m) => m.id === 'a_turn')).toBe(false)
    expect(chat.messages.filter((m) => m.role === 'user')).toHaveLength(1)
    expect(mockedChatStream).toHaveBeenCalledTimes(1)
    const req = lastRequest()
    expect(req.turn_action).toBe('revise')
    expect(req.target_id).toBe('u_turn')
    expect(req.messages).toEqual([
      { role: 'user', content: '改过的输入' },
      { role: 'assistant', content: '' },
    ])
  })

  it('lastUserId 与 lastAssistantId 返回最后一轮两条消息的 id', () => {
    const chat = seedTurn()

    expect(chat.lastUserId).toBe('u_turn')
    expect(chat.lastAssistantId).toBe('a_turn')
  })
})
