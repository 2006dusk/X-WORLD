// 分支动作测试：编辑定稿、切换分支、快照重建消息列表，以及编辑源文本拼装。
import { beforeEach, describe, it, expect, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { editTurn } from '../api/chat'
import { switchBranch } from '../api/sessions'
import { useChatStore } from '../stores/chat'
import type { DisplayMessage } from '../stores/chat'
import type { SessionNode, SessionSnapshot } from '../types/chat'
import { buildEditSource } from '../lib/editSource'

vi.mock('../api/chat', () => ({
  chatComplete: vi.fn(),
  chatStream: vi.fn(async () => () => undefined),
  editTurn: vi.fn(),
}))

vi.mock('../api/sessions', () => ({
  fetchSessionSnapshot: vi.fn(async () => ({
    head_id: '',
    items: [],
    tree: [],
    labels: {},
    count: 0,
  })),
  switchBranch: vi.fn(),
}))

function message(
  id: string,
  role: DisplayMessage['role'],
  content: string,
  parentId = '',
): DisplayMessage {
  return {
    id,
    parentId,
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

function node(
  id: string,
  role: SessionNode['role'],
  content: string,
  parentId = '',
): SessionNode {
  return {
    id,
    parent_id: parentId,
    role,
    content,
    summary: content.slice(0, 8),
    children: [],
  }
}

function snapshot(items: SessionNode[]): SessionSnapshot {
  return {
    head_id: items.length > 0 ? items[items.length - 1].id : '',
    items,
    tree: items,
    labels: { scene: '场景', dialogue: '对话', state: '状态' },
    count: items.length,
  }
}

beforeEach(() => {
  setActivePinia(createPinia())
  vi.mocked(editTurn).mockReset()
  vi.mocked(switchBranch).mockReset()
})

describe('分支动作', () => {
  it('editAssistant 用服务端返回的路径重建消息列表', async () => {
    const chat = useChatStore()
    chat.messages = [
      message('u1', 'user', '输入'),
      message('a1', 'assistant', '旧回复', 'u1'),
    ]
    vi.mocked(editTurn).mockResolvedValue(
      snapshot([node('u1', 'user', '输入'), node('a2', 'assistant', '改过的回复', 'u1')]),
    )

    await chat.editAssistant('a1', '改过的内容')

    const req = vi.mocked(editTurn).mock.calls[0][0]
    expect(req.parent_id).toBe('u1')
    expect(req.content).toBe('改过的内容')
    expect(chat.messages.map((m) => m.content)).toEqual(['输入', '改过的回复'])
    expect(chat.headId).toBe('a2')
  })

  it('editAssistant 失败时保留原消息', async () => {
    const chat = useChatStore()
    chat.messages = [message('u1', 'user', '输入')]
    vi.mocked(editTurn).mockRejectedValue(new Error('失败'))

    await chat.editAssistant('u1', '改过的内容')

    expect(chat.messages.map((m) => m.content)).toEqual(['输入'])
  })

  it('editAssistant 把服务端写回的世界状态存进面板', async () => {
    const chat = useChatStore()
    chat.messages = [message('a1', 'assistant', '旧回复', 'u1')]
    const result = snapshot([node('a2', 'assistant', '改过的回复', 'u1')])
    result.state = {
      character_name: '角色K',
      location: '地址U',
      position: '控制台前',
      items: [],
      scene_items: [],
      map: '地址N',
      time: '0700',
      updated_at: null,
    }
    vi.mocked(editTurn).mockResolvedValue(result)

    await chat.editAssistant('a1', '改过的内容')

    expect(chat.worldState?.time).toBe('0700')
  })

  it('switchBranch 用返回的路径重建消息列表', async () => {
    const chat = useChatStore()
    chat.messages = [message('a2', 'assistant', '新分支回复', 'u1')]
    vi.mocked(switchBranch).mockResolvedValue(
      snapshot([node('u1', 'user', '输入'), node('a1', 'assistant', '旧分支回复', 'u1')]),
    )

    await chat.switchBranch('a1')

    expect(vi.mocked(switchBranch).mock.calls[0][1]).toBe('a1')
    expect(chat.messages.map((m) => m.content)).toEqual(['输入', '旧分支回复'])
    expect(chat.headId).toBe('a1')
  })

  it('applySnapshot 重建结构块并记下分支树', () => {
    const chat = useChatStore()
    const item = node('a1', 'assistant', '正文', 'u1')
    item.blocks = { scene: '雨夜', dialogue: '“走吧。”', state: '时间：0635' }

    chat.applySnapshot(snapshot([item]))

    expect(chat.messages[0].structure?.blocks).toEqual({ scene: '雨夜', dialogue: '“走吧。”' })
    expect(chat.messages[0].rawBlocks?.state).toBe('时间：0635')
    expect(chat.tree).toHaveLength(1)
    expect(chat.headId).toBe('a1')
  })
})

describe('编辑源文本', () => {
  it('按场景、对白、状态三段拼出带标记的文本', () => {
    const text = buildEditSource(
      { scene: '雨夜', dialogue: '“走吧。”', state: '时间：0635' },
      { scene: '场景', dialogue: '对话', state: '状态' },
    )
    expect(text).toBe('【场景】\n雨夜\n\n【对话】\n“走吧。”\n\n【状态】\n时间：0635')
  })

  it('没有块时返回空串', () => {
    expect(buildEditSource(null, {})).toBe('')
  })
})
