<script setup lang="ts">
// 上下文窗口二级弹窗：占用率、进度条与分段占比。
// 数据来自装配回执的 context_report 与 usage.budget，计算在 lib/contextWindow.ts。
import { computed, ref } from 'vue'
import { ChevronDown, ChevronUp, TriangleAlert } from 'lucide-vue-next'
import { contextCategories } from '../lib/contextWindow'
import type { ContextUsage } from '../lib/contextWindow'
import type { ContextReport } from '../types/chat'

const props = defineProps<{
  usage: ContextUsage
  report: ContextReport | null
}>()

/** 收起时只留标题与占比，明细折叠。 */
const collapsed = ref(false)

const usedPct = computed(() => props.usage.pct)
const reservedPct = computed(() => {
  if (props.usage.limit <= 0) return 0
  return Math.min(100, (props.usage.reserved / props.usage.limit) * 100)
})
const rows = computed(() => contextCategories(props.report))
const totalTokens = computed(() => rows.value.reduce((sum, row) => sum + row.tokens, 0))

const usageText = computed(() => {
  const { used, reserved, limit } = props.usage
  if (reserved > 0) return '已用 ' + used + ' / 预留 ' + reserved + ' / 上限 ' + limit
  return '已用 ' + used + ' / 上限 ' + limit
})
</script>

<template>
  <section class="ctx" aria-label="上下文窗口">
    <header class="head">
      <span class="title">上下文窗口</span>
      <button
        class="toggle"
        :title="collapsed ? '展开明细' : '收起明细'"
        @click="collapsed = !collapsed"
      >
        <span class="pct">{{ Math.round(usedPct) }}%</span>
        <ChevronUp v-if="!collapsed" :size="14" />
        <ChevronDown v-else :size="14" />
      </button>
    </header>

    <template v-if="!collapsed">
      <div class="track" role="img" :aria-label="'上下文窗口占用 ' + Math.round(usedPct) + '%'">
        <div class="used" :style="{ width: usedPct + '%' }" />
        <div class="reserved" :style="{ width: reservedPct + '%', left: usedPct + '%' }" />
      </div>

      <p v-if="rows.length === 0" class="empty">还没有装配回执。发一条消息就能看到占用。</p>
      <ul v-else class="rows">
        <li v-for="row in rows" :key="row.label" class="row">
          <span class="chip" :class="'tone-' + row.tone" />
          <span class="name">{{ row.label }}</span>
          <span class="value">{{ row.pct.toFixed(1) }}%</span>
        </li>
      </ul>

      <footer class="foot">
        <span class="usage">{{ usageText }}</span>
        <span v-if="report?.truncated" class="warn">
          <TriangleAlert :size="12" />
          预算不足，已裁剪
        </span>
      </footer>
      <p v-if="totalTokens > 0" class="total">分段合计 {{ totalTokens }} token</p>
    </template>
  </section>
</template>

<style scoped>
.ctx {
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  background: var(--bg-1);
  padding: 10px 12px 12px;
}
.head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 8px;
}
.title {
  color: var(--text-2);
  font-size: 12px;
}
.toggle {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 0;
  border: none;
  background: transparent;
  color: var(--text-1);
  font-size: 12px;
  font-family: var(--font-mono);
  cursor: pointer;
}
.toggle:hover {
  color: var(--accent);
}
.track {
  position: relative;
  height: 6px;
  margin-bottom: 10px;
  background: var(--bg-0);
  border-radius: 3px;
  overflow: hidden;
}
.used {
  position: absolute;
  left: 0;
  top: 0;
  bottom: 0;
  background: var(--accent);
}
.reserved {
  position: absolute;
  top: 0;
  bottom: 0;
  background: var(--accent-soft);
  border-left: 1px solid var(--accent);
}
.rows {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.row {
  display: flex;
  align-items: center;
  gap: 8px;
}
.chip {
  width: 8px;
  height: 8px;
  flex: none;
  border-radius: 2px;
}
.chip.tone-0 {
  background: var(--ctx-1);
}
.chip.tone-1 {
  background: var(--ctx-2);
}
.chip.tone-2 {
  background: var(--ctx-3);
}
.chip.tone-3 {
  background: var(--ctx-4);
}
.chip.tone-4 {
  background: var(--ctx-5);
}
.chip.tone-5 {
  background: var(--ctx-6);
}
.chip.tone-6 {
  background: var(--ctx-7);
}
.name {
  flex: 1;
  color: var(--text-2);
  font-size: 12px;
}
.value {
  color: var(--text-1);
  font-size: 12px;
  font-family: var(--font-mono);
  text-align: right;
}
.empty {
  margin: 0 0 8px;
  color: var(--text-3);
  font-size: 12px;
}
.foot {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 10px;
  padding-top: 8px;
  border-top: 1px solid var(--border);
}
.usage {
  color: var(--text-3);
  font-size: 11px;
  font-family: var(--font-mono);
}
.warn {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  color: var(--level-warn);
  font-size: 11px;
  background: var(--warn-soft);
  padding: 1px 6px;
  border-radius: var(--radius);
}
.total {
  margin: 6px 0 0;
  color: var(--text-3);
  font-size: 11px;
}
</style>
