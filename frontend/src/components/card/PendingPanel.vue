<script setup lang="ts">
// 待完善的配角卡：自动建卡时只有名字，三层设定与状态由模型补全。
// 补全之前这张卡不进提示词，用户在这里补信息、改关系档或删掉。
import { RefreshCw, Trash2 } from 'lucide-vue-next'
import type { CardSummary } from '../../api/cards'
import { introSourceLabel, normalizeRelationLevel } from '../../lib/cardRoles'

const props = defineProps<{
  cards: CardSummary[]
  busy: boolean
}>()

const emit = defineEmits<{
  autofill: [id: string]
  remove: [id: string]
}>()
</script>

<template>
  <section v-if="props.cards.length" class="pending" aria-label="待完善的配角卡">
    <header>
      <strong>待完善的角色</strong>
      <span>{{ props.cards.length }} 个还没补全</span>
    </header>
    <ul>
      <li v-for="card in props.cards" :key="card.id">
        <div class="info">
          <strong>{{ card.name }}</strong>
          <span class="meta">
            {{ introSourceLabel(card.introduced_by) }} · 关系 {{ normalizeRelationLevel(card.relation) }}
          </span>
        </div>
        <div class="actions">
          <button :disabled="busy" class="fill" @click="emit('autofill', card.id)">
            <RefreshCw :size="13" /> 重新补全
          </button>
          <button :disabled="busy" class="remove" title="删掉这个角色" @click="emit('remove', card.id)">
            <Trash2 :size="13" /> 删除
          </button>
        </div>
      </li>
    </ul>
  </section>
</template>

<style scoped>
.pending {
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  background: var(--bg-1);
  padding: 8px 10px;
}
.pending header {
  display: flex;
  align-items: baseline;
  gap: 8px;
  margin-bottom: 6px;
}
.pending header strong { font-size: 12px; font-weight: 600; color: var(--text-1); }
.pending header span { color: var(--text-3); font-size: 11px; }
.pending ul { list-style: none; margin: 0; padding: 0; display: flex; flex-wrap: wrap; gap: 6px; }
.pending li {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
  padding: 5px 8px;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  background: var(--bg-2);
}
.info { display: flex; flex-direction: column; min-width: 0; }
.info strong { font-size: 12px; font-weight: 500; color: var(--text-1); }
.meta { color: var(--text-3); font-size: 11px; }
.actions { display: flex; gap: 4px; }
.actions button {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 3px 7px;
  border-radius: var(--radius);
  font-size: 11px;
  white-space: nowrap;
}
.fill { border: 1px solid var(--accent); color: var(--accent); }
.fill:hover:not(:disabled) { background: var(--accent-soft); }
.remove { border: 1px solid var(--border); color: var(--text-2); }
.remove:hover:not(:disabled) { color: var(--level-danger); border-color: var(--level-danger); }
button:disabled { opacity: 0.45; cursor: not-allowed; }
</style>
