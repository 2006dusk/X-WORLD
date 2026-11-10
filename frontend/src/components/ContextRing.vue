<script setup lang="ts">
// 上下文窗口圆环：显示上下文窗口的占用率。
// 只负责画，点它开二级弹窗的事交给父组件。
import { computed } from 'vue'

const props = withDefaults(
  defineProps<{
    /** 占用率，百分数，0 到 100 */
    pct: number
    /** 无障碍标签与悬停说明 */
    label?: string
  }>(),
  { label: '' },
)

const RADIUS = 6.5
const CIRC = 2 * Math.PI * RADIUS

const dash = computed(() => {
  const pct = Math.max(0, Math.min(100, Number(props.pct) || 0))
  return ((CIRC * pct) / 100).toFixed(2) + ' ' + CIRC.toFixed(2)
})

const text = computed(() => props.label || '上下文窗口 ' + Math.round(props.pct) + '%')
</script>

<template>
  <svg class="ring" viewBox="0 0 16 16" role="img" :aria-label="text">
    <circle class="track" cx="8" cy="8" :r="RADIUS" />
    <circle class="arc" cx="8" cy="8" :r="RADIUS" :stroke-dasharray="dash" />
  </svg>
</template>

<style scoped>
.ring {
  width: 16px;
  height: 16px;
  display: block;
}
.track {
  fill: none;
  stroke: var(--border);
  stroke-width: 2;
}
.arc {
  fill: none;
  stroke: var(--accent);
  stroke-width: 2;
  stroke-linecap: round;
  transform: rotate(-90deg);
  transform-origin: 50% 50%;
  transition: stroke-dasharray 0.2s ease;
}
</style>
