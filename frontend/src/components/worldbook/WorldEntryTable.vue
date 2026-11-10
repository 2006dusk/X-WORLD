<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { ChevronLeft, ChevronRight, Pencil, Plus, Trash2 } from 'lucide-vue-next'
import type { WorldEntry } from '../../api/worldbooks'

const PAGE_SIZE = 50
const props = defineProps<{
  entries: WorldEntry[]
  loading: boolean
  busy: boolean
}>()
const emit = defineEmits<{
  create: []
  createRegion: []
  edit: [entry: WorldEntry]
  remove: [entry: WorldEntry]
}>()

const page = ref(1)
const deleteId = ref('')
const pageCount = computed(() => Math.max(1, Math.ceil(props.entries.length / PAGE_SIZE)))
const visibleEntries = computed(() =>
  props.entries.slice((page.value - 1) * PAGE_SIZE, page.value * PAGE_SIZE),
)
const rangeStart = computed(() => props.entries.length === 0 ? 0 : (page.value - 1) * PAGE_SIZE + 1)
const rangeEnd = computed(() => Math.min(page.value * PAGE_SIZE, props.entries.length))

watch(() => props.entries.length, () => {
  if (page.value > pageCount.value) page.value = pageCount.value
})

function keys(entry: WorldEntry, secondary = false): string {
  const value = secondary ? entry.secondary_keys : entry.keys
  return Array.isArray(value) ? value.join(', ') : ''
}

function summary(content: string): string {
  const singleLine = content.replace(/\s+/g, ' ').trim()
  return singleLine.length > 120 ? `${singleLine.slice(0, 120)}…` : singleLine
}

function goTo(value: number): void {
  page.value = Math.max(1, Math.min(pageCount.value, value))
}

function confirmDelete(): void {
  const target = props.entries.find((item) => item.id === deleteId.value)
  deleteId.value = ''
  if (target) emit('remove', target)
}
</script>

<template>
  <section class="entry-table" aria-label="世界书条目">
    <header class="table-head">
      <div><h3>条目</h3><span>共 {{ entries.length }} 条 · 每页 50 条</span></div>
      <div class="head-actions">
        <button class="new-entry" :disabled="busy" @click="emit('createRegion')"><Plus :size="14" /> 新建区域</button>
        <button class="new-entry" :disabled="busy" @click="emit('create')"><Plus :size="14" /> 新建条目</button>
      </div>
    </header>

    <div class="table-scroll">
      <table>
        <thead><tr>
          <th class="number">#</th><th>关键词</th><th class="priority">优先级</th><th class="position">位置</th><th>常驻</th><th>二级关键词</th><th>正则</th><th>大小写</th><th class="content">内容摘要</th><th class="actions">操作</th>
        </tr></thead>
        <tbody>
          <tr v-for="(entry, index) in visibleEntries" :key="entry.id">
            <td class="number">{{ rangeStart + index }}</td>
            <td class="keys" :title="keys(entry)"><span v-if="entry.selective" class="selective" title="需要二级关键词">二</span>{{ keys(entry) || '—' }}</td>
            <td class="priority mono">{{ entry.priority }}</td>
            <td class="position mono" :title="entry.position === 0 ? '角色定义之前' : entry.position === 1 ? '角色定义之后' : '自定义位置'">{{ entry.position }}</td>
            <td><span class="flag" :class="{ on: entry.constant }">{{ entry.constant ? '是' : '否' }}</span></td>
            <td class="keys secondary-keys" :title="keys(entry, true)">{{ keys(entry, true) || '—' }}</td>
            <td><span class="flag" :class="{ on: entry.match_regex }">{{ entry.match_regex ? '是' : '否' }}</span></td>
            <td><span class="flag" :class="{ on: entry.case_sensitive }">{{ entry.case_sensitive ? '敏感' : '忽略' }}</span></td>
            <td class="summary" :title="entry.content"><span v-if="entry.card_ref" class="ref-badge" :title="`引用卡片 ${entry.card_ref.card_id}`">卡</span>{{ summary(entry.content) || '（空内容）' }}</td>
            <td class="actions"><div><button title="编辑条目" :aria-label="`编辑条目${entry.id}`" :disabled="busy" @click="emit('edit', entry)"><Pencil :size="13" /></button><button class="delete" title="删除条目" :aria-label="`删除条目${entry.id}`" :disabled="busy" @click="deleteId = entry.id"><Trash2 :size="13" /></button></div></td>
          </tr>
        </tbody>
      </table>
      <p v-if="loading" class="empty">正在读取条目…</p>
      <div v-else-if="entries.length === 0" class="empty"><strong>这本世界书还没有条目</strong><span>新建条目，填写关键词与要注入的设定内容。</span></div>
    </div>

    <footer v-if="entries.length > 0" class="pagination">
      <span>{{ rangeStart }}–{{ rangeEnd }} / {{ entries.length }}</span>
      <div><button title="上一页" aria-label="上一页条目" :disabled="page <= 1" @click="goTo(page - 1)"><ChevronLeft :size="14" /></button><label>第 <input type="number" min="1" :max="pageCount" :value="page" aria-label="条目页码" @change="goTo(Number(($event.target as HTMLInputElement).value))" /> / {{ pageCount }} 页</label><button title="下一页" aria-label="下一页条目" :disabled="page >= pageCount" @click="goTo(page + 1)"><ChevronRight :size="14" /></button></div>
    </footer>

    <div v-if="deleteId" class="delete-backdrop" role="presentation" @click.self="deleteId = ''">
      <div class="delete-dialog" role="alertdialog" aria-modal="true" aria-label="确认删除世界书条目">
        <Trash2 :size="20" />
        <h4>删除这个条目？</h4>
        <p>条目 {{ deleteId }} 将从当前世界书永久移除，此操作无法撤销。</p>
        <div><button @click="deleteId = ''">取消</button><button class="danger" :disabled="busy" @click="confirmDelete">确认删除</button></div>
      </div>
    </div>
  </section>
</template>

<style scoped>
.entry-table { position: relative; min-width: 0; min-height: 0; display: flex; flex: 1; flex-direction: column; border: 1px solid var(--border); border-radius: var(--radius-lg); background: var(--bg-1); overflow: hidden; }
.table-head { display: flex; align-items: center; justify-content: space-between; padding: 7px 9px; border-bottom: 1px solid var(--border); background: var(--bg-2); }
.table-head > div { display: flex; align-items: baseline; gap: 8px; }
h3 { margin: 0; font-size: 13px; }
.table-head span { color: var(--text-3); font-size: 10px; }
.new-entry { display: inline-flex; align-items: center; gap: 4px; padding: 4px 8px; border: 1px solid var(--accent); border-radius: var(--radius); color: var(--accent); font-size: 11px; }
.new-entry:hover { background: var(--accent-soft); }
.table-scroll { flex: 1; min-height: 0; overflow: auto; }
table { width: 100%; min-width: 1000px; border-collapse: collapse; table-layout: fixed; font-size: 11px; }
th { position: sticky; top: 0; z-index: 1; height: 28px; padding: 4px 6px; border-bottom: 1px solid var(--border); background: var(--bg-2); color: var(--text-3); font-weight: 500; text-align: left; white-space: nowrap; }
td { height: 32px; padding: 4px 6px; border-bottom: 1px solid var(--border); color: var(--text-2); vertical-align: middle; }
tbody tr:hover { background: var(--bg-2); }
tbody tr:hover td { color: var(--text-1); }
th.number, td.number { width: 35px; color: var(--text-3); text-align: right; }
th.priority, td.priority { width: 53px; text-align: right; }
th.position, td.position { width: 45px; text-align: center; }
th:nth-child(5), td:nth-child(5), th:nth-child(7), td:nth-child(7) { width: 45px; text-align: center; }
th:nth-child(8), td:nth-child(8) { width: 52px; text-align: center; }
th.content { width: 240px; }
th.actions, td.actions { width: 64px; }
.mono { font-family: var(--font-mono); }
.keys { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.secondary-keys { color: var(--text-3); }
.selective { display: inline-block; margin-right: 4px; padding: 0 3px; border: 1px solid var(--accent); border-radius: var(--radius); color: var(--accent); font-size: 9px; }
.flag { color: var(--text-3); font-size: 10px; }
.flag.on { color: var(--level-ok); }
.summary { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.ref-badge { display: inline-flex; align-items: center; justify-content: center; min-width: 16px; height: 16px; margin-right: 5px; border-radius: 4px; background: var(--accent-soft); color: var(--accent); font-size: 10px; }
.head-actions { display: flex; gap: 6px; }
.actions > div { display: flex; gap: 3px; }
.actions button { display: inline-flex; align-items: center; justify-content: center; width: 24px; height: 23px; border-radius: var(--radius); color: var(--text-3); }
.actions button:hover { color: var(--accent); background: var(--accent-soft); }
.actions button.delete:hover { color: var(--level-danger); background: var(--danger-soft); }
.empty { display: flex; flex-direction: column; align-items: center; gap: 5px; margin: 0; padding: 30px 10px; color: var(--text-3); font-size: 12px; text-align: center; }
.empty strong { color: var(--text-1); }
.pagination { display: flex; align-items: center; justify-content: space-between; min-height: 34px; padding: 4px 8px; border-top: 1px solid var(--border); background: var(--bg-2); color: var(--text-3); font-size: 10px; }
.pagination > div { display: flex; align-items: center; gap: 6px; }
.pagination button { display: inline-flex; padding: 3px; border: 1px solid var(--border); border-radius: var(--radius); color: var(--text-2); }
.pagination button:hover:not(:disabled) { border-color: var(--accent); color: var(--accent); }
.pagination input { width: 42px; padding: 2px 4px; text-align: center; font: 10px var(--font-mono); }
.delete-backdrop { position: absolute; inset: 0; z-index: 5; display: grid; place-items: center; padding: 15px; background: var(--bg-0); }
.delete-dialog { width: min(360px, 100%); padding: 16px; border: 1px solid var(--level-danger); border-radius: var(--radius-lg); background: var(--bg-1); color: var(--level-danger); }
.delete-dialog h4 { margin: 7px 0 4px; color: var(--text-1); font-size: 14px; }
.delete-dialog p { margin: 0; color: var(--text-2); font-size: 12px; }
.delete-dialog > div { display: flex; justify-content: flex-end; gap: 6px; margin-top: 13px; }
.delete-dialog button { padding: 4px 9px; border: 1px solid var(--border); border-radius: var(--radius); color: var(--text-2); font-size: 11px; }
.delete-dialog button.danger { border-color: var(--level-danger); color: var(--level-danger); }
button:disabled { opacity: 0.45; cursor: not-allowed; }
/* 窄屏：行内按钮按触控尺寸放大，操作列相应加宽，表在滚动容器里横向滚。 */
@media (max-width: 768px) {
  /* 表身留出能读的高度，其余在详情栏里继续往下滚 */
  .entry-table { min-height: 240px; }
  .table-head { flex-wrap: wrap; row-gap: 6px; }
  .head-actions { flex-wrap: wrap; }
  th.actions, td.actions { width: 96px; }
  .actions button { width: 40px; height: 40px; }
  .pagination button { width: 40px; height: 40px; }
}
</style>
