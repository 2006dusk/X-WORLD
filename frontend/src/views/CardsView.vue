<script setup lang="ts">
// 卡片库视图：角色卡清单、编辑、导入导出与删除。
// 卡片结构以 SillyTavern V3 为主，同时兼容 V1/V2 的只读归一化结果。
import { computed, onMounted, ref } from 'vue'
import CardList from '../components/card/CardList.vue'
import CardEditor from '../components/card/CardEditor.vue'
import CardImportDialog from '../components/card/CardImportDialog.vue'
import PendingPanel from '../components/card/PendingPanel.vue'
import {
  autofillCard,
  createCard,
  deleteCard,
  downloadCardExport,
  fetchCard,
  fetchCards,
  importCardJson,
  updateCard,
  type Card,
  type CardInput,
  type CardSummary,
} from '../api/cards'

const cards = ref<CardSummary[]>([])
const current = ref<Card | null>(null)
const selectedId = ref('')
const query = ref('')
const loading = ref(false)
const busy = ref(false)
const showImport = ref(false)
const importError = ref('')
const notice = ref('')
const errorText = ref('')
/** 待完善的配角卡：自动建卡时只有名字，设定与状态等模型补全。 */
const pendingCards = computed(() => cards.value.filter((card) => card.pending))

function fail(err: unknown): void {
  errorText.value = err instanceof Error ? err.message : String(err)
}

async function load(): Promise<void> {
  loading.value = true
  errorText.value = ''
  try {
    const res = await fetchCards()
    cards.value = res.items
    if (!selectedId.value && res.items.length > 0) await select(res.items[0].id)
  } catch (err) {
    fail(err)
  } finally {
    loading.value = false
  }
}

async function select(id: string): Promise<void> {
  busy.value = true
  errorText.value = ''
  try {
    current.value = await fetchCard(id)
    selectedId.value = id
  } catch (err) {
    fail(err)
  } finally {
    busy.value = false
  }
}

async function onCreate(): Promise<void> {
  busy.value = true
  errorText.value = ''
  try {
    const card = await createCard({
      name: '新角色卡',
      spec_version: 'v1',
      data: {
        name: '新角色卡',
        identity: '',
        behavior: '',
        speech: '',
        mes_example: '',
        tags: [],
        creator_notes: '',
      },
    })
    await load()
    if (card.id) await select(card.id)
    notice.value = '角色卡已创建'
  } catch (err) {
    fail(err)
  } finally {
    busy.value = false
  }
}

async function onImport(jsonText: string): Promise<void> {
  busy.value = true
  importError.value = ''
  try {
    const card = await importCardJson(jsonText)
    showImport.value = false
    notice.value = `已导入 ${card.name}`
    await load()
    if (card.id) await select(card.id)
  } catch (err) {
    importError.value = err instanceof Error ? err.message : String(err)
  } finally {
    busy.value = false
  }
}

async function onSave(payload: CardInput): Promise<void> {
  if (!current.value) return
  busy.value = true
  errorText.value = ''
  try {
    const saved = await updateCard(current.value.id, {
      id: current.value.id,
      name: payload.name ?? current.value.name,
      spec_version: payload.spec_version ?? current.value.spec_version,
      data: payload.data ?? current.value.data,
      raw: payload.raw ?? current.value.raw,
    })
    current.value = saved
    notice.value = '角色卡已保存'
    await load()
  } catch (err) {
    fail(err)
  } finally {
    busy.value = false
  }
}

async function onRemove(): Promise<void> {
  // 二次确认在编辑器里已经做过（行内确认条），这里不再弹原生对话框。
  if (!current.value) return
  busy.value = true
  errorText.value = ''
  try {
    await deleteCard(current.value.id)
    notice.value = '角色卡已删除'
    current.value = null
    selectedId.value = ''
    await load()
  } catch (err) {
    fail(err)
  } finally {
    busy.value = false
  }
}

async function onAutofill(id: string): Promise<void> {
  busy.value = true
  errorText.value = ''
  try {
    const card = await autofillCard(id)
    notice.value = `已补全 ${card.name} 的设定`
    await load()
    if (card.id) await select(card.id)
  } catch (err) {
    fail(err)
  } finally {
    busy.value = false
  }
}

async function onRemovePending(id: string): Promise<void> {
  busy.value = true
  errorText.value = ''
  try {
    await deleteCard(id)
    notice.value = '已删除这个角色'
    if (selectedId.value === id) {
      current.value = null
      selectedId.value = ''
    }
    await load()
  } catch (err) {
    fail(err)
  } finally {
    busy.value = false
  }
}

async function onExport(format: 'json' | 'md'): Promise<void> {
  if (!current.value) return
  errorText.value = ''
  try {
    await downloadCardExport(current.value, format)
    notice.value = `已导出 ${format.toUpperCase()}`
  } catch (err) {
    fail(err)
  }
}

onMounted(() => {
  void load()
})
</script>

<template>
  <div class="cards">
    <p v-if="errorText" class="error">{{ errorText }}</p>
    <p v-if="notice" class="notice">{{ notice }}</p>

    <PendingPanel
      :cards="pendingCards"
      :busy="busy"
      @autofill="onAutofill"
      @remove="onRemovePending"
    />

    <div class="body">
      <aside class="side">
        <CardList
          v-model:query="query"
          :cards="cards"
          :selected-id="selectedId"
          :loading="loading"
          @select="select"
          @create="onCreate"
          @import="showImport = true; importError = ''"
        />
      </aside>

      <section class="detail">
        <CardEditor
          v-if="current"
          :card="current"
          :busy="busy"
          @save="onSave"
          @remove="onRemove"
          @export="onExport"
        />
        <div v-else class="empty">
          <p>尚未选择角色卡。</p>
          <p class="meta">左侧新建或导入一张角色卡开始。</p>
        </div>
      </section>
    </div>

    <CardImportDialog
      v-if="showImport"
      :busy="busy"
      :error="importError"
      @close="showImport = false"
      @submit="onImport"
    />
  </div>
</template>

<style scoped>
.cards {
  display: flex;
  flex-direction: column;
  gap: 10px;
  height: 100%;
  min-height: 0;
}
.body {
  display: grid;
  grid-template-columns: 300px 1fr;
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
}
.empty {
  margin: auto;
  text-align: center;
  color: var(--text-2);
  font-size: 13px;
}
.meta {
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
.notice {
  margin: 0;
  padding: 6px 10px;
  border-radius: var(--radius);
  background: var(--ok-soft);
  color: var(--level-ok);
  font-size: 12px;
}
/* 窄屏：两栏改上下叠放。清单与详情各占一段高度，内部照旧滚动，
 * 页面不会出现横向滚动条。 */
@media (max-width: 768px) {
  .body {
    grid-template-columns: 1fr;
    grid-template-rows: minmax(0, 2fr) minmax(0, 3fr);
  }
}
</style>
