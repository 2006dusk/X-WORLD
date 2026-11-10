<script setup lang="ts">
// 记忆视图：条目清单、混合检索、统计、实体与遗忘扫描。
// 命中排序与分数由后端给出；前端只展示，不做二次排序或再打分。
import { onMounted, ref } from 'vue'
import { Archive, Search, Sparkles } from 'lucide-vue-next'
import {
  archiveMemory,
  extractMemories,
  fetchEntities,
  fetchMemories,
  fetchMemoryStats,
  forgetScan,
  saveEntity,
  searchMemories,
  type EntityItem,
  type MemoryHit,
  type MemoryItem,
  type MemoryStats,
} from '../api/memory'

const KINDS = ['', 'fact', 'relation', 'event']

const characterId = ref('')
const kind = ref('')
const searchTerm = ref('')
const sessionId = ref('')
const entityName = ref('')
const entityAttrs = ref('{}')

const items = ref<MemoryItem[]>([])
const hits = ref<MemoryHit[]>([])
const approximate = ref(false)
const stats = ref<MemoryStats | null>(null)
const entities = ref<EntityItem[]>([])
const forgettable = ref<MemoryItem[]>([])

const busy = ref(false)
const notice = ref('')
const errorText = ref('')

function fail(err: unknown): void {
  errorText.value = err instanceof Error ? err.message : String(err)
}

async function loadItems(): Promise<void> {
  busy.value = true
  errorText.value = ''
  try {
    const res = await fetchMemories({
      character_id: characterId.value.trim() || undefined,
      kind: kind.value || undefined,
    })
    items.value = res.items
  } catch (err) {
    fail(err)
  } finally {
    busy.value = false
  }
}

async function loadStats(): Promise<void> {
  try {
    stats.value = await fetchMemoryStats(characterId.value.trim() || undefined)
  } catch (err) {
    fail(err)
  }
}

async function loadEntities(): Promise<void> {
  try {
    const res = await fetchEntities(characterId.value.trim() || undefined)
    entities.value = res.items
  } catch (err) {
    fail(err)
  }
}

async function onSearch(): Promise<void> {
  const term = searchTerm.value.trim()
  if (!term) {
    errorText.value = '检索词不能为空'
    return
  }
  busy.value = true
  errorText.value = ''
  try {
    const res = await searchMemories({
      query: term,
      character_id: characterId.value.trim() || undefined,
      top_k: 12,
    })
    hits.value = res.items
    approximate.value = res.approximate
    notice.value = res.approximate
      ? '命中为近似结果：当前无向量文件，仅按关键词排序'
      : `命中 ${res.items.length} 条`
  } catch (err) {
    fail(err)
  } finally {
    busy.value = false
  }
}

async function onExtract(): Promise<void> {
  const session = sessionId.value.trim()
  if (!session) {
    errorText.value = '抽取需要会话 ID'
    return
  }
  busy.value = true
  errorText.value = ''
  try {
    const res = await extractMemories({
      session_id: session,
      character_id: characterId.value.trim() || undefined,
    })
    notice.value = `抽取得到 ${res.count} 条候选记忆`
    await loadItems()
    await loadStats()
  } catch (err) {
    fail(err)
  } finally {
    busy.value = false
  }
}

async function onForgetScan(): Promise<void> {
  busy.value = true
  errorText.value = ''
  try {
    const res = await forgetScan({ character_id: characterId.value.trim() || undefined })
    forgettable.value = res.items
    notice.value = `扫描出 ${res.count} 条可遗忘记忆`
  } catch (err) {
    fail(err)
  } finally {
    busy.value = false
  }
}

async function onArchive(item: MemoryItem): Promise<void> {
  busy.value = true
  errorText.value = ''
  try {
    await archiveMemory(item.id, item.character_id)
    notice.value = '记忆已归档'
    await loadItems()
    await loadStats()
  } catch (err) {
    fail(err)
  } finally {
    busy.value = false
  }
}

async function onSaveEntity(): Promise<void> {
  const name = entityName.value.trim()
  if (!name) {
    errorText.value = '实体名不能为空'
    return
  }
  let attributes: Record<string, unknown>
  try {
    const parsed: unknown = JSON.parse(entityAttrs.value || '{}')
    if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed)) {
      throw new Error('属性必须是 JSON 对象')
    }
    attributes = parsed as Record<string, unknown>
  } catch (err) {
    errorText.value = err instanceof Error ? err.message : String(err)
    return
  }
  busy.value = true
  errorText.value = ''
  try {
    await saveEntity(name, {
      character_id: characterId.value.trim() || undefined,
      attributes,
    })
    notice.value = '实体已保存'
    await loadEntities()
  } catch (err) {
    fail(err)
  } finally {
    busy.value = false
  }
}

onMounted(() => {
  void loadItems()
  void loadStats()
  void loadEntities()
})
</script>

<template>
  <div class="memory">
    <header class="toolbar">
      <h2 class="heading">记忆</h2>
      <input v-model="characterId" class="input" placeholder="角色卡 ID" />
      <select v-model="kind" class="input kind">
        <option v-for="k in KINDS" :key="k" :value="k">{{ k || '全部类型' }}</option>
      </select>
      <button class="btn" :disabled="busy" @click="loadItems">刷新条目</button>
    </header>

    <p v-if="errorText" class="error">{{ errorText }}</p>
    <p v-if="notice" class="notice">{{ notice }}</p>

    <div class="stats">
      <div class="stat">
        <span class="stat-value">{{ stats?.total ?? 0 }}</span>
        <span class="stat-label">总条目</span>
      </div>
      <div class="stat">
        <span class="stat-value">{{ stats?.active ?? 0 }}</span>
        <span class="stat-label">活跃</span>
      </div>
      <div class="stat">
        <span class="stat-value">{{ stats?.archived ?? 0 }}</span>
        <span class="stat-label">已归档</span>
      </div>
      <div class="stat">
        <span class="stat-value">
          {{ (stats?.average_importance ?? 0).toFixed(2) }}
        </span>
        <span class="stat-label">平均重要度</span>
      </div>
      <div class="stat kinds">
        <span class="stat-label">按类型</span>
        <span class="mono small">
          {{ Object.entries(stats?.by_kind ?? {}).map(([k, v]) => k + '=' + v).join('、') || '—' }}
        </span>
      </div>
    </div>

    <section class="block">
      <h3 class="title">检索</h3>
      <div class="row">
        <input v-model="searchTerm" class="input grow" placeholder="检索词" @keyup.enter="onSearch" />
        <button class="btn" :disabled="busy" @click="onSearch">
          <Search :size="13" />
          检索
        </button>
        <input v-model="sessionId" class="input" placeholder="会话 ID（抽取用）" />
        <button class="btn" :disabled="busy" @click="onExtract">
          <Sparkles :size="13" />
          抽取
        </button>
        <button class="btn" :disabled="busy" @click="onForgetScan">遗忘扫描</button>
      </div>
      <div v-if="hits.length" class="table-scroll">
      <table class="table">
        <thead>
          <tr>
            <th>ID</th>
            <th>类型</th>
            <th>得分</th>
            <th>BM25</th>
            <th>向量</th>
            <th>重要度</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="hit in hits" :key="hit.id">
            <td class="mono small">{{ hit.id }}</td>
            <td>{{ hit.kind }}</td>
            <td class="mono">{{ hit.score.toFixed(4) }}</td>
            <td class="mono">{{ hit.bm25.toFixed(4) }}</td>
            <td class="mono">{{ hit.vector.toFixed(4) }}</td>
            <td class="mono">{{ hit.importance.toFixed(2) }}</td>
          </tr>
        </tbody>
      </table>
      </div>
      <p v-else class="meta">输入检索词后查看混合检索命中。</p>
    </section>

    <section class="block">
      <h3 class="title">记忆条目</h3>
      <div class="table-scroll">
      <table class="table">
        <thead>
          <tr>
            <th>ID</th>
            <th>类型</th>
            <th>内容</th>
            <th>重要度</th>
            <th>状态</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="item in items" :key="item.id">
            <td class="mono small">{{ item.id }}</td>
            <td>{{ item.kind }}</td>
            <td class="content">{{ item.content }}</td>
            <td class="mono">{{ item.importance.toFixed(2) }}</td>
            <td>{{ item.archived ? '已归档' : '活跃' }}</td>
            <td>
              <button
                v-if="!item.archived"
                class="icon"
                title="归档"
                :disabled="busy"
                @click="onArchive(item)"
              >
                <Archive :size="13" />
              </button>
            </td>
          </tr>
          <tr v-if="items.length === 0">
            <td colspan="6" class="meta">暂无记忆条目。</td>
          </tr>
        </tbody>
      </table>
      </div>
    </section>

    <section class="block">
      <h3 class="title">实体状态</h3>
      <div class="row">
        <input v-model="entityName" class="input" placeholder="实体名" />
        <input v-model="entityAttrs" class="input grow mono" placeholder='{"属性":"值"}' />
        <button class="btn" :disabled="busy" @click="onSaveEntity">保存实体</button>
      </div>
      <div class="table-scroll">
      <table class="table">
        <thead>
          <tr>
            <th>名称</th>
            <th>作用域</th>
            <th>属性</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="entity in entities" :key="entity.name">
            <td>{{ entity.name }}</td>
            <td class="mono small">{{ entity.character_id }}</td>
            <td class="mono small">
              {{ Object.entries(entity.attributes ?? {}).map(([k, v]) => k + '=' + String(v)).join('、') || '—' }}
            </td>
          </tr>
          <tr v-if="entities.length === 0">
            <td colspan="3" class="meta">暂无实体。</td>
          </tr>
        </tbody>
      </table>
      </div>
    </section>

    <section v-if="forgettable.length" class="block">
      <h3 class="title">可遗忘记忆</h3>
      <ul class="forget">
        <li v-for="item in forgettable" :key="item.id">
          <span class="mono small">{{ item.id }}</span>
          <span>{{ item.content }}</span>
        </li>
      </ul>
    </section>
  </div>
</template>

<style scoped>
.memory {
  display: flex;
  flex-direction: column;
  gap: 10px;
  height: 100%;
  overflow-y: auto;
  min-height: 0;
}
.toolbar {
  display: flex;
  align-items: center;
  gap: 8px;
}
.heading {
  margin: 0;
  font-size: 13px;
  color: var(--text-2);
  text-transform: uppercase;
  letter-spacing: 0.05em;
}
.input {
  background: var(--bg-0);
  font-size: 12px;
}
.toolbar .input {
  max-width: 220px;
}
.kind {
  width: 110px;
}
.grow {
  flex: 1;
  min-width: 0;
}
.btn {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 5px 10px;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  background: var(--bg-1);
  color: var(--text-2);
  font-size: 12px;
  white-space: nowrap;
}
.btn:hover:not(:disabled) {
  background: var(--bg-2);
  color: var(--text-1);
}
.btn:disabled {
  opacity: 0.55;
}
.icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 24px;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  color: var(--text-3);
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
.stats {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(120px, 1fr));
  gap: 8px;
}
.stat {
  display: flex;
  flex-direction: column;
  gap: 2px;
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  background: var(--bg-1);
  padding: 8px 10px;
}
.stat-value {
  color: var(--text-1);
  font-size: 18px;
  font-family: var(--font-mono);
}
.stat-label {
  color: var(--text-3);
  font-size: 11px;
}
.block {
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  background: var(--bg-1);
  padding: 10px 12px;
}
.title {
  margin: 0 0 8px;
  font-size: 12px;
  color: var(--text-2);
  text-transform: uppercase;
  letter-spacing: 0.05em;
}
.row {
  display: flex;
  gap: 8px;
  align-items: center;
  flex-wrap: wrap;
  margin-bottom: 8px;
}
.table {
  width: 100%;
  border-collapse: collapse;
  font-size: 12px;
}
.table th {
  text-align: left;
  color: var(--text-3);
  font-weight: 500;
  border-bottom: 1px solid var(--border);
  padding: 4px 6px;
}
.table td {
  padding: 4px 6px;
  border-bottom: 1px solid var(--border);
  color: var(--text-2);
  vertical-align: top;
}
.table tbody tr:hover td {
  background: var(--bg-2);
}
.content {
  max-width: 460px;
}
.meta {
  color: var(--text-3);
  font-size: 12px;
  margin: 6px 0;
}
.forget {
  margin: 0;
  padding-left: 18px;
  display: flex;
  flex-direction: column;
  gap: 4px;
  font-size: 12px;
}
.mono {
  font-family: var(--font-mono);
}
.small {
  font-size: 11px;
}
/* 表格放进横向滚动容器：窄屏时列挤在一起也不撑破布局 */
.table-scroll {
  overflow-x: auto;
}
/* 窄屏：工具条换行，表格保持最小宽度并横向滚动。 */
@media (max-width: 768px) {
  .toolbar {
    flex-wrap: wrap;
  }
  .toolbar .input {
    max-width: none;
    flex: 1 1 140px;
  }
  .table {
    min-width: 520px;
  }
  /* 检索行与实体行：一行一项，输入框不被挤成细条 */
  .row > * {
    flex: 1 1 100%;
  }
}
</style>
