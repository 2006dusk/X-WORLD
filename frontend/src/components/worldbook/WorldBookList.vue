<script setup lang="ts">
import { ref } from 'vue'
import { BookOpen, Check, Pencil, Plus, Power, Trash2, X } from 'lucide-vue-next'
import type { WorldBookSummary } from '../../api/worldbooks'

defineProps<{
  books: WorldBookSummary[]
  selectedId: string
  busyId: string
  loading: boolean
}>()
const emit = defineEmits<{
  select: [id: string]
  create: [name: string]
  rename: [book: WorldBookSummary, name: string]
  remove: [book: WorldBookSummary]
  toggle: [book: WorldBookSummary]
}>()

const newName = ref('')
const renamingId = ref('')
const renameValue = ref('')
const deletingId = ref('')
const deletingValue = ref('')

function create(): void {
  const name = newName.value.trim()
  if (!name) return
  emit('create', name)
  newName.value = ''
}

function beginRename(book: WorldBookSummary): void {
  renamingId.value = book.id
  renameValue.value = book.name
  deletingId.value = ''
}

function submitRename(book: WorldBookSummary): void {
  const name = renameValue.value.trim()
  if (name && name !== book.name) emit('rename', book, name)
  renamingId.value = ''
}

function beginDelete(book: WorldBookSummary): void {
  deletingId.value = book.id
  deletingValue.value = book.name
  renamingId.value = ''
}

function submitDelete(book: WorldBookSummary): void {
  if (deletingValue.value.trim() !== book.name) return
  emit('remove', book)
}

function formatTime(value?: string): string {
  if (!value) return '未记录时间'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false,
  }).format(date)
}
</script>

<template>
  <aside class="world-list" aria-label="世界书列表">
    <div class="title-row">
      <h2><BookOpen :size="15" /> 世界书</h2>
      <span>{{ books.length }} 本</span>
    </div>
    <form class="create" @submit.prevent="create">
      <input v-model="newName" maxlength="200" placeholder="新世界书名称…" aria-label="新世界书名称" />
      <button class="add" type="submit" title="新建世界书" aria-label="新建世界书" :disabled="!newName.trim()"><Plus :size="16" /></button>
    </form>

    <div class="items">
      <p v-if="loading" class="state">正在读取世界书…</p>
      <template v-for="book in books" v-else :key="book.id">
        <div class="book-wrap" :class="{ selected: book.id === selectedId }">
          <form v-if="renamingId === book.id" class="rename" @submit.prevent="submitRename(book)">
            <input v-model="renameValue" maxlength="200" aria-label="重命名世界书" autofocus @keydown.esc="renamingId = ''" />
            <button type="submit" title="保存名称" aria-label="保存名称" :disabled="!renameValue.trim()"><Check :size="14" /></button>
            <button type="button" title="取消重命名" aria-label="取消重命名" @click="renamingId = ''"><X :size="14" /></button>
          </form>
          <div v-else class="book" :class="{ disabled: !book.enabled }">
            <button class="book-select" :aria-pressed="book.id === selectedId" @click="emit('select', book.id)">
              <span class="book-name" :title="book.name">{{ book.name }}</span>
              <span class="book-meta">{{ book.enabled ? '已启用' : '已停用' }} · {{ formatTime(book.updated_at) }}</span>
            </button>
            <div class="book-actions">
              <button :disabled="busyId === book.id" :title="book.enabled ? '停用世界书' : '启用世界书'" :aria-label="book.enabled ? `停用${book.name}` : `启用${book.name}`" :class="{ off: !book.enabled }" @click="emit('toggle', book)"><Power :size="13" /></button>
              <button :disabled="busyId === book.id" title="重命名" :aria-label="`重命名${book.name}`" @click="beginRename(book)"><Pencil :size="13" /></button>
              <button :disabled="busyId === book.id" class="remove" title="删除" :aria-label="`删除${book.name}`" @click="beginDelete(book)"><Trash2 :size="13" /></button>
            </div>
          </div>
        </div>
        <div v-if="deletingId === book.id" class="delete-confirm" role="alertdialog" :aria-label="`确认删除${book.name}`">
          <strong>删除「{{ book.name }}」？</strong>
          <p>世界书及其中全部条目将被永久删除。输入完整名称以确认：</p>
          <input v-model="deletingValue" :placeholder="book.name" :aria-label="`输入${book.name}以确认删除`" @keydown.esc="deletingId = ''" />
          <div><button @click="deletingId = ''">取消</button><button class="danger" :disabled="deletingValue !== book.name || busyId === book.id" @click="submitDelete(book)">永久删除</button></div>
        </div>
      </template>
      <div v-if="!loading && books.length === 0" class="empty">
        <BookOpen :size="22" />
        <strong>还没有世界书</strong>
        <p>在上方输入名称并新建，然后添加关键词与设定条目。</p>
      </div>
    </div>
  </aside>
</template>

<style scoped>
.world-list { min-height: 0; display: flex; flex-direction: column; border: 1px solid var(--border); border-radius: var(--radius-lg); background: var(--bg-1); overflow: hidden; }
.title-row { display: flex; align-items: center; justify-content: space-between; padding: 10px 10px 7px; }
h2 { display: flex; align-items: center; gap: 6px; margin: 0; font-size: 14px; }
.title-row > span { color: var(--text-3); font-size: 11px; }
.create { display: flex; gap: 5px; margin: 0 8px 8px; }
.create input { min-width: 0; flex: 1; padding: 5px 7px; font-size: 12px; }
.add { display: inline-flex; align-items: center; justify-content: center; width: 29px; border: 1px solid var(--accent); border-radius: var(--radius); color: var(--accent); }
.add:hover:not(:disabled) { background: var(--accent-soft); }
.items { flex: 1; min-height: 0; padding: 0 5px 6px; overflow-y: auto; }
.book-wrap { border: 1px solid transparent; border-radius: var(--radius); }
.book-wrap:hover { background: var(--bg-2); }
.book-wrap.selected { border-color: var(--accent); background: var(--accent-soft); }
.book { display: flex; align-items: center; gap: 3px; padding: 4px 4px 4px 7px; }
.book-select { display: flex; flex-direction: column; gap: 2px; min-width: 0; flex: 1; text-align: left; }
.book-name { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--text-1); font-size: 12px; }
.book-meta { color: var(--text-3); font-size: 10px; }
.book.disabled .book-name { color: var(--text-3); }
.book-actions { display: flex; gap: 2px; opacity: 0; }
.book-wrap:hover .book-actions, .book-wrap.selected .book-actions, .book-actions:focus-within { opacity: 1; }
.book-actions button, .rename button { display: inline-flex; align-items: center; justify-content: center; width: 23px; height: 23px; border-radius: var(--radius); color: var(--level-ok); }
.book-actions button:nth-child(2) { color: var(--text-3); }
.book-actions button.off { color: var(--text-3); }
.book-actions button.remove { color: var(--text-3); }
.book-actions button:hover { background: var(--bg-1); color: var(--accent); }
.book-actions button.remove:hover { color: var(--level-danger); background: var(--danger-soft); }
.rename { display: flex; align-items: center; gap: 3px; padding: 5px; }
.rename input { min-width: 0; flex: 1; padding: 3px 5px; font-size: 12px; }
.rename button { color: var(--text-2); }
.rename button:first-of-type { color: var(--accent); }
.delete-confirm { margin: 3px 3px 7px; padding: 8px; border: 1px solid var(--level-danger); border-radius: var(--radius); background: var(--danger-soft); }
.delete-confirm strong { color: var(--level-danger); font-size: 12px; }
.delete-confirm p { margin: 3px 0 6px; color: var(--text-2); font-size: 11px; }
.delete-confirm input { width: 100%; padding: 4px 6px; font-size: 11px; }
.delete-confirm > div { display: flex; justify-content: flex-end; gap: 5px; margin-top: 6px; }
.delete-confirm button { padding: 3px 7px; border: 1px solid var(--border); border-radius: var(--radius); color: var(--text-2); font-size: 11px; }
.delete-confirm button.danger { border-color: var(--level-danger); color: var(--level-danger); }
.state, .empty { color: var(--text-3); font-size: 12px; }
.empty { display: flex; flex-direction: column; align-items: center; gap: 6px; margin: 20px 5px; padding: 15px 8px; border: 1px dashed var(--border); border-radius: var(--radius); text-align: center; }
.empty strong { color: var(--text-1); }
.empty p { margin: 0; }
button:disabled { cursor: not-allowed; opacity: 0.45; }
@media (hover: none) { .book-actions { opacity: 1; } }
</style>
