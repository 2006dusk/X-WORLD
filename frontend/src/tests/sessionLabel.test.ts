// 会话列表标签的测试：侧栏只写角色名，分支数只在真的分叉时写。
import { describe, it, expect } from 'vitest'
import { branchCountLabel } from '../lib/sessionLabel'

describe('会话列表标签', () => {
  it('没有分叉就不写分支数', () => {
    expect(branchCountLabel(0)).toBe('')
  })

  it('分叉数就是分支条数', () => {
    expect(branchCountLabel(3)).toBe('3 条分支')
  })
})
