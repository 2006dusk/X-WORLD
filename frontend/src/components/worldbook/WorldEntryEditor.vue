<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { Save, X } from 'lucide-vue-next'
import type { WorldEntry, WorldEntryInput } from '../../api/worldbooks'
import { fetchCard, fetchCards, type CardSummary } from '../../api/cards'
import { cloneJson } from '../../lib/json'

type EntryDraft = {
  id: string
  keys: string
  secondary_keys: string
  priority: number
  position: number
  constant: boolean
  selective: boolean
  recursion: number
  case_sensitive: boolean
  match_regex: boolean
  comment: string
  content: string
  /** 条目类型：普通条目还是世界区域 */
  kind: 'entry' | 'region'
  /** 区域名。区域条目必填。 */
  name: string
  /** 活引用：指向卡片库里的角色卡 */
  cardRef: { cardId: string; name: string } | null
  extensions: Record<string, unknown>
}

const props = defineProps<{
  entry: WorldEntry | null
  busy: boolean
  error: string
}>()
const emit = defineEmits<{
  close: []
  save: [entry: WorldEntryInput]
}>()

function arrayText(value: unknown): string {
  if (Array.isArray(value)) return value.filter((key): key is string => typeof key === 'string').join(', ')
  return ''
}

function makeDraft(entry: WorldEntry | null): EntryDraft {
  const source = entry ?? {
    id: '', keys: [], content: '', priority: 0, position: 0, constant: false,
    selective: false, recursion: 2, case_sensitive: false, match_regex: false,
  }
  const { id, keys, secondary_keys, priority, position, constant, selective, recursion,
    case_sensitive, match_regex, comment, content, card_ref, kind, name, ...extensions } = source
  const ref = card_ref as { card_id?: string; name?: string } | null | undefined
  return {
    id,
    keys: arrayText(keys),
    secondary_keys: arrayText(secondary_keys ?? []),
    priority,
    position,
    constant,
    selective,
    recursion,
    case_sensitive: case_sensitive ?? false,
    match_regex: match_regex ?? false,
    comment: comment ?? '',
    content,
    kind: kind === 'region' ? 'region' : 'entry',
    name: typeof name === 'string' ? name : '',
    cardRef: ref && ref.card_id ? { cardId: ref.card_id, name: ref.name ?? '' } : null,
    extensions: cloneJson(extensions),
  }
}

const draft = ref<EntryDraft>(makeDraft(props.entry))
const localError = ref('')
const cards = ref<CardSummary[]>([])
const cardId = ref('')

watch(() => props.entry, (entry) => {
  draft.value = makeDraft(entry)
  cardId.value = draft.value.cardRef?.cardId ?? ''
  localError.value = ''
}, { immediate: true })

onMounted(() => {
  fetchCards()
    .then((res) => {
      cards.value = res.items
    })
    .catch(() => {
      cards.value = []
    })
})

/**
 * 引用卡片库里的角色。
 *
 * 引用是活的：正文由角色卡的字段生成，卡片改了这条目跟着更新。
 * 这里先把卡片内容填进来给用户看，保存时后端按同一份规则重算。
 */
async function applyRef(): Promise<void> {
  const id = cardId.value
  if (!id) return
  localError.value = ''
  try {
    const card = await fetchCard(id)
    const name = card.name || id
    const data = card.data ?? {}
    const parts: string[] = []
    if (name) parts.push('【' + name + '】')
    if (data.identity) parts.push('身份与处境：' + String(data.identity))
    if (data.behavior) parts.push('行为与细节：' + String(data.behavior))
    draft.value.cardRef = { cardId: id, name }
    if (parts.length > 0) draft.value.content = parts.join('\n')
    if (splitKeys(draft.value.keys).length === 0) draft.value.keys = name
  } catch (err) {
    localError.value = err instanceof Error ? err.message : String(err)
  }
}

/** 取消引用。正文保留当前内容，之后由用户自己维护。 */
function clearRef(): void {
  draft.value.cardRef = null
  cardId.value = ''
}

const title = computed(() =>
  props.entry && props.entry.id ? '编辑世界书条目' : '新建世界书条目',
)
const shownError = computed(() => localError.value || props.error)

function splitKeys(value: string): string[] {
  return value.split(/[,，\n]/).map((key) => key.trim()).filter(Boolean)
}

function save(): void {
  localError.value = ''
  if (draft.value.kind === 'region' && !draft.value.name.trim()) {
    localError.value = '区域需要一个名字，新建对话时按它选地点。'
    return
  }
  if (
    draft.value.kind !== 'region' &&
    !draft.value.constant &&
    splitKeys(draft.value.keys).length === 0
  ) {
    localError.value = '非常驻条目至少需要一个关键词；若需始终注入，请开启“常驻”。'
    return
  }
  if (draft.value.selective && splitKeys(draft.value.secondary_keys).length === 0) {
    localError.value = '启用二级匹配时，请至少填写一个二级关键词。'
    return
  }
  if (!Number.isFinite(draft.value.priority) || !Number.isInteger(draft.value.priority)) {
    localError.value = '优先级必须是整数。'
    return
  }
  emit('save', {
    ...cloneJson(draft.value.extensions),
    ...(draft.value.id ? { id: draft.value.id } : {}),
    card_ref: draft.value.cardRef
      ? { card_id: draft.value.cardRef.cardId, name: draft.value.cardRef.name }
      : null,
    kind: draft.value.kind,
    name: draft.value.name.trim(),
    keys: splitKeys(draft.value.keys),
    secondary_keys: splitKeys(draft.value.secondary_keys),
    priority: draft.value.priority,
    position: draft.value.position,
    constant: draft.value.constant,
    selective: draft.value.selective,
    recursion: Math.max(0, Math.trunc(draft.value.recursion)),
    case_sensitive: draft.value.case_sensitive,
    match_regex: draft.value.match_regex,
    comment: draft.value.comment,
    content: draft.value.content,
  })
}
</script>

<template>
  <div class="overlay" role="presentation" @click.self="!busy && emit('close')" @keydown.esc="!busy && emit('close')">
    <section class="dialog" role="dialog" aria-modal="true" :aria-label="title">
      <header class="head"><div><h3>{{ title }}</h3><p v-if="entry">{{ entry.id }}</p><p v-else>保存后由后端分配条目 ID</p></div><button :disabled="busy" title="关闭" aria-label="关闭条目编辑" @click="emit('close')"><X :size="17" /></button></header>

      <div class="form">
        <div class="wide kinds">
          <label>类型
            <select v-model="draft.kind">
              <option value="entry">普通条目（按关键词命中）</option>
              <option value="region">世界区域（对话选址）</option>
            </select>
          </label>
          <label v-if="draft.kind === 'region'">区域名<input v-model="draft.name" maxlength="60" placeholder="夜班便利店" /></label>
        </div>
        <label class="wide">条目标题 / 备注<input v-model="draft.comment" maxlength="200" placeholder="便于识别的可选名称" /></label>
        <label class="wide">关键词 <span>使用逗号或换行分隔</span><textarea v-model="draft.keys" rows="2" placeholder="王都, 北门" /></label>
        <label class="wide">二级关键词 <span>启用二级匹配时必填</span><textarea v-model="draft.secondary_keys" rows="2" placeholder="深夜, 宵禁" /></label>

        <label>优先级<input v-model.number="draft.priority" type="number" step="1" /></label>
        <label>注入位置<select v-model.number="draft.position"><option :value="0">0 · 角色定义之前</option><option :value="1">1 · 角色定义之后</option><option :value="2">2 · 示例消息之前</option><option :value="3">3 · 示例消息之后</option><option :value="4">4 · 指定深度</option></select></label>
        <label>递归深度<input v-model.number="draft.recursion" type="number" min="0" step="1" /></label>
        <div class="matching"><span>匹配方式</span><div><label><input v-model="draft.constant" type="checkbox" /> 常驻</label><label><input v-model="draft.selective" type="checkbox" /> 二级匹配</label><label><input v-model="draft.match_regex" type="checkbox" /> 正则</label><label><input v-model="draft.case_sensitive" type="checkbox" /> 大小写敏感</label></div></div>
        <div class="wide ref">
          <span class="ref-label">引用卡片库角色</span>
          <div class="ref-row">
            <select v-model="cardId">
              <option value="">不引用</option>
              <option v-for="card in cards" :key="card.id" :value="card.id">{{ card.name }}</option>
            </select>
            <button class="secondary" type="button" :disabled="!cardId" @click="applyRef">引用</button>
            <button v-if="draft.cardRef" class="secondary" type="button" @click="clearRef">取消引用</button>
          </div>
          <p v-if="draft.cardRef" class="ref-note">
            正文由卡片「{{ draft.cardRef.name || draft.cardRef.cardId }}」生成，卡片改了这条目跟着更新。
          </p>
        </div>
        <label class="wide content">条目内容<textarea v-model="draft.content" rows="9" placeholder="命中后由后端注入上下文的设定内容…" /></label>
        <p class="note">前端仅提交匹配配置；关键词判断、正则执行、命中排序和递归均在后端完成。未展示的扩展字段将原样保留。</p>
      </div>

      <p v-if="shownError" class="error" role="alert">{{ shownError }}</p>
      <footer><span>{{ draft.constant ? '常驻条目无需关键词。' : '关键词条目会按输入文本命中。' }}</span><div><button class="secondary" :disabled="busy" @click="emit('close')">取消</button><button class="primary" :disabled="busy" @click="save"><Save :size="14" /> {{ busy ? '保存中…' : '保存条目' }}</button></div></footer>
    </section>
  </div>
</template>

<style scoped>
.overlay { position: fixed; inset: 0; z-index: 30; display: grid; place-items: center; padding: 18px; background: var(--bg-0); }
.dialog { width: min(700px, 100%); max-height: calc(100vh - 36px); display: flex; flex-direction: column; border: 1px solid var(--accent); border-radius: var(--radius-lg); background: var(--bg-1); padding: 13px; }
.head { display: flex; align-items: flex-start; justify-content: space-between; margin-bottom: 11px; }
h3 { margin: 0; font-size: 15px; }
.head p { margin: 2px 0 0; color: var(--text-3); font: 10px var(--font-mono); }
.head button { display: inline-flex; padding: 3px; border-radius: var(--radius); color: var(--text-3); }
.head button:hover { color: var(--text-1); background: var(--bg-2); }
.form { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 9px 10px; overflow-y: auto; padding-right: 2px; }
.form > label { display: flex; flex-direction: column; gap: 4px; color: var(--text-2); font-size: 11px; }
.form label > span { color: var(--text-3); font-size: 10px; }
.wide { grid-column: 1 / -1; }
input:not([type='checkbox']), select, textarea { width: 100%; padding: 5px 7px; font-size: 12px; }
textarea { resize: vertical; line-height: 1.5; }
.content textarea { min-height: 120px; }
.matching { grid-column: 1 / -1; }
.kinds { display: grid; grid-template-columns: 1fr 1fr; gap: 9px 10px; }
.ref { display: flex; flex-direction: column; gap: 4px; }
.ref-label { color: var(--text-2); font-size: 11px; }
.ref-row { display: flex; align-items: center; gap: 6px; }
.ref-row select { flex: 1; }
.ref-row button { white-space: nowrap; }
.ref-note { margin: 0; color: var(--text-3); font-size: 10px; }
.matching > span { display: block; margin-bottom: 5px; color: var(--text-2); font-size: 11px; }
.matching > div { display: flex; flex-wrap: wrap; gap: 7px 15px; padding: 7px 9px; border: 1px solid var(--border); border-radius: var(--radius); background: var(--bg-0); }
.matching label { display: inline-flex; align-items: center; gap: 5px; color: var(--text-2); font-size: 11px; cursor: pointer; }
.matching input { accent-color: var(--accent); }
.note { grid-column: 1 / -1; margin: 0; color: var(--text-3); font-size: 10px; }
.error { margin: 8px 0 0; padding: 6px 8px; border: 1px solid var(--level-danger); border-radius: var(--radius); background: var(--danger-soft); color: var(--level-danger); font-size: 11px; }
footer { display: flex; align-items: center; justify-content: space-between; gap: 10px; margin-top: 11px; padding-top: 10px; border-top: 1px solid var(--border); }
footer > span { color: var(--text-3); font-size: 10px; }
footer > div { display: flex; gap: 6px; }
footer button { display: inline-flex; align-items: center; gap: 4px; padding: 5px 10px; border-radius: var(--radius); font-size: 12px; }
.primary { border: 1px solid var(--accent); color: var(--accent); }
.primary:hover { background: var(--accent-soft); }
.secondary { border: 1px solid var(--border); color: var(--text-2); }
.secondary:hover { background: var(--bg-2); color: var(--text-1); }
button:disabled { opacity: 0.45; cursor: not-allowed; }
/* 窄屏：表单单列，弹窗按可视视口算高度，内容自己滚。 */
@media (max-width: 560px) {
  .form { grid-template-columns: 1fr; }
  .wide, .matching, .note { grid-column: 1; }
  .kinds { grid-template-columns: 1fr; }
  .ref-row { flex-wrap: wrap; }
  .ref-row select { flex: 1 1 100%; }
  footer { flex-direction: column; align-items: stretch; }
  footer > div { justify-content: flex-end; }
  .overlay { padding: 12px; }
  .dialog { max-height: calc(var(--app-h) - 24px); }
}
</style>
