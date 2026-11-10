<script setup lang="ts">
import { computed } from 'vue'
import { FilePlus2, Import, Search, X } from 'lucide-vue-next'
import type { CardSummary } from '../../api/cards'
import { groupCardsByRole, normalizeRole } from '../../lib/cardRoles'

const props = defineProps<{
  cards: CardSummary[]
  selectedId: string
  loading: boolean
}>()

const query = defineModel<string>('query', { default: '' })
const emit = defineEmits<{
  select: [id: string]
  create: []
  import: []
}>()

const normalizedQuery = computed(() => query.value.trim().toLocaleLowerCase())
const filtered = computed(() => {
  if (!normalizedQuery.value) return props.cards
  return props.cards.filter((card) =>
    card.name.toLocaleLowerCase().includes(normalizedQuery.value),
  )
})

// 两类角色在列表上一眼能分开：V1 主要人物在前，V2 引入角色在后。
const groups = computed(() => {
  const grouped = groupCardsByRole(filtered.value)
  return [
    { role: 'primary', label: 'V1 主要人物', items: grouped.primary, empty: '还没有主要人物。' },
    {
      role: 'supporting',
      label: 'V2 引入角色',
      items: grouped.supporting,
      empty: '还没有对话中引入的角色。',
    },
  ]
})

function formatTime(value?: string): string {
  if (!value) return '未记录时间'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).format(date)
}
</script>

<template>
  <aside class="card-list" aria-label="角色卡列表">
    <div class="list-head">
      <div>
        <h2>角色卡</h2>
        <span>{{ cards.length }} 张</span>
      </div>
      <button class="icon-button" title="新建空白角色卡" aria-label="新建空白角色卡" @click="emit('create')">
        <FilePlus2 :size="16" />
      </button>
    </div>

    <label class="search">
      <Search :size="14" />
      <input v-model="query" type="search" placeholder="按名称过滤…" aria-label="按名称过滤角色卡" />
      <button v-if="query" title="清空过滤" aria-label="清空过滤" @click="query = ''">
        <X :size="13" />
      </button>
    </label>

    <div class="items">
      <p v-if="loading" class="state">正在读取卡片库…</p>
      <template v-else>
        <section v-for="group in groups" :key="group.role" class="group">
          <h3 class="group-title">
            {{ group.label }}
            <span>{{ group.items.length }}</span>
          </h3>
          <button
            v-for="card in group.items"
            :key="card.id"
            class="card-item"
            :class="{ selected: card.id === selectedId }"
            :aria-pressed="card.id === selectedId"
            @click="emit('select', card.id)"
          >
            <span class="item-top">
              <strong :title="card.name">{{ card.name }}</strong>
              <span class="spec">{{ normalizeRole(card.role) === 'supporting' ? 'V2' : 'V1' }}</span>
              <span v-if="card.pending" class="pending-flag">待完善</span>
            </span>
            <span class="updated" :title="card.updated_at">更新于 {{ formatTime(card.updated_at) }}</span>
          </button>
          <p v-if="group.items.length === 0" class="group-empty">{{ group.empty }}</p>
        </section>

        <div v-if="cards.length === 0" class="empty">
          <strong>卡片库还是空的</strong>
          <p>导入 V1、V2 或 V3 JSON 角色卡，或新建一张空白卡开始编辑。</p>
          <button class="primary" @click="emit('import')"><Import :size="14" /> 导入角色卡</button>
        </div>
        <div v-else-if="filtered.length === 0" class="empty compact">
          <strong>没有匹配的卡片</strong>
          <p>换一个名称关键词，或清空当前过滤。</p>
          <button class="text-button" @click="query = ''">清空过滤</button>
        </div>
      </template>
    </div>
  </aside>
</template>

<style scoped>
.card-list {
  min-height: 0;
  display: flex;
  flex-direction: column;
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  background: var(--bg-1);
  overflow: hidden;
}
.list-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 10px 7px;
}
.list-head > div { display: flex; align-items: baseline; gap: 8px; }
h2 { margin: 0; font-size: 14px; font-weight: 600; }
.list-head span { color: var(--text-3); font-size: 11px; }
.icon-button {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  color: var(--text-2);
}
.icon-button:hover { color: var(--accent); border-color: var(--accent); background: var(--accent-soft); }
.search {
  display: flex;
  align-items: center;
  gap: 6px;
  margin: 0 9px 8px;
  padding: 0 7px;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  background: var(--bg-0);
  color: var(--text-3);
}
.search:focus-within { border-color: var(--accent); }
.search input { min-width: 0; width: 100%; border: 0; padding: 6px 0; background: transparent; font-size: 12px; }
.search button { display: inline-flex; color: var(--text-3); }
.search button:hover { color: var(--text-1); }
.items { flex: 1; min-height: 0; padding: 0 6px 6px; overflow-y: auto; }
.card-item {
  display: flex;
  flex-direction: column;
  gap: 3px;
  width: 100%;
  padding: 8px;
  border: 1px solid transparent;
  border-radius: var(--radius);
  text-align: left;
  color: var(--text-2);
}
.card-item:hover { background: var(--bg-2); color: var(--text-1); }
.card-item.selected { background: var(--accent-soft); border-color: var(--accent); }
.item-top { display: flex; align-items: center; gap: 6px; min-width: 0; }
strong { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 13px; font-weight: 500; color: var(--text-1); }
.spec {
  margin-left: auto;
  padding: 0 4px;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  color: var(--text-3);
  font: 10px var(--font-mono);
  text-transform: uppercase;
}
.updated { color: var(--text-3); font-size: 11px; }
.state { padding: 12px 6px; color: var(--text-3); font-size: 12px; }
.group { padding: 0 0 6px; }
.group-title {
  display: flex;
  align-items: baseline;
  gap: 6px;
  margin: 4px 2px 3px;
  color: var(--text-3);
  font-size: 11px;
  font-weight: 500;
}
.group-title span { font: 10px var(--font-mono); }
.group-empty { margin: 0; padding: 4px 8px; color: var(--text-3); font-size: 11px; }
.pending-flag {
  padding: 0 4px;
  border: 1px solid var(--level-warn);
  border-radius: var(--radius);
  color: var(--level-warn);
  font-size: 10px;
  white-space: nowrap;
}
.empty { margin: 22px 4px 4px; padding: 16px 10px; border: 1px dashed var(--border); border-radius: var(--radius-lg); text-align: center; }
.empty strong { display: block; }
.empty p { margin: 7px 0 12px; color: var(--text-3); font-size: 12px; }
.empty.compact { border: 0; }
.primary { display: inline-flex; align-items: center; justify-content: center; gap: 5px; padding: 5px 9px; border: 1px solid var(--accent); border-radius: var(--radius); color: var(--accent); font-size: 12px; }
.primary:hover { background: var(--accent-soft); }
.text-button { color: var(--accent); font-size: 12px; }
.text-button:hover { text-decoration: underline; }
</style>
