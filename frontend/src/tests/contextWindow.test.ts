// 上下文窗口测试：段归类、占比与占用率。
import { describe, it, expect } from 'vitest'
import {
  contextCategories,
  contextUsage,
} from '../lib/contextWindow'
import type { ContextReport } from '../types/chat'

function report(segments: { name: string; tokens: number }[]): ContextReport {
  return { segments, template: 'chatml', tokenizer: 'llamacpp', truncated: false }
}

describe('上下文占用', () => {
  it('有用量回执时用回执的三个数', () => {
    const usage = contextUsage({ limit: 16384, reserved_output: 1024, used: 8192 }, null)
    expect(usage.used).toBe(8192)
    expect(usage.limit).toBe(16384)
    expect(usage.reserved).toBe(1024)
    expect(usage.pct).toBe(50)
  })

  it('没有回执时按装配回执的分段合计算已用', () => {
    const usage = contextUsage(
      null,
      report([
        { name: 'history', tokens: 400 },
        { name: 'user', tokens: 100 },
      ]),
      2000,
    )
    expect(usage.used).toBe(500)
    expect(usage.limit).toBe(2000)
    expect(usage.reserved).toBe(0)
    expect(usage.pct).toBe(25)
  })

  it('上限为零时占用率是零', () => {
    expect(contextUsage(null, null, 0).pct).toBe(0)
    expect(contextUsage(null, null).pct).toBe(0)
    expect(contextUsage({ limit: 0, reserved_output: 0, used: 0 }, null).pct).toBe(0)
  })
})

describe('分段归类', () => {
  it('把段归到分类并算占比', () => {
    const rows = contextCategories(
      report([
        { name: 'history', tokens: 400 },
        { name: 'user', tokens: 100 },
        { name: 'system', tokens: 300 },
        { name: 'memory', tokens: 200 },
      ]),
    )
    expect(rows.map((row) => row.label)).toEqual(['对话消息', '系统提示词', '记忆'])
    expect(rows[0].pct).toBe(50)
    expect(rows[1].pct).toBe(30)
    expect(rows[2].pct).toBe(20)
  })

  it('按用量从多到少排', () => {
    const rows = contextCategories(
      report([
        { name: 'memory', tokens: 10 },
        { name: 'history', tokens: 900 },
        { name: 'character', tokens: 50 },
      ]),
    )
    expect(rows.map((row) => row.label)).toEqual(['对话消息', '角色卡', '记忆'])
  })

  it('认不出来的段归其他', () => {
    const rows = contextCategories(
      report([
        { name: 'history', tokens: 300 },
        { name: 'tool_result', tokens: 100 },
      ]),
    )
    expect(rows.map((row) => row.label)).toEqual(['对话消息', '其他'])
    expect(rows[1].tone).toBe(6)
  })

  it('占比合计正好一百：四舍五入的差额补给最大的一项', () => {
    const rows = contextCategories(
      report([
        { name: 'history', tokens: 333 },
        { name: 'system', tokens: 333 },
        { name: 'memory', tokens: 333 },
      ]),
    )
    const sum = rows.reduce((acc, row) => acc + row.pct, 0)
    // 二进制浮点加出来是 99.99999999999999，按显示精度比
    expect(Math.round(sum * 10) / 10).toBe(100)
  })

  it('没有回执时是空表', () => {
    expect(contextCategories(null)).toEqual([])
    expect(contextCategories(report([]))).toEqual([])
  })
})
