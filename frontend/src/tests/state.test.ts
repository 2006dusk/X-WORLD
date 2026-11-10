// 世界状态测试：state 事件写入面板数据源，状态块按块归位。
import { describe, it, expect } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { useChatStore } from '../stores/chat'
import type { WorldState } from '../types/chat'

const STATE: WorldState = {
  character_name: '角色K',
  location: '地址U二楼',
  position: '控制台前',
  items: ['磁带', '老花镜'],
  scene_items: ['推子', '落地监听音箱'],
  map: '地址N',
  time: '雨夜',
  updated_at: '2026-11-06T12:00:00+00:00',
}

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

describe('世界状态', () => {
  it('state 事件写入世界状态', () => {
    setActivePinia(createPinia())
    const chat = useChatStore()
    expect(chat.worldState).toBe(null)

    chat.applyStreamEvent({ type: 'state', state: STATE })

    expect(chat.worldState?.location).toBe('地址U二楼')
    expect(chat.worldState?.position).toBe('控制台前')
    expect(chat.worldState?.items).toEqual(['磁带', '老花镜'])
    expect(chat.worldState?.scene_items).toEqual(['推子', '落地监听音箱'])
    expect(chat.worldState?.map).toBe('地址N')
    expect(chat.worldState?.time).toBe('雨夜')
  })

  it('状态块不混进场景与对白', () => {
    setActivePinia(createPinia())
    const chat = useChatStore()
    pushAssistant()

    chat.applyStreamEvent({ type: 'delta', content: '雨声', block: 'scene' })
    chat.applyStreamEvent({ type: 'delta', content: '当前位置：地址S', block: 'state' })

    const tail = chat.messages[0]
    expect(tail.structure?.blocks.scene).toBe('雨声')
    expect(tail.structure?.blocks.state).toBeUndefined()
    expect(tail.content).toBe('雨声')
  })

  it('状态块不进对话气泡', () => {
    setActivePinia(createPinia())
    const chat = useChatStore()
    pushAssistant()

    chat.applyStreamEvent({ type: 'delta', content: '雨声', block: 'scene' })
    chat.applyStreamEvent({ type: 'delta', content: '“来了。”', block: 'dialogue' })
    chat.applyStreamEvent({ type: 'delta', content: '当前位置：地址S', block: 'state' })
    chat.applyStreamEvent({
      type: 'blocks',
      blocks: { scene: '雨声', dialogue: '“来了。”', state: '当前位置：地址S' },
      labels: { scene: '场景', dialogue: '对话', state: '状态' },
    })

    const tail = chat.messages[0]
    expect(tail.structure?.blocks.state).toBeUndefined()
    expect(tail.content).toBe('雨声“来了。”')
    expect(tail.content).not.toContain('当前位置')
  })
})
