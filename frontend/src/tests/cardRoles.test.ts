import { describe, expect, it } from 'vitest'
import {
  groupCardsByRole,
  introSourceLabel,
  normalizeRelationLevel,
  normalizeRole,
} from '../lib/cardRoles'

describe('角色定位', () => {
  it('认不出来的定位按主要人物处理', () => {
    expect(normalizeRole(undefined)).toBe('primary')
    expect(normalizeRole('')).toBe('primary')
    expect(normalizeRole('乱七八糟')).toBe('primary')
    expect(normalizeRole('SUPPORTING')).toBe('supporting')
  })

  it('按定位分成 V1 与 V2 两组，顺序保持不变', () => {
    const cards = [
      { name: '角色A', role: 'primary' },
      { name: '角色D', role: 'supporting' },
      { name: '角色C' },
    ]
    const grouped = groupCardsByRole(cards)
    expect(grouped.primary.map((card) => card.name)).toEqual(['角色A', '角色C'])
    expect(grouped.supporting.map((card) => card.name)).toEqual(['角色D'])
  })
})

describe('关系程度', () => {
  it('认不出来的关系按相逢处理', () => {
    expect(normalizeRelationLevel('相逢')).toBe('相逢')
    expect(normalizeRelationLevel('相识')).toBe('相识')
    expect(normalizeRelationLevel('相熟')).toBe('相熟')
    expect(normalizeRelationLevel('主角的同门师妹')).toBe('相逢')
    expect(normalizeRelationLevel(undefined)).toBe('相逢')
  })
})

describe('引入来源', () => {
  it('给中文标签，认不出来按模型引入', () => {
    expect(introSourceLabel('user')).toBe('用户引入')
    expect(introSourceLabel('ai')).toBe('模型引入')
    expect(introSourceLabel('')).toBe('模型引入')
  })
})
