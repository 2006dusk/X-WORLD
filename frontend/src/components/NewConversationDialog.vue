<!-- 新建对话向导：先定世界，再定区域，最后定对话对象。
     世界书是一个世界，里面可以有多个区域条目；区域是世界的地点，所有人
     共用同一个世界。选中的区域会随这段对话注入。一个人的对话始终是一条
     上下文：再选同一个人就是接着上次继续，不新建第二条。 -->
<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { X } from 'lucide-vue-next'
import { fetchCards, type CardSummary } from '../api/cards'
import { fetchWorldBooks, fetchWorldRegions, type WorldBookSummary, type WorldRegion } from '../api/worldbooks'
import { createSession } from '../api/sessions'

const emit = defineEmits<{
  close: []
  created: [sessionId: string]
}>()

const books = ref<WorldBookSummary[]>([])
const regions = ref<WorldRegion[]>([])
const cards = ref<CardSummary[]>([])
const pickedBook = ref('')
const pickedRegion = ref('')
const pickedCard = ref('')
const busy = ref(false)
const loading = ref(true)
const errorText = ref('')

const bookName = computed(
  () => books.value.find((book) => book.id === pickedBook.value)?.name ?? '',
)
const regionName = computed(
  () => regions.value.find((region) => region.id === pickedRegion.value)?.name ?? '',
)

async function loadBooks(): Promise<void> {
  loading.value = true
  errorText.value = ''
  try {
    const res = await fetchWorldBooks()
    books.value = res.items
    if (res.items.length > 0) await pickBook(res.items[0].id)
  } catch (err) {
    errorText.value = err instanceof Error ? err.message : String(err)
  } finally {
    loading.value = false
  }
}

/** 选世界后拉出这个世界里的区域。 */
async function pickBook(id: string): Promise<void> {
  pickedBook.value = id
  pickedRegion.value = ''
  regions.value = []
  try {
    const res = await fetchWorldRegions(id)
    regions.value = res.items
    if (res.items.length > 0) pickedRegion.value = res.items[0].id
  } catch {
    regions.value = []
  }
}

async function loadCards(): Promise<void> {
  try {
    const res = await fetchCards()
    cards.value = res.items
  } catch {
    cards.value = []
  }
}

function pickRegion(id: string): void {
  pickedRegion.value = pickedRegion.value === id ? '' : id
}

async function onCreate(): Promise<void> {
  busy.value = true
  errorText.value = ''
  try {
    const session = await createSession(
      pickedCard.value,
      undefined,
      pickedBook.value ? [pickedBook.value] : [],
      pickedRegion.value,
    )
    emit('created', session.id)
  } catch (err) {
    errorText.value = err instanceof Error ? err.message : String(err)
  } finally {
    busy.value = false
  }
}

watch(pickedBook, () => undefined)

onMounted(() => {
  void loadBooks()
  void loadCards()
})
</script>

<template>
  <div class="overlay" @click.self="emit('close')">
    <div class="dialog" role="dialog" aria-label="新建对话">
      <header class="head">
        <h3 class="title">新建对话</h3>
        <button class="icon" title="关闭" @click="emit('close')">
          <X :size="15" />
        </button>
      </header>

      <p v-if="errorText" class="error">{{ errorText }}</p>

      <section class="section">
        <h4 class="section-title">
          <span class="step">1</span>
          选世界
        </h4>
        <p class="hint">世界书就是世界。所有人共用同一个世界，各自的记忆各自记。</p>
        <div v-if="loading" class="meta">读取世界书…</div>
        <ul v-else-if="books.length > 0" class="opts">
          <li v-for="book in books" :key="book.id">
            <button
              class="opt"
              :class="{ on: pickedBook === book.id }"
              @click="pickBook(book.id)"
            >
              <span class="box">{{ pickedBook === book.id ? '✓' : '' }}</span>
              <span class="opt-name">{{ book.name }}</span>
              <span class="opt-id mono">{{ book.id }}</span>
            </button>
          </li>
        </ul>
        <div v-else class="meta">还没有世界书。可以先建一个，或稍后在会话列表里补。</div>
      </section>

      <section class="section">
        <h4 class="section-title">
          <span class="step">2</span>
          选区域
        </h4>
        <p class="hint">世界里的地点。这段对话从这里开始，区域设定会随每轮注入。</p>
        <div v-if="loading" class="meta">读取世界…</div>
        <ul v-else-if="regions.length > 0" class="opts">
          <li v-for="region in regions" :key="region.id">
            <button
              class="opt"
              :class="{ on: pickedRegion === region.id }"
              @click="pickRegion(region.id)"
            >
              <span class="box">{{ pickedRegion === region.id ? '✓' : '' }}</span>
              <span class="opt-name">{{ region.name }}</span>
              <span class="opt-id mono">{{ region.id }}</span>
            </button>
          </li>
        </ul>
        <div v-else-if="bookName" class="meta">这个世界里还没有区域，去世界书加一个。</div>
        <div v-else class="meta">先选一个世界。</div>
      </section>

      <section class="section">
        <h4 class="section-title">
          <span class="step">3</span>
          选对话对象
        </h4>
        <p class="hint">一个人的对话始终是一条上下文：再选同一个人就是接着上次继续。</p>
        <ul v-if="cards.length > 0" class="opts">
          <li v-for="card in cards" :key="card.id">
            <button
              class="opt"
              :class="{ on: pickedCard === card.id }"
              @click="pickedCard = pickedCard === card.id ? '' : card.id"
            >
              <span class="box">{{ pickedCard === card.id ? '✓' : '' }}</span>
              <span class="opt-name">{{ card.name }}</span>
              <span class="opt-id mono">{{ card.id }}</span>
            </button>
          </li>
        </ul>
        <div v-else class="meta">卡片库里还没有角色卡。</div>
      </section>

      <footer class="foot">
        <span class="summary">
          <template v-if="bookName">
            {{ bookName }}<template v-if="regionName"> · {{ regionName }}</template>
          </template>
          <template v-else>未选世界</template>
        </span>
        <div class="actions">
          <button class="btn" :disabled="busy" @click="emit('close')">取消</button>
          <button class="btn primary" :disabled="busy || loading" @click="onCreate">
            开始对话
          </button>
        </div>
      </footer>
    </div>
  </div>
</template>

<style scoped>
.overlay {
  position: fixed;
  inset: 0;
  z-index: 40;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 12px;
  background: rgba(6, 8, 12, 0.62);
}
.dialog {
  display: flex;
  flex-direction: column;
  gap: 12px;
  width: min(460px, 100%);
  /* 按可视视口算高度：软键盘弹出时向导不被顶出屏幕 */
  max-height: min(84vh, calc(var(--app-h) - 24px));
  overflow-y: auto;
  padding: 14px 16px;
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  background: var(--bg-1);
}
.head {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.title {
  margin: 0;
  font-size: 14px;
  color: var(--text-1);
}
.icon {
  display: inline-flex;
  color: var(--text-3);
}
.icon:hover {
  color: var(--text-1);
}
.section {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.section-title {
  display: flex;
  align-items: center;
  gap: 7px;
  margin: 0;
  font-size: 12px;
  font-weight: 600;
  color: var(--text-2);
}
.step {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 17px;
  height: 17px;
  border-radius: 50%;
  background: var(--accent-soft);
  color: var(--accent);
  font-size: 11px;
}
.hint {
  margin: 0;
  color: var(--text-3);
  font-size: 11px;
}
.opts {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 3px;
  max-height: 130px;
  overflow-y: auto;
}
.opt {
  display: flex;
  align-items: baseline;
  gap: 8px;
  width: 100%;
  padding: 6px 8px;
  border-radius: var(--radius);
  text-align: left;
}
.opt:hover {
  background: var(--bg-2);
}
.opt.on {
  background: var(--accent-soft);
}
.box {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 15px;
  height: 15px;
  border: 1px solid var(--border);
  border-radius: 4px;
  color: var(--accent);
  font-size: 11px;
}
.opt-name {
  color: var(--text-1);
  font-size: 13px;
}
.opt-id {
  color: var(--text-3);
  font-size: 11px;
}
.meta {
  margin: 0;
  color: var(--text-3);
  font-size: 12px;
}
.error {
  margin: 0;
  padding: 6px 10px;
  border-radius: var(--radius);
  background: var(--danger-soft);
  color: var(--level-danger);
  font-size: 12px;
}
.foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  border-top: 1px solid var(--border);
  padding-top: 10px;
}
.summary {
  color: var(--text-3);
  font-size: 11px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.actions {
  display: flex;
  gap: 6px;
}
.btn {
  padding: 5px 12px;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  background: var(--bg-2);
  color: var(--text-2);
  font-size: 12px;
}
.btn:hover:not(:disabled) {
  color: var(--text-1);
}
.btn:disabled {
  opacity: 0.55;
}
.btn.primary {
  border-color: transparent;
  background: var(--accent);
  color: #ffffff;
}
/* 窄屏：底部一行放不下就换行，摘要占满一行。 */
@media (max-width: 768px) {
  .foot {
    flex-direction: column;
    align-items: stretch;
    gap: 8px;
  }
  .summary {
    white-space: normal;
  }
  .actions {
    justify-content: flex-end;
  }
}
</style>
