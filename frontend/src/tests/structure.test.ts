// 结构化回合的块状态测试：增量归块、块事件覆盖、replace 清空。
import { describe, it, expect } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { useChatStore } from '../stores/chat'

function pushAssistant(): void {
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
}

describe('结构化回合的块', () => {
  it('带 block 的增量落进对应块', () => {
    setActivePinia(createPinia())
    const chat = useChatStore()
    pushAssistant()

    chat.applyStreamEvent({ type: 'delta', content: '雨声', block: 'scene' })
    chat.applyStreamEvent({ type: 'delta', content: '“来了。”', block: 'dialogue' })

    const tail = chat.messages[0]
    expect(tail.structure?.blocks.scene).toBe('雨声')
    expect(tail.structure?.blocks.dialogue).toBe('“来了。”')
    expect(tail.content).toBe('雨声“来了。”')
  })

  it('块事件以最终解析结果覆盖增量', () => {
    setActivePinia(createPinia())
    const chat = useChatStore()
    pushAssistant()

    chat.applyStreamEvent({ type: 'delta', content: '草稿', block: 'scene' })
    chat.applyStreamEvent({
      type: 'blocks',
      blocks: { scene: '正式场景', dialogue: '正式对白' },
      labels: { scene: '场景', dialogue: '对话' },
    })

    const tail = chat.messages[0]
    expect(tail.structure?.blocks.scene).toBe('正式场景')
    expect(tail.structure?.blocks.dialogue).toBe('正式对白')
    expect(tail.structure?.labels.dialogue).toBe('对话')
  })

  it('replace 事件清掉已显示的正文与块', () => {
    setActivePinia(createPinia())
    const chat = useChatStore()
    pushAssistant()

    chat.applyStreamEvent({ type: 'delta', content: '草稿', block: 'scene' })
    chat.applyStreamEvent({ type: 'replace', reason: 'repetition' })

    const tail = chat.messages[0]
    expect(tail.content).toBe('')
    expect(tail.structure).toBe(null)
  })
})
