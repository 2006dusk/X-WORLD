// 分支树索引测试：树的形状由后端决定，前端只按索引渲染。
import { describe, it, expect } from 'vitest'
import {
  buildTreeIndex,
  collapseGroups,
  groupDepth,
  layoutTree,
} from '../lib/branchTree'
import type { SessionNode } from '../types/chat'

function node(
  id: string,
  parent: string,
  role: 'user' | 'assistant' = 'user',
  summary = id,
): SessionNode {
  return { id, parent_id: parent, role, summary }
}

describe('分支树索引', () => {
  it('线性会话是一条线，没有分叉', () => {
    const tree = [
      node('u1', ''),
      node('a1', 'u1', 'assistant'),
      node('u2', 'a1'),
      node('a2', 'u2', 'assistant'),
    ]

    const index = buildTreeIndex(tree, 'a2')

    expect(index.forkCount).toBe(0)
    expect(index.roots.map((n) => n.id)).toEqual(['u1'])
    expect(index.childrenOf.get('a1')?.map((n) => n.id)).toEqual(['u2'])
    expect(index.currentIds.size).toBe(4)
  })

  it('同一个父节点下有两条线时算一处分叉', () => {
    const tree = [
      node('u1', ''),
      node('a1', 'u1', 'assistant'),
      node('u2', 'a1'),
      node('a2', 'u2', 'assistant'),
      node('u2b', 'a1'),
      node('a2b', 'u2b', 'assistant'),
    ]

    const index = buildTreeIndex(tree, 'a2b')

    expect(index.forkCount).toBe(1)
    expect(index.childrenOf.get('a1')?.map((n) => n.id).sort()).toEqual(['u2', 'u2b'])
  })

  it('当前路径那条排最左，并被高亮', () => {
    const tree = [
      node('u1', ''),
      node('a1', 'u1', 'assistant'),
      node('u2', 'a1'),
      node('a2', 'u2', 'assistant'),
      node('u2b', 'a1'),
      node('a2b', 'u2b', 'assistant'),
    ]

    const index = buildTreeIndex(tree, 'a2b')

    expect(index.childrenOf.get('a1')?.[0].id).toBe('u2b')
    expect(index.currentIds.has('a2b')).toBe(true)
    expect(index.currentIds.has('a2')).toBe(false)
  })

  it('父指针缺失时按 children 字段补齐连线', () => {
    const tree: SessionNode[] = [
      { id: 'u1', parent_id: '', role: 'user', summary: 'u1', children: ['a1'] },
      { id: 'a1', parent_id: '', role: 'assistant', summary: 'a1' },
    ]

    const index = buildTreeIndex(tree, 'a1')

    expect(index.childrenOf.get('u1')?.map((n) => n.id)).toEqual(['a1'])
  })

  it('树形图：线性会话是一条竖线', () => {
    const tree = [
      node('u1', ''),
      node('a1', 'u1', 'assistant'),
      node('u2', 'a1'),
    ]

    const layout = layoutTree(tree, 'u2')

    // 含虚拟根，一共四个点
    expect(layout.nodes.length).toBe(4)
    expect(new Set(layout.nodes.map((n) => n.x)).size).toBe(1)
    const ys = layout.nodes.map((n) => n.y)
    expect(ys).toEqual([...ys].sort((a, b) => a - b))
    expect(layout.edges.length).toBe(3)
  })

  it('树形图：分叉时两条线分开排，起点在中间', () => {
    const tree = [
      node('u1', ''),
      node('a1', 'u1', 'assistant'),
      node('u1b', ''),
      node('a1b', 'u1b', 'assistant'),
    ]

    const layout = layoutTree(tree, 'a1b')
    const byId = new Map(layout.nodes.map((n) => [n.id, n]))
    const left = byId.get('u1')?.x ?? 0
    const right = byId.get('u1b')?.x ?? 0
    const rootX = byId.get('')?.x ?? 0

    expect(left).not.toBe(right)
    expect(rootX).toBeGreaterThan(Math.min(left, right))
    expect(rootX).toBeLessThan(Math.max(left, right))
    // 起点是分叉点，两条线各有一个子节点
    expect(byId.get('')?.fork).toBe(true)
    expect(byId.get('u1')?.fork).toBe(false)
    expect(byId.get('u1b')?.fork).toBe(false)
  })

  it('树形图：当前路径上的点被标出来', () => {
    const tree = [
      node('u1', ''),
      node('a1', 'u1', 'assistant'),
      node('u1b', ''),
      node('a1b', 'u1b', 'assistant'),
    ]

    const layout = layoutTree(tree, 'a1b')
    const byId = new Map(layout.nodes.map((n) => [n.id, n]))

    expect(byId.get('a1b')?.current).toBe(true)
    expect(byId.get('a1b')?.head).toBe(true)
    expect(byId.get('u1')?.current).toBe(false)
    expect(byId.get('u1b')?.current).toBe(true)
  })

  it('虚拟根下挂着全部根节点', () => {
    const tree = [node('u1', ''), node('u1b', '')]

    const index = buildTreeIndex(tree, 'u1b')

    expect(index.childrenOf.get('')?.map((n) => n.id).sort()).toEqual(['u1', 'u1b'])
  })

  it('根级分叉算一处分叉', () => {
    const tree = [
      node('u1', ''),
      node('a1', 'u1', 'assistant'),
      node('u2', 'a1'),
      node('a2', 'u2', 'assistant'),
      node('u1b', ''),
      node('a1b', 'u1b', 'assistant'),
    ]

    const index = buildTreeIndex(tree, 'a1b')

    expect(index.forkCount).toBe(1)
    expect(index.roots.map((n) => n.id).sort()).toEqual(['u1', 'u1b'])
  })

  it('根级分叉时当前路径那条排最左', () => {
    const tree = [
      node('u1', ''),
      node('a1', 'u1', 'assistant'),
      node('u1b', ''),
      node('a1b', 'u1b', 'assistant'),
    ]

    const index = buildTreeIndex(tree, 'a1b')

    expect(index.roots[0].id).toBe('u1b')
    expect(index.currentIds.has('u1b')).toBe(true)
    expect(index.currentIds.has('u1')).toBe(false)
  })
})

describe('折叠成组', () => {
  it('没有分叉的一段并成一个点', () => {
    const tree = [
      node('u1', ''),
      node('a1', 'u1', 'assistant'),
      node('u2', 'a1'),
      node('a2', 'u2', 'assistant'),
    ]

    const groups = collapseGroups(tree, 'a2')

    expect(groups.length).toBe(1)
    expect(groups[0].id).toBe('a2')
    expect(groups[0].rounds).toBe(4)
    expect(groups[0].parent_id).toBe('')
    expect(groups[0].children).toEqual([])
  })

  it('分叉点保留，链上的中间节点不再出现', () => {
    const tree = [
      node('u1', ''),
      node('a1', 'u1', 'assistant'),
      node('u2', 'a1'),
      node('a2', 'u2', 'assistant'),
      node('u2b', 'a1'),
      node('a2b', 'u2b', 'assistant'),
    ]

    const groups = collapseGroups(tree, 'a2b')
    const ids = groups.map((group) => group.id).sort()

    // a1 是分叉点：它自己一组，下面两条线各成一组
    expect(ids).toEqual(['a1', 'a2', 'a2b'])
    expect([...(groups.find((g) => g.id === 'a1')?.children ?? [])].sort()).toEqual(['a2', 'a2b'])
    expect(groups.find((g) => g.id === 'a1')?.rounds).toBe(2)
    expect(groups.find((g) => g.id === 'a2')?.rounds).toBe(2)
    expect(groups.find((g) => g.id === 'a2b')?.rounds).toBe(2)
  })

  it('折叠后分叉数不变', () => {
    const tree = [
      node('u1', ''),
      node('a1', 'u1', 'assistant'),
      node('u2', 'a1'),
      node('a2', 'u2', 'assistant'),
      node('u2b', 'a1'),
      node('a2b', 'u2b', 'assistant'),
    ]

    const groups = collapseGroups(tree, 'a2b')

    expect(buildTreeIndex(groups, 'a2b').forkCount).toBe(1)
  })

  it('这一段的正文拼在一起', () => {
    const tree: SessionNode[] = [
      { id: 'u1', parent_id: '', role: 'user', summary: 'u1', content: '第一句' },
      { id: 'a1', parent_id: 'u1', role: 'assistant', summary: 'a1', content: '第二句' },
      { id: 'u2', parent_id: 'a1', role: 'user', summary: 'u2', content: '第三句' },
    ]

    const groups = collapseGroups(tree, 'u2')

    expect(groups[0].content).toBe('第一句\n\n第二句\n\n第三句')
  })

  it('根级分叉各自成组', () => {
    const tree = [
      node('u1', ''),
      node('a1', 'u1', 'assistant'),
      node('u1b', ''),
      node('a1b', 'u1b', 'assistant'),
    ]

    const groups = collapseGroups(tree, 'a1b')

    expect(groups.map((group) => group.id).sort()).toEqual(['a1', 'a1b'])
    expect(groups.every((group) => group.parent_id === '')).toBe(true)
  })
})

describe('分组成员与深度', () => {
  const forked = [
    node('u1', ''),
    node('a1', 'u1', 'assistant'),
    node('u2', 'a1'),
    node('a2', 'u2', 'assistant'),
    node('u2b', 'a1'),
    node('a2b', 'u2b', 'assistant'),
  ]

  it('分组带成员节点 id：删这一段按它发删除请求', () => {
    const groups = collapseGroups(forked, 'a2b')
    const byId = new Map(groups.map((group) => [group.id, group]))

    expect(byId.get('a1')?.members).toEqual(['u1', 'a1'])
    expect(byId.get('a2')?.members).toEqual(['u2', 'a2'])
    expect(byId.get('a2b')?.members).toEqual(['u2b', 'a2b'])
  })

  it('成员 id 与这一组的轮数对得上', () => {
    const groups = collapseGroups(forked, 'a2b')

    for (const group of groups) {
      expect(group.members.length).toBe(group.rounds)
    }
  })

  it('分组深度：主线那组是 0，分出去的线是 1', () => {
    const groups = collapseGroups(forked, 'a2b')

    expect(groupDepth(groups, 'a1')).toBe(0)
    expect(groupDepth(groups, 'a2')).toBe(1)
    expect(groupDepth(groups, 'a2b')).toBe(1)
  })

  it('线性会话只有一组，深度是 0', () => {
    const groups = collapseGroups(
      [node('u1', ''), node('a1', 'u1', 'assistant'), node('u2', 'a1')],
      'u2',
    )

    expect(groups.length).toBe(1)
    expect(groupDepth(groups, groups[0].id)).toBe(0)
  })
})

describe('横排', () => {
  it('横排时深度占横坐标，两条线在纵坐标上分开', () => {
    const tree = [
      node('u1', ''),
      node('a1', 'u1', 'assistant'),
      node('u1b', ''),
      node('a1b', 'u1b', 'assistant'),
    ]

    const layout = layoutTree(tree, 'a1b', { direction: 'horizontal' })
    const byId = new Map(layout.nodes.map((n) => [n.id, n]))

    // 深度一层往右挪一层
    expect(byId.get('')?.x).toBeLessThan(byId.get('u1')?.x ?? 0)
    expect(byId.get('u1')?.x).toBeLessThan(byId.get('a1')?.x ?? 0)
    // 两条线在纵坐标上分开
    expect(byId.get('u1')?.y).not.toBe(byId.get('u1b')?.y)
  })

  it('横排时同一层的点横坐标相同，点数与连线数不变', () => {
    const tree = [
      node('u1', ''),
      node('a1', 'u1', 'assistant'),
      node('u1b', ''),
      node('a1b', 'u1b', 'assistant'),
    ]

    const vertical = layoutTree(tree, 'a1b')
    const horizontal = layoutTree(tree, 'a1b', { direction: 'horizontal' })

    expect(horizontal.nodes.length).toBe(vertical.nodes.length)
    expect(horizontal.edges.length).toBe(vertical.edges.length)
    const byDepth = new Map<number, number[]>()
    for (const item of horizontal.nodes) {
      byDepth.set(item.depth, [...(byDepth.get(item.depth) ?? []), item.x])
    }
    for (const xs of byDepth.values()) {
      expect(new Set(xs).size).toBe(1)
    }
  })
})
