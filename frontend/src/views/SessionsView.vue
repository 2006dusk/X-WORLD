<!-- 会话列表视图：一个人的对话始终一条，按所在区域分组。
     这里只做管理与跳转，消息正文在对话页看，避免所有对话挤在一个界面里。 -->
<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { Download, GitBranch, MessageSquare, Plus, Trash2 } from 'lucide-vue-next'
import {
  deleteBranch,
  deleteSession,
  downloadSessionMarkdown,
  fetchSessionSnapshot,
  fetchSessions,
  type SessionSummary,
} from '../api/sessions'
import { fetchWorldBooks, fetchWorldRegions, type WorldBookSummary } from '../api/worldbooks'
import { fetchCards, type CardSummary } from '../api/cards'
import {
  collapseGroups,
  currentPathIds,
  groupDepth,
  type GroupNode,
} from '../lib/branchTree'
import { branchCountLabel } from '../lib/sessionLabel'
import NewConversationDialog from '../components/NewConversationDialog.vue'

const router = useRouter()
const sessions = ref<SessionSummary[]>([])
const books = ref<WorldBookSummary[]>([])
const cards = ref<CardSummary[]>([])
const regionNames = ref<Record<string, string>>({})
const busy = ref(false)
const notice = ref('')
const errorText = ref('')
const wizardOpen = ref(false)
/** 展开了分支清单的会话 id：一次只开一个。 */
const branchPanel = ref('')
/** 分支清单：折叠后的每一段，删一段就是删一条支线。 */
const branchGroups = ref<GroupNode[]>([])
/** 当前路径上的节点 id：用来标出「当前」那一段。 */
const branchCurrent = ref<Set<string>>(new Set())
const branchLoading = ref(false)
/** 待确认的删除：分支组 id 或会话 id。空串表示没有待确认的删除。
 *
 * 不用原生确认框：它会挡住整个页面，在内嵌浏览器里还会被直接确认掉。
 */
const pendingGroup = ref('')
const pendingSession = ref('')

const cardNames = computed(() => {
  const map = new Map<string, string>()
  for (const card of cards.value) map.set(card.id, card.name)
  return map
})

/** 按对话所在的区域分组，组内按创建时间倒序。 */
const groups = computed(() => {
  const map = new Map<string, SessionSummary[]>()
  for (const item of sessions.value) {
    const key = item.region_id ?? ''
    const list = map.get(key)
    if (list) {
      list.push(item)
    } else {
      map.set(key, [item])
    }
  }
  return [...map.entries()].map(([key, items]) => ({
    key: key || 'none',
    label: key
      ? (regionNames.value[key] ?? key)
      : '未指定区域',
    items: items.slice().sort((a, b) =>
      String(b.created_at ?? '').localeCompare(String(a.created_at ?? '')),
    ),
  }))
})

function fail(err: unknown): void {
  errorText.value = err instanceof Error ? err.message : String(err)
}

async function load(): Promise<void> {
  busy.value = true
  errorText.value = ''
  try {
    const [sessionRes, bookRes, cardRes] = await Promise.all([
      fetchSessions(),
      fetchWorldBooks(),
      fetchCards(),
    ])
    sessions.value = sessionRes.items
    books.value = bookRes.items
    cards.value = cardRes.items
    await loadRegionNames(bookRes.items)
  } catch (err) {
    fail(err)
  } finally {
    busy.value = false
  }
}

/** 区域 id 到名字。分组标题显示区域名而不是 id。 */
async function loadRegionNames(items: WorldBookSummary[]): Promise<void> {
  const map: Record<string, string> = {}
  for (const book of items) {
    try {
      const res = await fetchWorldRegions(book.id)
      for (const region of res.items) map[region.id] = region.name
    } catch {
      /* 取不到就跳过 */
    }
  }
  regionNames.value = map
}

function openChat(id: string): void {
  void router.push({ path: '/chat', query: { session: id } })
}

async function onDelete(item: SessionSummary): Promise<void> {
  busy.value = true
  errorText.value = ''
  pendingSession.value = ''
  try {
    await deleteSession(item.id)
    notice.value = '会话已删除'
    if (branchPanel.value === item.id) branchPanel.value = ''
    await load()
    notifySessionsChanged()
  } catch (err) {
    fail(err)
  } finally {
    busy.value = false
  }
}

async function onExport(item: SessionSummary): Promise<void> {
  errorText.value = ''
  try {
    await downloadSessionMarkdown(item.id)
    notice.value = '逐字稿已导出'
  } catch (err) {
    fail(err)
  }
}

function onCreated(id: string): void {
  wizardOpen.value = false
  openChat(id)
}

function speakerLabel(item: SessionSummary): string {
  const roster = item.characters ?? []
  if (roster.length === 0) return '无主角色'
  return roster
    .map((id) => cardNames.value.get(id) ?? id)
    .join('、')
}



/** 通知侧栏：会话列表变了，侧栏的分支数要跟着刷新。 */
function notifySessionsChanged(): void {
  window.dispatchEvent(new CustomEvent('xworld:sessions-changed'))
}

/** 分支条目的正文预览：压成一行，超出截断。 */
function preview(text: string): string {
  const flat = text.replace(/\s+/g, ' ').trim()
  return flat.length > 42 ? flat.slice(0, 42) + '…' : flat
}

/** 展开或收起一段对话的分支清单。 */
async function toggleBranches(item: SessionSummary): Promise<void> {
  pendingGroup.value = ''
  pendingSession.value = ''
  if (branchPanel.value === item.id) {
    branchPanel.value = ''
    return
  }
  branchPanel.value = item.id
  branchLoading.value = true
  errorText.value = ''
  try {
    await loadBranches(item.id)
  } catch (err) {
    fail(err)
    branchGroups.value = []
    branchCurrent.value = new Set()
  } finally {
    branchLoading.value = false
  }
}

/** 读一段对话的分支树，折成分段清单。 */
async function loadBranches(id: string): Promise<void> {
  const snapshot = await fetchSessionSnapshot(id)
  const tree = snapshot.tree ?? []
  const head = snapshot.head_id ?? ''
  branchGroups.value = collapseGroups(tree, head)
  branchCurrent.value = currentPathIds(tree, head)
}

/** 删掉一段支线：别的支线照旧留着。 */
async function onDeleteGroup(item: SessionSummary, group: GroupNode): Promise<void> {
  busy.value = true
  errorText.value = ''
  pendingGroup.value = ''
  try {
    await deleteBranch(item.id, group.members)
    notice.value = `已删除这一段（${group.rounds} 轮），别的支线照旧留着`
    await loadBranches(item.id)
    await load()
    notifySessionsChanged()
  } catch (err) {
    fail(err)
  } finally {
    busy.value = false
  }
}

onMounted(() => {
  void load()
})
</script>

<template>
  <div class="sessions">
    <header class="toolbar">
      <h2 class="heading">会话</h2>
      <span class="meta">{{ sessions.length }} 段对话</span>
      <button class="btn primary" :disabled="busy" @click="wizardOpen = true">
        <Plus :size="13" />
        新建对话
      </button>
    </header>

    <p v-if="errorText" class="error">{{ errorText }}</p>
    <p v-if="notice" class="notice">{{ notice }}</p>

    <div class="body">
      <section v-for="group in groups" :key="group.key" class="group">
        <h3 class="group-title">
          <span class="bar"></span>
          {{ group.label }}
          <span class="group-count">{{ group.items.length }}</span>
        </h3>
        <ul class="rows">
          <li v-for="item in group.items" :key="item.id">
            <div class="row" :class="{ open: branchPanel === item.id }">
              <div class="row-main">
                <span class="row-name">{{ speakerLabel(item) }}</span>
                <span v-if="branchCountLabel(item.branch_count ?? 0)" class="row-meta">{{ branchCountLabel(item.branch_count ?? 0) }}</span>
                <span v-if="item.created_at" class="row-time mono">{{ item.created_at }}</span>
              </div>
              <div class="row-actions">
                <button class="icon-btn" title="打开对话" @click="openChat(item.id)">
                  <MessageSquare :size="13" />
                </button>
                <button class="icon-btn" title="导出逐字稿" @click="onExport(item)">
                  <Download :size="13" />
                </button>
                <button
                  class="icon-btn"
                  :class="{ active: branchPanel === item.id }"
                  title="管理分支"
                  @click="toggleBranches(item)"
                >
                  <GitBranch :size="13" />
                </button>
              </div>
            </div>

            <div v-if="branchPanel === item.id" class="branch-panel">
              <p v-if="branchLoading" class="meta">正在读分支…</p>
              <ul v-else class="branch-list">
                <li
                  v-for="g in branchGroups"
                  :key="g.id"
                  class="branch-item"
                  :style="{ paddingLeft: 8 + groupDepth(branchGroups, g.id) * 14 + 'px' }"
                >
                  <span class="branch-role">{{ g.role === 'user' ? '我' : '角色' }}</span>
                  <span class="branch-text">{{ preview(g.content ?? '') }}</span>
                  <span v-if="branchCurrent.has(g.id)" class="branch-tag">当前</span>
                  <span class="branch-rounds">{{ g.rounds }} 轮</span>
                  <template v-if="pendingGroup === g.id">
                    <span class="branch-confirm">确认删除这一段？</span>
                    <button class="mini danger" :disabled="busy" @click="onDeleteGroup(item, g)">
                      确认
                    </button>
                    <button class="mini" @click="pendingGroup = ''">取消</button>
                  </template>
                  <button
                    v-else
                    class="icon-btn danger"
                    title="删除这一段"
                    :disabled="busy"
                    @click="pendingGroup = g.id"
                  >
                    <Trash2 :size="13" />
                  </button>
                </li>
              </ul>
              <p v-if="!branchLoading && branchGroups.length === 0" class="meta">
                这段对话还没有分支。
              </p>
              <button
                v-if="pendingSession === item.id"
                class="link-danger confirm"
                :disabled="busy"
                @click="onDelete(item)"
              >
                确认删除整段对话
              </button>
              <button v-else class="link-danger" :disabled="busy" @click="pendingSession = item.id">
                删除整段对话
              </button>
            </div>
          </li>
        </ul>
      </section>

      <p v-if="groups.length === 0" class="meta empty">
        还没有会话。点右上角「新建对话」，先选一个世界位置。
      </p>
    </div>

    <NewConversationDialog v-if="wizardOpen" @close="wizardOpen = false" @created="onCreated" />
  </div>
</template>

<style scoped>
.sessions {
  display: flex;
  flex-direction: column;
  gap: 10px;
  height: 100%;
  min-height: 0;
}
.toolbar {
  display: flex;
  align-items: center;
  gap: 10px;
}
.heading {
  margin: 0;
  font-size: 13px;
  color: var(--text-2);
  text-transform: uppercase;
  letter-spacing: 0.05em;
}
.toolbar .meta {
  margin-right: auto;
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
}
.btn:hover:not(:disabled) {
  background: var(--bg-2);
  color: var(--text-1);
}
.btn.primary {
  border-color: transparent;
  background: var(--accent);
  color: #ffffff;
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
.body {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 14px;
}
.group {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.group-title {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 0;
  font-size: 12px;
  font-weight: 600;
  color: var(--text-2);
}
.bar {
  display: inline-block;
  width: 3px;
  height: 12px;
  border-radius: 2px;
  background: var(--accent);
}
.group-count {
  color: var(--text-3);
  font-weight: 400;
}
.rows {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  padding: 7px 10px;
  border-radius: var(--radius);
  background: var(--bg-1);
}
.row:hover {
  background: var(--bg-2);
}
.row.open {
  background: var(--bg-2);
}
.row-main {
  display: flex;
  align-items: baseline;
  gap: 10px;
  min-width: 0;
}
.row-name {
  color: var(--text-1);
  font-size: 13px;
  white-space: nowrap;
}
.row-meta {
  color: var(--text-2);
  font-size: 12px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.row-time {
  color: var(--text-3);
  font-size: 11px;
}
.row-actions {
  display: flex;
  gap: 4px;
  flex: none;
}
.icon-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 26px;
  height: 26px;
  border-radius: var(--radius);
  color: var(--text-3);
}
.icon-btn:hover {
  background: var(--bg-0);
  color: var(--text-1);
}
.icon-btn.danger:hover {
  color: var(--level-danger);
}
.icon-btn.active {
  background: var(--accent-soft);
  color: var(--accent);
}
.icon-btn:disabled {
  opacity: 0.5;
}
.branch-panel {
  margin: 2px 0 6px;
  padding: 6px 8px;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  background: var(--bg-0);
}
.branch-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.branch-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 4px 6px;
  border-radius: var(--radius);
  color: var(--text-2);
  font-size: 12px;
}
.branch-item:hover {
  background: var(--bg-2);
}
.branch-role {
  flex: none;
  color: var(--text-3);
  font-size: 11px;
}
.branch-text {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.branch-tag {
  flex: none;
  padding: 0 5px;
  border-radius: 999px;
  background: var(--accent-soft);
  color: var(--accent);
  font-size: 10px;
}
.branch-rounds {
  flex: none;
  color: var(--text-3);
  font-size: 11px;
}
.link-danger {
  margin-top: 6px;
  padding: 0;
  border: none;
  background: transparent;
  color: var(--text-3);
  font-size: 11px;
  text-decoration: underline;
}
.link-danger:hover:not(:disabled) {
  color: var(--level-danger);
}
.link-danger.confirm {
  color: var(--level-danger);
}
.branch-confirm {
  flex: none;
  color: var(--level-danger);
  font-size: 11px;
}
.mini {
  flex: none;
  padding: 2px 8px;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  background: var(--bg-1);
  color: var(--text-2);
  font-size: 11px;
}
.mini:hover:not(:disabled) {
  border-color: var(--accent);
  color: var(--accent);
}
.mini.danger:hover:not(:disabled) {
  border-color: var(--level-danger);
  color: var(--level-danger);
}
.mini:disabled {
  opacity: 0.5;
}
.meta {
  color: var(--text-3);
  font-size: 12px;
}
.empty {
  padding: 24px 0;
  text-align: center;
}
/* 窄屏：工具条换行，分支行内控件不再被挤扁。 */
@media (max-width: 768px) {
  .toolbar {
    flex-wrap: wrap;
  }
  .toolbar .meta {
    margin-right: 0;
  }
  .branch-item {
    flex-wrap: wrap;
  }
}
</style>
