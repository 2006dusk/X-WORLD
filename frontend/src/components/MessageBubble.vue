<script setup lang="ts">
// 消息气泡：正文、思考折叠、计量徽标、错误与重试/续写入口。
// 思考内容与正文分离，默认折叠。
import { computed, ref } from 'vue'
import { Brain, ChevronDown, ChevronRight, Pencil, RotateCcw, Play } from 'lucide-vue-next'
import type { DisplayMessage } from '../stores/chat'
import { useChatStore } from '../stores/chat'
import { buildEditSource } from '../lib/editSource'

const props = defineProps<{
  msg: DisplayMessage
}>()

const chat = useChatStore()
const expanded = ref(false)
/** 修改输入的行内编辑态 */
const editing = ref(false)
const draft = ref('')

/** 修改按钮对每一条用户输入开放：改写中间某轮会在那里分叉。 */
const canRevise = computed(
  (): boolean => props.msg.role === 'user' && !chat.busy,
)

/** 重新生成对每一条角色回复开放：原地换掉那一条，不在树上新增节点。 */
const canRegenerate = computed(
  (): boolean =>
    props.msg.role === 'assistant' && !chat.busy && !props.msg.error,
)

/**
 * 角色回复的修改入口：任意一轮都能改。
 *
 * 编辑内容是完整的编辑源文本：动作、场景、对白与状态四段都在里面，
 * 提交后定稿落盘，并在该轮的父节点下分叉出新回复。
 */
const canEditReply = computed(
  (): boolean =>
    props.msg.role === 'assistant' && !chat.busy && !props.msg.error,
)

/** 编辑源文本：结构化回合带块标记，普通回合是纯正文。 */
function editSource(): string {
  const source = buildEditSource(
    props.msg.rawBlocks ?? null,
    props.msg.structure?.labels ?? {},
  )
  return source || props.msg.content
}

function startEdit(): void {
  draft.value = props.msg.role === 'assistant' ? editSource() : props.msg.content
  editing.value = true
}

function cancelEdit(): void {
  editing.value = false
  draft.value = ''
}

/** 提交修改：用户输入改完重跑这一轮；角色回复改完定稿并分叉。 */
async function submitEdit(): Promise<void> {
  const text = draft.value.trim()
  if (!text) return
  editing.value = false
  draft.value = ''
  if (props.msg.role === 'assistant') {
    await chat.editAssistant(props.msg.id, text)
    return
  }
  await chat.revise(props.msg.id, text)
}

/** 重新生成：用同样的输入再答一次，原地换掉这一条。 */
async function onRegenerate(): Promise<void> {
  await chat.regenerate(props.msg.id)
}

const hasReasoning = (): boolean => props.msg.reasoning.length > 0

/** 结构化回合的块：只展示有内容的块，标签由后端给出。 */
const visibleBlocks = computed(() => {
  const structure = props.msg.structure
  if (!structure) return []
  return Object.keys(structure.blocks)
    .map((name) => ({
      name,
      label: structure.labels[name] ?? name,
      text: (structure.blocks[name] ?? '').trim(),
    }))
    .filter((item) => item.text.length > 0)
})

/** 生成速率，显示在卡片右上角。 */
const rateText = computed<string>(() => {
  const tps = props.msg.usage?.generation_tps
  if (!tps || tps <= 0) return ''
  return tps.toFixed(1) + ' tok/s'
})
</script>

<template>
  <div class="bubble" :class="msg.role">
    <div v-if="msg.usage || rateText" class="head">
      <span v-if="msg.usage" class="badge">
        {{ msg.usage.prompt_tokens }} + {{ msg.usage.completion_tokens }}
        <template v-if="msg.usage.reasoning_tokens > 0">
          · 思考 {{ msg.usage.reasoning_tokens }}
        </template>
      </span>
      <span v-if="rateText" class="rate">{{ rateText }}</span>
    </div>

    <!-- 思考内容折叠块 -->
    <div v-if="hasReasoning()" class="reasoning">
      <button class="fold" @click="expanded = !expanded">
        <Brain :size="13" />
        <span>思考（{{ msg.reasoning.length }} 字）</span>
        <component :is="expanded ? ChevronDown : ChevronRight" :size="13" />
      </button>
      <pre v-if="expanded" class="reasoning-body">{{ msg.reasoning }}</pre>
    </div>

    <div v-if="editing" class="edit-box">
      <textarea v-model="draft" class="edit-input" rows="3" />
      <div class="edit-actions">
        <button class="mini" @click="cancelEdit">取消</button>
        <button class="mini primary" :disabled="!draft.trim()" @click="submitEdit">
          {{ msg.role === 'assistant' ? '定稿这一轮' : '重发这一轮' }}
        </button>
      </div>
    </div>
    <template v-else>
      <div v-if="visibleBlocks.length > 0" class="blocks">
        <section
          v-for="item in visibleBlocks"
          :key="item.name"
          class="block"
          :class="'block-' + item.name"
        >
          <p class="block-body">{{ item.text }}</p>
        </section>
      </div>
      <div v-else class="body">{{ msg.content }}</div>
    </template>
    <div v-if="msg.interrupted && !msg.error" class="status interrupted">流已中断</div>

    <!-- 错误与恢复入口 -->
    <div v-if="msg.error" class="error-box">
      <span class="code">{{ msg.error.code }}</span>
      <span class="message">{{ msg.error.message }}</span>
      <div class="actions">
        <button v-if="msg.error.retryable" class="btn" @click="chat.retry()">
          <RotateCcw :size="13" /> 重试
        </button>
        <button class="btn" @click="chat.continueWrite()">
          <Play :size="13" /> 续写
        </button>
      </div>
    </div>

    <!-- 修改与重新生成入口：每一轮都能改，改写用户输入是分叉 -->
    <div v-if="canRevise || canRegenerate || canEditReply" class="turn-actions">
      <button
        v-if="canRevise"
        class="mini"
        title="修改这条输入并重跑这一轮"
        @click="startEdit"
      >
        <Pencil :size="12" /> 修改
      </button>
      <button
        v-if="canEditReply"
        class="mini"
        title="改这一轮回复的动作、场景、对白与状态，改完定稿"
        @click="startEdit"
      >
        <Pencil :size="12" /> 修改
      </button>
      <button
        v-if="canRegenerate"
        class="mini"
        title="用同样的输入再答一次"
        @click="onRegenerate"
      >
        <RotateCcw :size="12" /> 重新生成
      </button>
    </div>
  </div>
</template>

<style scoped>
.bubble {
  max-width: 78%;
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  padding: 8px 12px;
  background: var(--bg-1);
}
.bubble.user {
  align-self: flex-end;
  background: var(--accent-soft);
  border-color: transparent;
}
.bubble.assistant {
  align-self: flex-start;
}
.head {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 4px;
}
.badge {
  font-size: 11px;
  color: var(--text-2);
  font-family: var(--font-mono);
}
.rate {
  margin-left: auto;
  font-size: 11px;
  color: var(--text-3);
  font-family: var(--font-mono);
}
.body {
  white-space: pre-wrap;
  word-break: break-word;
}
.blocks {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.block {
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.block-body {
  margin: 0;
  white-space: pre-wrap;
  word-break: break-word;
}
/* 动作与场景是叙事，衬线体；对白是角色说话，楷体 */
.block-action .block-body,
.block-scene .block-body {
  font-family: var(--font-scene);
  line-height: 1.75;
}
.block-dialogue .block-body {
  font-family: var(--font-dialogue);
  line-height: 1.7;
}
.reasoning {
  margin: 4px 0 6px;
  border: 1px dashed var(--border);
  border-radius: var(--radius);
  background: var(--bg-0);
}
.fold {
  display: flex;
  align-items: center;
  gap: 6px;
  width: 100%;
  padding: 5px 8px;
  color: var(--text-2);
  font-size: 12px;
}
.reasoning-body {
  margin: 0;
  padding: 0 10px 8px;
  color: var(--text-2);
  font-size: 12px;
  white-space: pre-wrap;
  word-break: break-word;
  font-family: inherit;
}
.status.interrupted {
  margin-top: 6px;
  color: var(--level-warn);
  font-size: 12px;
}
.error-box {
  margin-top: 8px;
  border: 1px solid var(--level-danger);
  background: var(--danger-soft);
  border-radius: var(--radius);
  padding: 6px 8px;
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.error-box .code {
  color: var(--level-danger);
  font-family: var(--font-mono);
  font-size: 11px;
}
.error-box .message {
  color: var(--text-1);
  font-size: 12px;
}
.actions {
  display: flex;
  gap: 8px;
  margin-top: 2px;
}
.btn {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  color: var(--accent);
  font-size: 12px;
  border: 1px solid var(--accent);
  border-radius: var(--radius);
  padding: 2px 8px;
}
.btn:hover {
  background: var(--accent-soft);
}
.turn-actions {
  display: flex;
  gap: 6px;
  margin-top: 6px;
  justify-content: flex-end;
}
.mini {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  padding: 1px 7px;
  font-size: 11px;
  color: var(--text-3);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  background: transparent;
}
.mini:hover:not(:disabled) {
  color: var(--accent);
  border-color: var(--accent);
}
.mini.primary {
  color: var(--accent);
  border-color: var(--accent);
}
.mini:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
.edit-box {
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin-top: 4px;
}
.edit-input {
  width: 100%;
  min-height: 60px;
  resize: vertical;
  padding: 6px 8px;
  font-family: inherit;
  font-size: 13px;
  line-height: 1.6;
  color: var(--text-1);
  background: var(--bg-0);
  border: 1px solid var(--accent);
  border-radius: var(--radius);
}
.edit-actions {
  display: flex;
  gap: 6px;
  justify-content: flex-end;
}
</style>
