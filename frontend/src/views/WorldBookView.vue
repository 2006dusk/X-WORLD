<script setup lang="ts">
// 世界书视图：世界书清单、条目表、条目编辑与匹配测试。
// 命中判定、三层排序与递归均由后端负责，前端只展示匹配结果。
import { onMounted, ref } from 'vue'
import WorldBookList from '../components/worldbook/WorldBookList.vue'
import WorldEntryTable from '../components/worldbook/WorldEntryTable.vue'
import WorldEntryEditor from '../components/worldbook/WorldEntryEditor.vue'
import WorldMatchPanel from '../components/worldbook/WorldMatchPanel.vue'
import {
  createWorldBook,
  createWorldEntry,
  deleteWorldBook,
  deleteWorldEntry,
  fetchWorldBook,
  fetchWorldBooks,
  matchWorldBook,
  updateWorldBook,
  updateWorldEntry,
  type WorldBook,
  type WorldBookSummary,
  type WorldEntry,
  type WorldEntryInput,
  type WorldMatchResponse,
} from '../api/worldbooks'

const books = ref<WorldBookSummary[]>([])
const current = ref<WorldBook | null>(null)
const selectedId = ref('')
const busyId = ref('')
const loading = ref(false)
const busy = ref(false)
const notice = ref('')
const errorText = ref('')

const editing = ref<WorldEntry | null>(null)
const editorOpen = ref(false)
const editorError = ref('')

const matchResult = ref<WorldMatchResponse | null>(null)
const matchError = ref('')
const hasRun = ref(false)

function fail(err: unknown): void {
  errorText.value = err instanceof Error ? err.message : String(err)
}

async function load(): Promise<void> {
  loading.value = true
  errorText.value = ''
  try {
    const res = await fetchWorldBooks()
    books.value = res.items
    if (!selectedId.value && res.items.length > 0) await open(res.items[0].id)
  } catch (err) {
    fail(err)
  } finally {
    loading.value = false
  }
}

async function open(id: string): Promise<void> {
  busy.value = true
  errorText.value = ''
  try {
    current.value = await fetchWorldBook(id)
    selectedId.value = id
    matchResult.value = null
    hasRun.value = false
  } catch (err) {
    fail(err)
  } finally {
    busy.value = false
  }
}

async function onCreate(name: string): Promise<void> {
  busy.value = true
  errorText.value = ''
  try {
    const book = await createWorldBook({ name, enabled: true, entries: [] })
    notice.value = `已创建世界书 ${book.name}`
    await load()
    if (book.id) await open(book.id)
  } catch (err) {
    fail(err)
  } finally {
    busy.value = false
  }
}

async function onRename(book: WorldBookSummary, name: string): Promise<void> {
  busyId.value = book.id
  errorText.value = ''
  try {
    const entries = current.value && current.value.id === book.id ? current.value.entries : undefined
    await updateWorldBook(book.id, { name, enabled: book.enabled, entries })
    notice.value = '已重命名'
    await load()
  } catch (err) {
    fail(err)
  } finally {
    busyId.value = ''
  }
}

async function onToggle(book: WorldBookSummary): Promise<void> {
  busyId.value = book.id
  errorText.value = ''
  try {
    const entries = current.value && current.value.id === book.id ? current.value.entries : undefined
    await updateWorldBook(book.id, { name: book.name, enabled: !book.enabled, entries })
    notice.value = book.enabled ? '已停用' : '已启用'
    await load()
  } catch (err) {
    fail(err)
  } finally {
    busyId.value = ''
  }
}

async function onRemoveBook(book: WorldBookSummary): Promise<void> {
  busyId.value = book.id
  errorText.value = ''
  try {
    await deleteWorldBook(book.id)
    notice.value = '世界书已删除'
    if (selectedId.value === book.id) {
      current.value = null
      selectedId.value = ''
    }
    await load()
  } catch (err) {
    fail(err)
  } finally {
    busyId.value = ''
  }
}

function onEditEntry(entry: WorldEntry): void {
  editing.value = entry
  editorError.value = ''
  editorOpen.value = true
}

function onCreateEntry(): void {
  editing.value = null
  editorError.value = ''
  editorOpen.value = true
}

/** 新建区域：世界里的地点，新建对话时按它选地方。 */
function onCreateRegion(): void {
  editing.value = {
    id: '',
    kind: 'region',
    name: '',
    keys: [],
    content: '',
    priority: 0,
    position: 0,
    constant: false,
    selective: false,
    recursion: 0,
  }
  editorError.value = ''
  editorOpen.value = true
}

async function onSaveEntry(input: WorldEntryInput): Promise<void> {
  if (!current.value) return
  busy.value = true
  editorError.value = ''
  try {
    if (input.id) {
      await updateWorldEntry(current.value.id, input.id, input)
    } else {
      await createWorldEntry(current.value.id, input)
    }
    editorOpen.value = false
    notice.value = '条目已保存'
    await open(current.value.id)
  } catch (err) {
    editorError.value = err instanceof Error ? err.message : String(err)
  } finally {
    busy.value = false
  }
}

async function onRemoveEntry(entry: WorldEntry): Promise<void> {
  if (!current.value) return
  busy.value = true
  errorText.value = ''
  try {
    await deleteWorldEntry(current.value.id, entry.id)
    notice.value = '条目已删除'
    await open(current.value.id)
  } catch (err) {
    fail(err)
  } finally {
    busy.value = false
  }
}

async function onMatch(text: string, secondaryText: string): Promise<void> {
  if (!current.value) return
  busy.value = true
  matchError.value = ''
  try {
    matchResult.value = await matchWorldBook(current.value.id, {
      text,
      secondary_text: secondaryText,
    })
    hasRun.value = true
  } catch (err) {
    matchError.value = err instanceof Error ? err.message : String(err)
  } finally {
    busy.value = false
  }
}

onMounted(() => {
  void load()
})
</script>

<template>
  <div class="worldbooks">
    <p v-if="errorText" class="error">{{ errorText }}</p>
    <p v-if="notice" class="notice">{{ notice }}</p>

    <div class="body">
      <aside class="side">
        <WorldBookList
          :books="books"
          :selected-id="selectedId"
          :busy-id="busyId"
          :loading="loading"
          @select="open"
          @create="onCreate"
          @rename="onRename"
          @remove="onRemoveBook"
          @toggle="onToggle"
        />
      </aside>

      <section class="detail">
        <template v-if="current">
          <WorldEntryTable
            :entries="current.entries"
            :loading="loading"
            :busy="busy"
            @create="onCreateEntry"
      @create-region="onCreateRegion"
            @edit="onEditEntry"
            @remove="onRemoveEntry"
          />
          <WorldMatchPanel
            :busy="busy"
            :result="matchResult"
            :error="matchError"
            :has-run="hasRun"
            @run="onMatch"
          />
        </template>
        <p v-else class="meta">左侧选择或新建世界书。</p>
      </section>
    </div>

    <WorldEntryEditor
      v-if="editorOpen"
      :entry="editing"
      :busy="busy"
      :error="editorError"
      @close="editorOpen = false"
      @save="onSaveEntry"
    />
  </div>
</template>

<style scoped>
.worldbooks {
  display: flex;
  flex-direction: column;
  gap: 10px;
  height: 100%;
  min-height: 0;
}
.body {
  display: grid;
  grid-template-columns: 280px 1fr;
  gap: 10px;
  min-height: 0;
  flex: 1;
}
.side {
  min-height: 0;
  min-width: 0;
  display: flex;
}
.side > * {
  flex: 1;
  min-height: 0;
}
.detail {
  min-height: 0;
  min-width: 0;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.meta {
  color: var(--text-3);
  font-size: 12px;
  text-align: center;
  margin-top: 24px;
}
.error {
  margin: 0;
  padding: 6px 10px;
  border-radius: var(--radius);
  background: var(--danger-soft);
  color: var(--level-danger);
  font-size: 12px;
}
.notice {
  margin: 0;
  padding: 6px 10px;
  border-radius: var(--radius);
  background: var(--ok-soft);
  color: var(--level-ok);
  font-size: 12px;
}
/* 窄屏：两栏改上下叠放。清单与详情各占一段高度，条目表自己横向滚动。
 * 详情分得多一些：条目表要留出能读的高度，匹配测试在详情里继续往下滚。 */
@media (max-width: 768px) {
  .body {
    grid-template-columns: 1fr;
    grid-template-rows: minmax(0, 2fr) minmax(0, 5fr);
  }
}
</style>
