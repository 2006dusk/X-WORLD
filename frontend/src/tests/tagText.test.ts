// 角色卡标签的测试：拼接与切分要对得上，顿号也算分隔符。
import { describe, it, expect } from 'vitest'
import { joinTags, splitTags, toggleTag } from '../lib/tagText'

describe('角色卡标签', () => {
  it('数组拼成顿号分隔的一行', () => {
    expect(joinTags(['四爱', '女攻男受'])).toBe('四爱、女攻男受')
  })

  it('没有标签时是空串', () => {
    expect(joinTags([])).toBe('')
    expect(joinTags(undefined)).toBe('')
  })

  it('顿号切开也能拆回数组', () => {
    expect(splitTags('四爱、女攻男受')).toEqual(['四爱', '女攻男受'])
  })

  it('逗号与顿号都算分隔符', () => {
    expect(splitTags('四爱，女攻男受、足控调教')).toEqual([
      '四爱',
      '女攻男受',
      '足控调教',
    ])
  })

  it('切分时丢掉空项与重复', () => {
    expect(splitTags('四爱、 、四爱、  女攻男受 ')).toEqual(['四爱', '女攻男受'])
  })

  it('拼接与切分来回一次不变', () => {
    const tags = ['四爱', '女性支配', '足控调教']
    expect(splitTags(joinTags(tags))).toEqual(tags)
  })

  it('点一下候选标签能加上也能去掉', () => {
    expect(toggleTag(['四爱'], '足控调教')).toEqual(['四爱', '足控调教'])
    expect(toggleTag(['四爱', '足控调教'], '足控调教')).toEqual(['四爱'])
  })
})
