// cloneJson 回归用例。
//
// 组件 props 里的对象来自 ref 包裹的深层响应式对象，模板解包后传给子组件的
// 是 reactive Proxy，而 structuredClone 无法克隆 Proxy，会抛
// DOMException: Proxy object could not be cloned。
// 本用例锁定这一回归。
import { describe, expect, it } from 'vitest'
import { isProxy, reactive, ref } from 'vue'
import { cloneJson } from '../lib/json'

describe('cloneJson', () => {
  it('克隆 reactive 代理不抛异常并得到普通对象', () => {
    const card = reactive({ id: 'c_1', name: '角色K', data: { name: '角色K' }, raw: {} })
    expect(() => structuredClone(card)).toThrow()

    const copy = cloneJson(card)

    expect(copy).toEqual({ id: 'c_1', name: '角色K', data: { name: '角色K' }, raw: {} })
    expect(copy).not.toBe(card)
    expect(copy.data).not.toBe(card.data)
    expect(isProxy(copy)).toBe(false)
    expect(isProxy(copy.data)).toBe(false)
  })

  it('克隆 ref 内嵌套的代理', () => {
    const holder = ref<{ id: string; raw: { tags: string[] } } | null>(null)
    holder.value = { id: 'c_2', raw: { tags: ['a'] } }

    const copy = cloneJson(holder.value)

    expect(copy).toEqual({ id: 'c_2', raw: { tags: ['a'] } })
    expect(isProxy(copy)).toBe(false)
    expect(isProxy(copy.raw)).toBe(false)
  })

  it('保留未定义键且副本与源互不影响', () => {
    const source = reactive<{ a: undefined; b: number }>({ a: undefined, b: 1 })

    const copy = cloneJson(source)

    expect('a' in copy).toBe(true)
    copy.b = 2
    expect(source.b).toBe(1)
  })

  it('数组内的代理元素也被剥离', () => {
    const list = reactive([{ id: 'x' }])

    const copy = cloneJson(list)

    expect(copy).toEqual([{ id: 'x' }])
    expect(isProxy(copy[0])).toBe(false)
  })
})
