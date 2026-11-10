// 分支树的索引计算：当前路径、父子索引、根节点与分叉点。
// 纯函数，组件只按索引渲染。
//
// 树是什么形状由后端决定：改写某一轮的用户输入或定稿某一条角色回复会
// 新开节点，于是同一个父节点下有两条线；重新生成是原地替换，不新开
// 节点，所以它不会让树多出一支。
//
// 后端把没有父节点的节点收在 children[""] 下，也就是树有一个虚拟根。
// 前端按同一口径把根挂在虚拟根上，根级分叉同样算一处分叉。
import type { SessionNode } from '../types/chat'

/** 虚拟根的键：父节点 id 为空的那些节点挂在它下面。 */
export const ROOT_KEY = ''

export interface TreeIndex {
  /** 当前路径上的节点 id：从 head 沿父指针回溯 */
  currentIds: Set<string>
  /** 父节点 id 到子节点列表，当前路径那条排最左。虚拟根用 ROOT_KEY 作键。 */
  childrenOf: Map<string, SessionNode[]>
  /** 挂在虚拟根下的节点，当前路径那条排最左 */
  roots: SessionNode[]
  /** 分叉点个数：有两条以上子节点的节点，虚拟根也算 */
  forkCount: number
}

/** 当前路径：从 head 沿父指针回溯。 */
export function currentPathIds(tree: SessionNode[], headId: string): Set<string> {
  const byId = new Map(tree.map((node) => [node.id, node]))
  const ids = new Set<string>()
  let current = headId
  while (current && byId.has(current) && !ids.has(current)) {
    ids.add(current)
    current = byId.get(current)?.parent_id ?? ''
  }
  return ids
}

/** 父子索引。children 字段是连线依据，父指针兜底补齐，剩下挂虚拟根。 */
export function buildChildrenIndex(
  tree: SessionNode[],
  currentIds: Set<string>,
): Map<string, SessionNode[]> {
  const byId = new Map(tree.map((node) => [node.id, node]))
  const map = new Map<string, SessionNode[]>()
  const linked = new Set<string>()
  const add = (parent: string, child: SessionNode): void => {
    const list = map.get(parent) ?? []
    if (list.some((item) => item.id === child.id)) return
    list.push(child)
    map.set(parent, list)
    linked.add(child.id)
  }
  // children 字段是连线依据
  for (const node of tree) {
    for (const id of node.children ?? []) {
      const child = byId.get(id)
      if (child) add(node.id, child)
    }
  }
  // 父指针兜底补齐
  for (const node of tree) {
    const parent = node.parent_id
    if (!parent || !byId.has(parent)) continue
    add(parent, node)
  }
  // 没有父节点、也没被别的节点挂着的，挂在虚拟根下
  for (const node of tree) {
    if (linked.has(node.id)) continue
    add(ROOT_KEY, node)
  }
  // 当前路径所在的那条分支排最左，其余按原顺序
  for (const [id, kids] of map) {
    kids.sort((a, b) => (currentIds.has(a.id) ? 0 : 1) - (currentIds.has(b.id) ? 0 : 1))
    map.set(id, kids)
  }
  return map
}

/** 根节点：挂在虚拟根下的那些。 */
export function rootNodes(tree: SessionNode[]): SessionNode[] {
  return buildChildrenIndex(tree, new Set()).get(ROOT_KEY) ?? []
}

/** 一次算全：组件与测试共用同一套口径。 */
export function buildTreeIndex(tree: SessionNode[], headId: string): TreeIndex {
  const currentIds = currentPathIds(tree, headId)
  const childrenOf = buildChildrenIndex(tree, currentIds)
  let forkCount = 0
  for (const kids of childrenOf.values()) {
    if (kids.length > 1) forkCount += 1
  }
  return { currentIds, childrenOf, roots: childrenOf.get(ROOT_KEY) ?? [], forkCount }
}

/** 树形图上的一个点。 */
export interface GraphNode {
  id: string
  role: string
  summary: string
  /** 这一组的正文，悬停时显示 */
  content: string
  x: number
  y: number
  depth: number
  /** 在当前路径上 */
  current: boolean
  /** 当前路径末端 */
  head: boolean
  /** 分叉点：子节点两条以上 */
  fork: boolean
  /** 虚拟根，即对话起点 */
  root: boolean
}

/** 树形图上的一条线，父节点与子节点之间一条。 */
export interface GraphEdge {
  key: string
  x1: number
  y1: number
  x2: number
  y2: number
  /** 这条线在当前路径上 */
  current: boolean
}

export interface GraphLayout {
  nodes: GraphNode[]
  edges: GraphEdge[]
  width: number
  height: number
}

/** 树的走向：竖排是深度往下，横排是深度往右。 */
export type TreeDirection = 'vertical' | 'horizontal'

export interface GraphOptions {
  /** 每层的高度 */
  rowHeight?: number
  /** 每条分支占的横向宽度 */
  colWidth?: number
  /** 同一层里两个点的最小间距 */
  minGap?: number
  /** 树的走向，默认竖排 */
  direction?: TreeDirection
}

/**
 * 把树排成树形图：点与点之间用线连起来。
 *
 * 纵坐标按深度排，横坐标按叶子占位排：每个叶子占一格，内部节点取子节点
 * 位置的平均，于是分叉处自然张开，两点之间就是一组。
 */
export function layoutTree(
  tree: SessionNode[],
  headId: string,
  options: GraphOptions = {},
): GraphLayout {
  const rowHeight = options.rowHeight ?? 26
  const colWidth = options.colWidth ?? 26
  const minGap = options.minGap ?? 16
  // 横排时深度占横坐标、叶子占纵坐标；竖排时两条轴反过来。
  const horizontal = options.direction === 'horizontal'
  const acrossStep = horizontal ? rowHeight : colWidth
  const alongStep = horizontal ? colWidth : rowHeight
  const index = buildTreeIndex(tree, headId)
  const byId = new Map(tree.map((node) => [node.id, node]))
  const positions = new Map<string, { across: number; along: number; depth: number }>()
  let slot = 0

  const childrenOfNode = (id: string): SessionNode[] =>
    id === ROOT_KEY ? index.roots : (index.childrenOf.get(id) ?? [])

  // 两条轴分开算：along 是深度那一头，across 是横跨叶子那一头。
  const place = (id: string, depth: number): number => {
    const kids = childrenOfNode(id)
    const along = depth * alongStep + (horizontal ? alongStep / 2 : 0)
    if (kids.length === 0) {
      const across = slot * acrossStep + acrossStep / 2
      slot += 1
      positions.set(id, { across, along, depth })
      return across
    }
    const xs = kids.map((kid) => place(kid.id, depth + 1))
    const across = xs.reduce((sum, value) => sum + value, 0) / xs.length
    positions.set(id, { across, along, depth })
    return across
  }

  place(ROOT_KEY, 0)

  /** 两条轴换算成屏幕坐标：横排时深度在横坐标，竖排时在纵坐标。 */
  const toXY = (pos: { across: number; along: number }): { x: number; y: number } =>
    horizontal ? { x: pos.along, y: pos.across } : { x: pos.across, y: pos.along }

  // 同一层里挨太近的点推开，免得两个点叠在一起
  const byDepth = new Map<number, string[]>()
  for (const [id, pos] of positions) {
    const list = byDepth.get(pos.depth) ?? []
    list.push(id)
    byDepth.set(pos.depth, list)
  }
  for (const ids of byDepth.values()) {
    ids.sort((a, b) => (positions.get(a)?.across ?? 0) - (positions.get(b)?.across ?? 0))
    for (let i = 1; i < ids.length; i += 1) {
      const prev = positions.get(ids[i - 1])
      const cur = positions.get(ids[i])
      if (!prev || !cur) continue
      if (cur.across - prev.across < minGap) cur.across = prev.across + minGap
    }
  }

  const edges: GraphEdge[] = []
  for (const [id, pos] of positions) {
    for (const kid of childrenOfNode(id)) {
      const childPos = positions.get(kid.id)
      if (!childPos) continue
      const from = toXY(pos)
      const to = toXY(childPos)
      edges.push({
        key: id + '>' + kid.id,
        x1: from.x,
        y1: from.y,
        x2: to.x,
        y2: to.y,
        current: index.currentIds.has(id) && index.currentIds.has(kid.id),
      })
    }
  }

  const nodes: GraphNode[] = []
  for (const [id, pos] of positions) {
    if (id === ROOT_KEY) {
      nodes.push({
        id,
        role: 'root',
        summary: '对话起点',
        content: '全部对话从这条线分出去',
        x: toXY(pos).x,
        y: toXY(pos).y,
        depth: pos.depth,
        current: false,
        head: false,
        fork: index.roots.length > 1,
        root: true,
      })
      continue
    }
    const node = byId.get(id)
    const kids = index.childrenOf.get(id) ?? []
    nodes.push({
      id,
      role: node?.role ?? 'user',
      summary: node?.summary ?? '',
      content: node?.content ?? '',
      x: toXY(pos).x,
      y: toXY(pos).y,
      depth: pos.depth,
      current: index.currentIds.has(id),
      head: id === headId,
      fork: kids.length > 1,
      root: false,
    })
  }
  nodes.sort((a, b) => (horizontal ? a.x - b.x || a.y - b.y : a.y - b.y || a.x - b.x))

  let maxAcross = acrossStep / 2
  let maxAlong = 0
  let maxDepth = 0
  for (const pos of positions.values()) {
    maxAcross = Math.max(maxAcross, pos.across)
    maxAlong = Math.max(maxAlong, pos.along)
    maxDepth = Math.max(maxDepth, pos.depth)
  }
  return {
    nodes,
    edges,
    width: Math.ceil((horizontal ? maxAlong : maxAcross) + colWidth / 2 + 10),
    height: horizontal
      ? Math.ceil(maxAcross + rowHeight / 2 + 10)
      : (maxDepth + 1) * rowHeight + 10,
  }
}

/** 折叠后的一组：一串没有分叉的节点并成一个点。 */
export interface GroupNode extends SessionNode {
  /** 这一组含多少轮 */
  rounds: number
  /** 这一组的成员节点 id：删这一段时按它发删除请求 */
  members: string[]
}

/**
 * 把没有分叉的连节点并成一组，树上只留分叉点与末端点。
 *
 * 没有分叉的地方就是同一条线，逐轮画点没有信息量：那一段记一个点就够。
 * 这一组的成员从上一个点连到这一个点，悬停时显示的就是这一整段。
 *
 * 组落在这一串的末端：末端是分叉点或这条线的最后一点。
 */
export function collapseGroups(tree: SessionNode[], headId: string): GroupNode[] {
  const index = buildTreeIndex(tree, headId)
  const byId = new Map(tree.map((node) => [node.id, node]))
  const order = new Map(tree.map((node, index) => [node.id, index]))
  const kidsOf = (id: string): SessionNode[] =>
    id === ROOT_KEY ? index.roots : (index.childrenOf.get(id) ?? [])

  // 一串没有分叉的节点归成一组，组落在这一串的末端。
  const endCache = new Map<string, string>()
  const groupEnd = (id: string): string => {
    const cached = endCache.get(id)
    if (cached) return cached
    const chain: string[] = []
    let current = id
    while (true) {
      chain.push(current)
      const kids = kidsOf(current)
      if (kids.length !== 1) break
      const next = kids[0].id
      if (chain.includes(next)) break
      current = next
    }
    for (const nodeId of chain) endCache.set(nodeId, current)
    return current
  }

  const groups: GroupNode[] = []
  for (const node of tree) {
    const end = groupEnd(node.id)
    // 只有组尾成组：分叉点，或这条线的最后一点。
    if (end !== node.id) continue
    // 这一组的成员：从组尾往上走，父节点还在这一组里就继续往上。
    const members: string[] = []
    let cursor = end
    while (cursor) {
      members.unshift(cursor)
      const parent = byId.get(cursor)?.parent_id ?? ''
      if (!parent || groupEnd(parent) !== end) break
      cursor = parent
    }
    const endNode = byId.get(end)
    if (!endNode) continue
    // 子组：末端下面每条线各成一组。
    const childGroups: string[] = []
    for (const kid of kidsOf(end)) {
      const group = groupEnd(kid.id)
      if (!childGroups.includes(group)) childGroups.push(group)
    }
    // 组头的父节点所在的那一组是本组的父组；组头挂在虚拟根下时没有父组。
    const rawParent = byId.get(members[0])?.parent_id ?? ''
    const parentGroup = rawParent ? groupEnd(rawParent) : ''
    groups.push({
      id: end,
      parent_id: parentGroup,
      role: endNode.role,
      summary: endNode.summary,
      content: members
        .map((id) => byId.get(id)?.content ?? '')
        .filter((text) => text.trim())
        .join('\n\n'),
      blocks: endNode.blocks,
      created_at: endNode.created_at,
      children: childGroups,
      rounds: members.length,
      members: [...members],
    })
  }
  groups.sort((a, b) => (order.get(a.id) ?? 0) - (order.get(b.id) ?? 0))
  return groups
}

/**
 * 一组在折叠树里的深度：主线那组是 0，往下每层加一。
 *
 * 会话列表的分支清单按这个缩进，一眼看出哪几段是从哪一段分出去的。
 */
export function groupDepth(groups: GroupNode[], id: string): number {
  const byId = new Map(groups.map((group) => [group.id, group]))
  const seen = new Set<string>()
  let depth = 0
  let current = byId.get(id)?.parent_id ?? ''
  while (current && byId.has(current) && !seen.has(current)) {
    seen.add(current)
    depth += 1
    current = byId.get(current)?.parent_id ?? ''
  }
  return depth
}
