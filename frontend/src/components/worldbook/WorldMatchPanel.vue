<script setup lang="ts">
import { ref } from 'vue'
import { ChevronDown, ChevronRight, FlaskConical } from 'lucide-vue-next'
import type { WorldMatchResponse } from '../../api/worldbooks'

const props = defineProps<{
  busy: boolean
  result: WorldMatchResponse | null
  error: string
  hasRun: boolean
}>()
const emit = defineEmits<{
  run: [text: string, secondaryText: string]
}>()
const text = ref('')
const secondaryText = ref('')
const expanded = ref(true)
const localError = ref('')

function run(): void {
  if (!text.value.trim() && !secondaryText.value.trim()) {
    localError.value = '请输入正文或二级关键词文本；也可以仅测试常驻条目，填写任意正文即可。'
    return
  }
  localError.value = ''
  emit('run', text.value, secondaryText.value)
}
</script>

<template>
  <section class="match-panel" aria-label="世界书匹配测试">
    <button class="panel-title" :aria-expanded="expanded" @click="expanded = !expanded">
      <span><FlaskConical :size="14" /> 匹配测试</span>
      <span class="fold-label">{{ result ? `命中 ${result.entries.length} 条` : '由后端执行匹配与排序' }}<component :is="expanded ? ChevronDown : ChevronRight" :size="14" /></span>
    </button>
    <div v-if="expanded" class="panel-body">
      <div class="test-inputs">
        <label>正文<textarea v-model="text" rows="3" placeholder="输入要测试的对话正文，例如：深夜，我们抵达王都北门…" @input="localError = ''" /></label>
        <label>二级关键词文本<textarea v-model="secondaryText" rows="3" placeholder="输入用于 selective 条目二级匹配的文本…" @input="localError = ''" /></label>
        <button class="run" :disabled="busy" @click="run"><FlaskConical :size="14" /> {{ busy ? '匹配中…' : '运行匹配' }}</button>
      </div>
      <p v-if="localError || error" class="error" role="alert">{{ localError || error }}</p>
      <div v-if="result && result.entries.length" class="results">
        <div class="result-head"><span>命中顺序</span><span>条目与内容</span></div>
        <article v-for="(entry, index) in result.entries" :key="entry.id" class="hit">
          <div class="hit-meta"><span class="order">{{ index + 1 }}</span><span class="badges"><i v-if="entry.selective">二级</i><i v-else-if="entry.constant">常驻</i><i v-else>关键词</i><i>优先级 {{ entry.priority }}</i><i>位置 {{ entry.position }}</i></span></div>
          <div class="hit-content"><strong>{{ entry.comment || entry.id }}</strong><code>{{ entry.id }}</code><p>{{ entry.content || '（空内容）' }}</p></div>
        </article>
      </div>
      <div v-else-if="hasRun && !props.busy && !error" class="no-hit">没有命中条目。检查关键词、二级匹配条件、正则与大小写设置。</div>
      <p v-else class="guide">结果中的顺序就是后端返回的注入候选顺序；这里不自行计算匹配或优先级。</p>
    </div>
  </section>
</template>

<style scoped>
.match-panel { flex-shrink: 0; border: 1px solid var(--border); border-radius: var(--radius-lg); background: var(--bg-1); overflow: hidden; }
.panel-title { display: flex; align-items: center; justify-content: space-between; width: 100%; padding: 7px 9px; background: var(--bg-2); color: var(--text-2); }
.panel-title > span { display: inline-flex; align-items: center; gap: 6px; font-size: 12px; }
.panel-title > span:first-child { color: var(--text-1); font-weight: 500; }
.fold-label { color: var(--text-3); font-size: 10px !important; }
.panel-title:hover { color: var(--accent); }
.panel-body { padding: 8px; }
.test-inputs { display: grid; grid-template-columns: 1fr 1fr auto; gap: 7px; align-items: end; }
.test-inputs label { display: flex; flex-direction: column; gap: 4px; color: var(--text-2); font-size: 10px; }
.test-inputs textarea { width: 100%; resize: vertical; padding: 5px 7px; font-size: 11px; line-height: 1.45; }
.run { display: inline-flex; align-items: center; gap: 4px; min-height: 29px; padding: 4px 9px; border: 1px solid var(--accent); border-radius: var(--radius); color: var(--accent); font-size: 11px; }
.run:hover:not(:disabled) { background: var(--accent-soft); }
.error { margin: 7px 0 0; padding: 5px 7px; border: 1px solid var(--level-danger); border-radius: var(--radius); background: var(--danger-soft); color: var(--level-danger); font-size: 11px; }
.results { max-height: 280px; margin-top: 8px; border: 1px solid var(--border); border-radius: var(--radius); overflow-y: auto; }
.result-head { position: sticky; top: 0; z-index: 1; display: grid; grid-template-columns: 150px 1fr; padding: 4px 8px; background: var(--bg-2); color: var(--text-3); font-size: 10px; }
.hit { display: grid; grid-template-columns: 150px 1fr; padding: 7px 8px; border-top: 1px solid var(--border); }
.hit:hover { background: var(--bg-2); }
.hit-meta { display: flex; align-items: flex-start; gap: 7px; }
.order { display: inline-flex; align-items: center; justify-content: center; width: 20px; height: 20px; border: 1px solid var(--accent); border-radius: var(--radius); color: var(--accent); font: 11px var(--font-mono); }
.badges { display: flex; flex-wrap: wrap; gap: 3px; }
.badges i { padding: 1px 4px; border: 1px solid var(--border); border-radius: var(--radius); color: var(--text-3); font-size: 9px; font-style: normal; white-space: nowrap; }
.hit-content { min-width: 0; }
.hit-content strong { color: var(--text-1); font-size: 11px; font-weight: 500; }
.hit-content code { margin-left: 7px; color: var(--text-3); font: 9px var(--font-mono); }
.hit-content p { margin: 3px 0 0; color: var(--text-2); font-size: 11px; white-space: pre-wrap; overflow-wrap: anywhere; }
.no-hit, .guide { margin: 7px 0 0; color: var(--text-3); font-size: 11px; }
.no-hit { padding: 8px; border: 1px dashed var(--border); border-radius: var(--radius); text-align: center; }
button:disabled { opacity: 0.45; cursor: not-allowed; }
@media (max-width: 900px) { .test-inputs { grid-template-columns: 1fr 1fr; } .run { grid-column: 1 / -1; justify-content: center; } .result-head, .hit { grid-template-columns: 120px 1fr; } }
/* 窄屏：触控目标不小于 40px。 */
@media (max-width: 768px) { .run { min-height: 40px; } }
</style>
