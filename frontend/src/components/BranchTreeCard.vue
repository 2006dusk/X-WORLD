<script setup lang="ts">
// 分支树卡片：会话的分支结构，常驻右侧面板底部。
// 画的是树形图：节点是点，父节点与子节点之间连一条线，两个点之间是一组。
// 平时不铺开每一轮的对话，鼠标悬停在点上才显示那一组的内容。
//
// 分叉点用叉号表示，不再写「几条分支」。
// 什么时候才会分叉：改写某一轮的用户输入，或定稿某一条角色回复。
// 重新生成是原地换掉那一条回复，不新增节点，所以不算分支。
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { ChevronDown, ChevronRight, GitBranch, GitFork, Trash2 } from 'lucide-vue-next'
import type { SessionNode } from '../types/chat'
import { useChatStore } from '../stores/chat'
import {
  buildTreeIndex,
  collapseGroups,
  layoutTree,
  type GroupNode,
} from '../lib/branchTree'

const props = defineProps<{
  tree: SessionNode[]
  headId: string
}>()

const chat = useChatStore()
const collapsed = ref(false)
/** 宽屏（PC）横排，窄屏（移动端）竖排。断点与状态浮层一致。 */
const wide = ref(false)
const wideQuery = window.matchMedia('(min-width: 768px)')

function syncWidth(): void {
  wide.value = wideQuery.matches
}

onMounted(() => {
  syncWidth()
  wideQuery.addEventListener('change', syncWidth)
})

onUnmounted(() => {
  wideQuery.removeEventListener('change', syncWidth)
  if (hideTimer !== null) window.clearTimeout(hideTimer)
})
/** 悬停中的点。空值表示没有悬停，不能用空串：空串是虚拟根的 id。 */
const hoverId = ref<string | null>(null)
/** 提示框的位置：跟着点走，固定定位，不占图里的地方。 */
const tipStyle = ref<Record<string, string>>({})
/**
 * 隐藏提示的延时。
 *
 * 提示框里有删除按钮，鼠标从点移到框上会经过一段空隙：立刻收起的话按钮
 * 根本点不到。延时收起，鼠标进了框就取消。
 */
let hideTimer: number | null = null
/** 待确认的删除：分组 id。空串表示没有待确认的删除。 */
const pendingDelete = ref('')

/** 树索引：当前路径、父子索引、根节点与分叉点。 */
const index = computed(() => buildTreeIndex(props.tree, props.headId))
/**
 * 折叠后的树：没有分叉的地方并成一个点。
 *
 * 没有分叉的那一段是同一条线，逐轮画点没有信息量，记一个点就够。
 */
const groups = computed(() => collapseGroups(props.tree, props.headId))
/** 树形图：点的位置与连线。宽屏从左往右排，窄屏从上往下排。 */
const layout = computed(() =>
  layoutTree(groups.value, props.headId, {
    direction: wide.value ? 'horizontal' : 'vertical',
  }),
)
const hovered = computed(() => {
  if (hoverId.value === null) return null
  return layout.value.nodes.find((node) => node.id === hoverId.value) ?? null
})
/** 悬停那一组含多少轮 */
const hoveredRounds = computed(() => {
  if (hoverId.value === null) return 0
  return groups.value.find((group) => group.id === hoverId.value)?.rounds ?? 0
})
/** 悬停的那一组：删这一段时按它的成员 id 发删除请求。 */
const hoveredGroup = computed(() => {
  if (hoverId.value === null) return null
  return groups.value.find((group) => group.id === hoverId.value) ?? null
})

/** 取消收起：鼠标进了提示框。 */
function keepVisible(): void {
  if (hideTimer !== null) {
    window.clearTimeout(hideTimer)
    hideTimer = null
  }
}

/** 延时收起提示：鼠标离开点或提示框。 */
function scheduleHide(): void {
  if (hideTimer !== null) window.clearTimeout(hideTimer)
  hideTimer = window.setTimeout(() => {
    hoverId.value = null
    hideTimer = null
  }, 180)
}

/**
 * 删掉这一段：别的支线照旧留着。
 *
 * 不用原生确认框：它会挡住整个页面，在内嵌浏览器里还会被直接确认掉。
 * 这里点一下变成确认态，再点确认才真删。
 */
async function onDeleteGroup(group: GroupNode): Promise<void> {
  pendingDelete.value = ''
  await chat.deleteBranch(group.members)
  keepVisible()
}

function onEnter(event: Event, id: string): void {
  keepVisible()
  hoverId.value = id
  const element = event.currentTarget as HTMLElement | null
  if (!element) return
  const rect = element.getBoundingClientRect()
  const width = 250
  const room = window.innerWidth - rect.right - 10
  const left = room >= width ? rect.right + 10 : Math.max(8, rect.left - width - 10)
  tipStyle.value = {
    left: Math.round(left) + 'px',
    top: Math.round(Math.max(8, rect.top - 8)) + 'px',
  }
}

function onPick(id: string): void {
  if (id === props.headId) return
  void chat.switchBranch(id)
}
</script>

<template>
  <section class="tree" aria-label="分支树">
    <button class="head" @click="collapsed = !collapsed">
      <component :is="collapsed ? ChevronRight : ChevronDown" :size="13" />
      <GitBranch :size="13" />
      <span class="title">分支树</span>
      <span class="count">
        {{ groups.length }} 个分点<template v-if="index.forkCount > 0"> · {{ index.forkCount }} 处分叉</template>
      </span>
    </button>
    <div v-if="!collapsed" class="body">
      <p v-if="layout.nodes.length === 0" class="empty">还没有分支。</p>
      <div
        class="graph"
        :style="{ width: layout.width + 'px', height: layout.height + 'px' }"
      >
        <svg
          class="edges"
          :width="layout.width"
          :height="layout.height"
          aria-hidden="true"
        >
          <line
            v-for="edge in layout.edges"
            :key="edge.key"
            :x1="edge.x1"
            :y1="edge.y1"
            :x2="edge.x2"
            :y2="edge.y2"
            :class="{ current: edge.current }"
          />
        </svg>
        <button
          v-for="node in layout.nodes"
          :key="node.id"
          class="node"
          :class="{ current: node.current, head: node.head, root: node.root }"
          :style="{ left: node.x + 'px', top: node.y + 'px' }"
          :aria-label="node.root ? '对话起点' : node.summary"
          @mouseenter="onEnter($event, node.id)"
          @mouseleave="scheduleHide"
          @focus="onEnter($event, node.id)"
          @blur="scheduleHide"
          @click="onPick(node.id)"
        >
          <GitFork v-if="node.fork" class="fork" :size="13" />
          <span v-else class="dot" />
        </button>
        <div
          v-if="hovered"
          class="tip"
          :style="tipStyle"
          @mouseenter="keepVisible"
          @mouseleave="scheduleHide"
        >
          <div class="tip-head">
            {{ hovered.root ? '对话起点' : hovered.role === 'user' ? '我' : '角色' }}
            <template v-if="hoveredRounds > 1"> · 这一段 {{ hoveredRounds }} 轮</template>
          </div>
          <p class="tip-body">{{ hovered.content || hovered.summary || '（空）' }}</p>
          <template v-if="hoveredGroup">
            <div v-if="pendingDelete === hoveredGroup.id" class="tip-confirm">
              <span class="tip-confirm-text">确认删除这一段？</span>
              <button class="tip-del danger" @click="onDeleteGroup(hoveredGroup)">确认</button>
              <button class="tip-del" @click="pendingDelete = ''">取消</button>
            </div>
            <button v-else class="tip-del" @click="pendingDelete = hoveredGroup.id">
              <Trash2 :size="12" />
              删除这一段
            </button>
          </template>
        </div>
      </div>
    </div>
  </section>
</template>

<style scoped>
.tree {
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  background: var(--bg-1);
  overflow: hidden;
}
.head {
  display: flex;
  align-items: center;
  gap: 6px;
  width: 100%;
  padding: 8px 10px;
  color: var(--text-2);
  font-size: 12px;
}
.title {
  color: var(--text-1);
}
.count {
  margin-left: auto;
  color: var(--text-3);
  font-size: 11px;
}
.body {
  max-height: 280px;
  overflow: auto;
  padding: 6px;
}
.graph {
  position: relative;
}
.edges {
  position: absolute;
  left: 0;
  top: 0;
  pointer-events: none;
}
.edges line {
  stroke: var(--border);
  stroke-width: 1;
}
.edges line.current {
  stroke: var(--accent);
  opacity: 0.55;
}
.node {
  position: absolute;
  display: flex;
  align-items: center;
  justify-content: center;
  width: 18px;
  height: 18px;
  padding: 0;
  border: none;
  background: transparent;
  transform: translate(-50%, -50%);
  cursor: pointer;
}
.dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--text-3);
}
.node:hover .dot {
  background: var(--text-1);
}
.node.current .dot {
  background: var(--accent);
}
.node.root .dot {
  background: transparent;
  border: 1px solid var(--text-3);
}
.node.head .dot {
  box-shadow: 0 0 0 2px var(--accent-soft);
}
/* 分叉点用叉号表示 */
.fork {
  color: var(--text-3);
  transform: rotate(180deg);
}
.node:hover .fork {
  color: var(--text-1);
}
.node.current .fork {
  color: var(--accent);
}
.tip {
  position: fixed;
  z-index: 20;
  width: 250px;
  max-height: 260px;
  overflow: hidden;
  padding: 8px 10px;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  background: var(--bg-2);
  box-shadow: 0 12px 32px #00000073;
  color: var(--text-1);
  font-size: 12px;
  line-height: 1.6;
}
.tip-head {
  margin-bottom: 4px;
  color: var(--text-3);
  font-size: 11px;
}
.tip-body {
  margin: 0;
  /* 正文自己滚，删除按钮留在下面始终看得见 */
  max-height: 150px;
  overflow-y: auto;
  white-space: pre-wrap;
  word-break: break-word;
}
.tip-del {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  margin-top: 6px;
  padding: 3px 8px;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  background: var(--bg-1);
  color: var(--text-3);
  font-size: 11px;
}
.tip-del:hover {
  border-color: var(--level-danger);
  color: var(--level-danger);
}
.tip-confirm {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-top: 6px;
}
.tip-confirm-text {
  color: var(--level-danger);
  font-size: 11px;
}
.empty {
  margin: 0;
  padding: 4px 8px 8px;
  color: var(--text-3);
  font-size: 12px;
}
</style>
