<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { Download, RotateCcw, Save, Trash2, X } from 'lucide-vue-next'
import type { Card, CardExportFormat, CardInput } from '../../api/cards'
import { fetchTagVocabulary } from '../../api/cards'
import { cloneJson } from '../../lib/json'
import {
  RELATION_LEVELS,
  ROLE_OPTIONS,
  introSourceLabel,
  normalizeRelationLevel,
  normalizeRole,
  type CardRole,
  type RelationLevel,
} from '../../lib/cardRoles'
import { joinTags, splitTags, toggleTag } from '../../lib/tagText'

type TextField =
  | 'identity'
  | 'behavior'
  | 'speech'
  | 'mes_example'
  | 'system_prompt'
  | 'post_history_instructions'
  | 'creator_notes'

const props = defineProps<{
  card: Card
  busy: boolean
}>()
const emit = defineEmits<{
  save: [card: CardInput]
  remove: []
  export: [format: CardExportFormat]
  dirtyChange: [dirty: boolean]
}>()

const draft = ref<Card>(cloneJson(props.card))
const confirmDelete = ref(false)
/** 受控标签词表，来自后端；拿不到就只留手输。 */
const vocabulary = ref<string[]>([])
const fields: Array<{ key: TextField; label: string; hint: string; rows: number }> = [
  { key: 'identity', label: '身份与处境', hint: 'identity', rows: 6 },
  { key: 'behavior', label: '行为与细节', hint: 'behavior', rows: 5 },
  { key: 'speech', label: '说话方式', hint: 'speech', rows: 4 },
  { key: 'mes_example', label: '对话示例', hint: 'mes_example', rows: 5 },
]
const extraFields: Array<{ key: TextField; label: string; hint: string; rows: number }> = [
  { key: 'system_prompt', label: '系统提示', hint: 'system_prompt', rows: 3 },
  { key: 'post_history_instructions', label: '历史后指令', hint: 'post_history_instructions', rows: 3 },
  { key: 'creator_notes', label: '创作者备注', hint: 'creator_notes', rows: 3 },
]

const roleOptions = ROLE_OPTIONS

/** 角色定位：主角卡进角色段，配角卡走配角段。 */
const draftRole = computed<CardRole>(() =>
  normalizeRole(typeof draft.value.data.role === 'string' ? draft.value.data.role : ''),
)

const roleHint = computed(
  () => roleOptions.find((option) => option.value === draftRole.value)?.hint ?? '',
)

function setRole(role: CardRole): void {
  draft.value.data.role = role
}

const relationLevels = RELATION_LEVELS

/** 与主角的关系是程度：相逢 / 相识 / 相熟。 */
const relationLevel = computed<RelationLevel>(() =>
  normalizeRelationLevel(typeof draft.value.data.relation === 'string' ? draft.value.data.relation : ''),
)

function updateRelation(event: Event): void {
  draft.value.data.relation = (event.target as HTMLSelectElement).value
}

/** 引入来源：用户带出来的还是模型带出来的。 */
const introSource = computed(() =>
  introSourceLabel(typeof draft.value.data.introduced_by === 'string' ? draft.value.data.introduced_by : ''),
)

/** 这张卡是不是还等着补全。 */
const isPending = computed(() => Boolean(draft.value.data.pending))

/** 配角状态：所在位置、心情、随身物品。 */
function stateText(key: 'location' | 'mood' | 'items'): string {
  const state = draft.value.data.state
  if (!state || typeof state !== 'object') return ''
  const value = (state as Record<string, unknown>)[key]
  if (Array.isArray(value)) return value.map((item) => String(item)).join('、')
  return typeof value === 'string' ? value : ''
}

function updateState(key: 'location' | 'mood' | 'items', event: Event): void {
  const raw = (event.target as HTMLInputElement).value
  const current = draft.value.data.state
  const state: Record<string, unknown> =
    current && typeof current === 'object' ? { ...(current as Record<string, unknown>) } : {}
  if (key === 'items') {
    const items = raw
      .split(/[、,，]/)
      .map((item) => item.trim())
      .filter(Boolean)
    if (items.length) state.items = items
    else delete state.items
  } else if (raw.trim()) {
    state[key] = raw.trim()
  } else {
    delete state[key]
  }
  draft.value.data.state = state
}

const dirty = computed(() =>
  JSON.stringify({ name: draft.value.name, data: draft.value.data }) !==
  JSON.stringify({ name: props.card.name, data: props.card.data }),
)

watch(
  () => props.card,
  (card) => {
    draft.value = cloneJson(card)
    confirmDelete.value = false
  },
)
watch(dirty, (value) => emit('dirtyChange', value), { immediate: true })

function textValue(key: TextField): string {
  const value = draft.value.data[key]
  return typeof value === 'string' ? value : ''
}

function updateText(key: TextField, event: Event): void {
  draft.value.data[key] = (event.target as HTMLTextAreaElement).value
}

/** 标签是类型约束，会跟着角色进提示词，因此在卡片上要能改。 */
function tagsText(): string {
  return joinTags(draft.value.data.tags)
}

function updateTags(event: Event): void {
  const raw = (event.target as HTMLInputElement).value
  draft.value.data.tags = splitTags(raw)
}

/** 候选标签点一下就加上或去掉，省得手打错写法。 */
function pickTag(name: string): void {
  const current = Array.isArray(draft.value.data.tags) ? draft.value.data.tags : []
  draft.value.data.tags = toggleTag(current, name)
}

function hasTag(name: string): boolean {
  const current = draft.value.data.tags
  return Array.isArray(current) && current.includes(name)
}

function reset(): void {
  draft.value = cloneJson(props.card)
}

function save(): void {
  const name = draft.value.name.trim()
  if (!name || props.busy || !dirty.value) return
  const data = cloneJson(draft.value.data)
  data.name = name
  // 用户在编辑器里改过就算处理过了：这张卡可以进提示词了。
  if (data.pending) data.pending = false
  // 场景、开场白、创作者与角色版本本项目不用，保存前剔除。
  delete data.scenario
  delete data.first_mes
  delete data.creator
  delete data.character_version
  emit('save', {
    id: props.card.id,
    name,
    spec_version: props.card.spec_version,
    data,
    raw: cloneJson(props.card.raw),
  })
}

function formatTime(value?: string): string {
  if (!value) return '未记录'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString('zh-CN', { hour12: false })
}

function beforeUnload(event: BeforeUnloadEvent): void {
  if (dirty.value) event.preventDefault()
}
onMounted(() => {
  window.addEventListener('beforeunload', beforeUnload)
  // 候选标签取不到就不显示这一排，卡片照样能编辑。
  fetchTagVocabulary()
    .then((response) => {
      vocabulary.value = response.items ?? []
    })
    .catch(() => {
      vocabulary.value = []
    })
})
onBeforeUnmount(() => window.removeEventListener('beforeunload', beforeUnload))
</script>

<template>
  <section class="editor" aria-label="角色卡详情与编辑">
    <header class="editor-head">
      <div class="identity">
        <div class="name-line">
          <input v-model="draft.name" class="name" aria-label="角色名称" maxlength="200" placeholder="角色名称" />
          <span class="spec">{{ draftRole === 'supporting' ? 'V2' : 'V1' }}</span>
        </div>
        <span class="meta">{{ card.id }} · 更新于 {{ formatTime(card.updated_at) }}</span>
      </div>
      <div class="head-actions">
        <div class="exports" aria-label="导出角色卡">
          <button :disabled="busy" title="由后端导出 JSON" @click="emit('export', 'json')"><Download :size="13" /> JSON</button>
          <button :disabled="busy" title="由后端导出 Markdown" @click="emit('export', 'md')"><Download :size="13" /> Markdown</button>
        </div>
        <button class="danger-icon" :disabled="busy" title="删除角色卡" aria-label="删除角色卡" @click="confirmDelete = true"><Trash2 :size="15" /></button>
      </div>
    </header>

    <div v-if="confirmDelete" class="confirm" role="alertdialog" aria-label="确认删除角色卡">
      <div><strong>删除「{{ card.name }}」？</strong><span>此操作会删除该卡片及其本地文件，且无法撤销。</span></div>
      <button class="secondary" :disabled="busy" @click="confirmDelete = false"><X :size="13" /> 取消</button>
      <button class="danger" :disabled="busy" @click="emit('remove')"><Trash2 :size="13" /> 确认删除</button>
    </div>

    <div class="fields">
      <p v-if="isPending" class="pending-note">
        这张卡还没补全：AI 补全之后才会进提示词。你可以自己填下面三层，或回待完善清单点「重新补全」。
      </p>
      <div class="field">
        <span><strong>角色定位</strong><code>role</code></span>
        <div class="role-picks" role="radiogroup" aria-label="角色定位">
          <button
            v-for="option in roleOptions"
            :key="option.value"
            type="button"
            class="role-pick"
            :class="{ on: draftRole === option.value }"
            :aria-pressed="draftRole === option.value"
            @click="setRole(option.value)"
          >
            {{ option.label }}
          </button>
        </div>
        <small class="hint">{{ roleHint }}</small>
      </div>

      <template v-if="draftRole === 'supporting'">
        <label class="field">
          <span><strong>关系程度</strong><code>relation</code></span>
          <select :value="relationLevel" @change="updateRelation($event)">
            <option v-for="level in relationLevels" :key="level" :value="level">{{ level }}</option>
          </select>
          <small class="hint">关系是程度不是亲疏：随机路过只是相逢，聊过是相识，常来常往是相熟。</small>
        </label>
        <div class="field">
          <span><strong>引入来源</strong><code>introduced_by</code></span>
          <p class="source">{{ introSource }}</p>
          <small class="hint">名字先出现在你的输入里就是你带出来的，先出现在模型回复里就是模型带出来的。</small>
        </div>
        <label class="field">
          <span><strong>所在位置</strong><code>state.location</code></span>
          <input
            :value="stateText('location')"
            placeholder="例：西苑偏殿"
            @input="updateState('location', $event)"
          />
        </label>
        <label class="field">
          <span><strong>当前心情</strong><code>state.mood</code></span>
          <input :value="stateText('mood')" placeholder="例：紧绷" @input="updateState('mood', $event)" />
        </label>
        <label class="field">
          <span><strong>随身物品</strong><code>state.items</code></span>
          <input
            :value="stateText('items')"
            placeholder="顿号分隔，例：玉牌、香囊"
            @input="updateState('items', $event)"
          />
        </label>
      </template>

      <label class="field">
        <span><strong>标签</strong><code>tags</code></span>
        <input
          :value="tagsText()"
          placeholder="用逗号分隔，如：四爱、女攻男受"
          @input="updateTags($event)"
        />
        <small class="hint">标签是类型约束，会跟着角色进提示词。顿号或逗号分隔。</small>
        <div v-if="vocabulary.length" class="tag-picks" aria-label="候选标签">
          <button
            v-for="name in vocabulary"
            :key="name"
            type="button"
            class="tag-pick"
            :class="{ on: hasTag(name) }"
            @click="pickTag(name)"
          >
            {{ name }}
          </button>
        </div>
      </label>

      <label v-for="field in fields" :key="field.key" class="field">
        <span><strong>{{ field.label }}</strong><code>{{ field.hint }}</code></span>
        <textarea
          :value="textValue(field.key)"
          :rows="field.rows"
          :placeholder="`填写${field.label}…`"
          @input="updateText(field.key, $event)"
        />
      </label>

      <details class="extras">
        <summary>扩展文本字段 <span>系统提示、历史后指令与创作者备注</span></summary>
        <label v-for="field in extraFields" :key="field.key" class="field">
          <span><strong>{{ field.label }}</strong><code>{{ field.hint }}</code></span>
          <textarea :value="textValue(field.key)" :rows="field.rows" @input="updateText(field.key, $event)" />
        </label>
      </details>
      <p class="preserve-note">未展示的扩展字段与角色内嵌世界书会原样保留。</p>
    </div>

    <footer class="save-bar">
      <span :class="{ changed: dirty }">{{ dirty ? '有尚未保存的更改' : '所有更改已保存' }}</span>
      <button class="secondary" :disabled="busy || !dirty" @click="reset"><RotateCcw :size="13" /> 还原</button>
      <button class="save" :disabled="busy || !dirty || !draft.name.trim()" @click="save"><Save :size="14" /> {{ busy ? '保存中…' : '保存卡片' }}</button>
    </footer>
  </section>
</template>

<style scoped>
.editor { min-width: 0; min-height: 0; display: flex; flex-direction: column; border: 1px solid var(--border); border-radius: var(--radius-lg); background: var(--bg-1); overflow: hidden; }
.editor-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; padding: 10px 12px; border-bottom: 1px solid var(--border); background: var(--bg-2); }
.identity { min-width: 0; flex: 1; }
.name-line { display: flex; align-items: center; gap: 8px; }
.name { min-width: 0; width: min(420px, 100%); padding: 2px 5px; border-color: transparent; background: transparent; font-size: 17px; font-weight: 600; }
.name:hover, .name:focus { border-color: var(--border); background: var(--bg-0); }
.spec { padding: 1px 5px; border: 1px solid var(--border); border-radius: var(--radius); color: var(--accent); font: 10px var(--font-mono); text-transform: uppercase; }
.meta { display: block; margin-top: 3px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--text-3); font: 10px var(--font-mono); }
.head-actions { display: flex; gap: 7px; align-items: center; }
.exports { display: flex; gap: 3px; }
.exports button, .danger-icon { display: inline-flex; align-items: center; gap: 4px; min-height: 28px; padding: 4px 7px; border: 1px solid var(--border); border-radius: var(--radius); color: var(--text-2); font-size: 11px; }
.exports button:hover { color: var(--accent); border-color: var(--accent); background: var(--accent-soft); }
.danger-icon:hover { color: var(--level-danger); border-color: var(--level-danger); background: var(--danger-soft); }
.confirm { display: flex; align-items: center; gap: 7px; padding: 8px 12px; border-bottom: 1px solid var(--level-danger); background: var(--danger-soft); }
.confirm > div { flex: 1; display: flex; flex-direction: column; }
.confirm strong { color: var(--level-danger); font-size: 12px; }
.confirm span { color: var(--text-2); font-size: 11px; }
.confirm button { display: inline-flex; align-items: center; gap: 4px; padding: 4px 8px; border-radius: var(--radius); font-size: 11px; }
.secondary { border: 1px solid var(--border); color: var(--text-2); }
.danger { border: 1px solid var(--level-danger); color: var(--level-danger); }
.fields { flex: 1; min-height: 0; overflow-y: auto; padding: 12px; }
.field input { width: 100%; padding: 5px 8px; font-size: 12px; }
.hint { color: var(--text-3); font-size: 11px; }
.field { display: flex; flex-direction: column; gap: 5px; margin-bottom: 11px; }
.field > span { display: flex; align-items: baseline; gap: 8px; }
.field strong { font-size: 12px; font-weight: 500; color: var(--text-1); }
.field code { color: var(--text-3); font: 10px var(--font-mono); }
.field textarea { width: 100%; resize: vertical; padding: 7px 9px; font-size: 12px; line-height: 1.6; }
.role-picks { display: flex; gap: 5px; }
.role-pick {
  padding: 4px 10px;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  background: var(--bg-2);
  color: var(--text-2);
  font-size: 12px;
  cursor: pointer;
}
.role-pick:hover { border-color: var(--text-3); color: var(--text-1); }
.role-pick.on { border-color: var(--accent); background: var(--accent-soft); color: var(--text-1); }
.tag-picks { display: flex; flex-wrap: wrap; gap: 4px; margin-top: 2px; }
.tag-pick { padding: 2px 7px; border: 1px solid var(--border); border-radius: 999px; background: var(--bg-2); color: var(--text-2); font-size: 11px; cursor: pointer; }
.tag-pick:hover { border-color: var(--text-3); color: var(--text-1); }
.tag-pick.on { border-color: var(--accent); background: var(--accent-soft); color: var(--text-1); }
.extras { border-top: 1px solid var(--border); padding-top: 8px; }
.extras summary { cursor: pointer; color: var(--text-2); font-size: 12px; }
.extras summary span { margin-left: 6px; color: var(--text-3); font-size: 11px; }
.extras[open] summary { margin-bottom: 11px; color: var(--accent); }
.preserve-note { margin: 5px 0 0; color: var(--text-3); font-size: 11px; }
.pending-note {
  margin: 0 0 11px;
  padding: 6px 9px;
  border: 1px solid var(--level-warn);
  border-radius: var(--radius);
  background: var(--bg-2);
  color: var(--text-2);
  font-size: 11px;
  line-height: 1.6;
}
.source { margin: 0; color: var(--text-1); font-size: 12px; }
.save-bar { display: flex; align-items: center; justify-content: flex-end; gap: 7px; padding: 8px 12px; border-top: 1px solid var(--border); background: var(--bg-2); }
.save-bar > span { margin-right: auto; color: var(--text-3); font-size: 11px; }
.save-bar > span.changed { color: var(--level-warn); }
.save-bar button { display: inline-flex; align-items: center; gap: 5px; padding: 5px 10px; border-radius: var(--radius); font-size: 12px; }
.save { border: 1px solid var(--accent); color: var(--accent); }
.save:hover:not(:disabled) { background: var(--accent-soft); }
.save-bar .secondary:hover:not(:disabled) { background: var(--bg-1); color: var(--text-1); }
button:disabled { opacity: 0.45; cursor: not-allowed; }
@media (max-width: 900px) { .editor-head { flex-direction: column; } .head-actions { width: 100%; justify-content: flex-end; } }
/* 窄屏：保存栏一行放不下就换行，说明文字占满一行。 */
@media (max-width: 768px) {
  .exports button,
  .danger-icon { min-height: 40px; }
  .exports { flex-wrap: wrap; }
  .save-bar { flex-wrap: wrap; }
  .save-bar > span { margin-right: 0; flex: 1 1 100%; }
}
</style>
