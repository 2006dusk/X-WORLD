<!-- 对话视图：一段会话一个页面。
     会话由路由参数决定（?session=<id>），历史与场景都留在这一段对话里。
     版式是单列加一条顶栏：顶栏只放两个互斥的开关（状态、分支），
     世界状态与分支树跟着开关出来，一次只显示一块，不挤占对话流。 -->
<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { ArrowUp, Camera, GitBranch, MapPin, Play, Square } from 'lucide-vue-next'
import { useChatStore } from '../stores/chat'
import MessageBubble from '../components/MessageBubble.vue'
import ContextRing from '../components/ContextRing.vue'
import ContextWindowPanel from '../components/ContextWindowPanel.vue'
import WorldStatePanel from '../components/WorldStatePanel.vue'
import BranchTreeCard from '../components/BranchTreeCard.vue'
import { contextUsage } from '../lib/contextWindow'

const chat = useChatStore()
const route = useRoute()
const input = ref('')
const listEl = ref<HTMLElement | null>(null)
/** 浮层显示哪一块：现在只有上下文窗口，空串表示收起。 */
const panel = ref<'context' | ''>('')
/** 输入框容器：浮层从它上边缘往上弹出，点它以外的地方收起。 */
const composerBoxEl = ref<HTMLElement | null>(null)
/** 右上角开关开着哪一块：世界状态或分支树，一次只显示一块，空串表示都收起。 */
const corner = ref<'state' | 'tree' | ''>('')

const sessionId = computed(() => String(route.query.session ?? ''))

async function scrollBottom(): Promise<void> {
  await nextTick()
  const el = listEl.value
  if (el) el.scrollTop = el.scrollHeight
}

watch(
  () => chat.messages.length,
  () => void scrollBottom(),
)
watch(
  () => chat.messages[chat.messages.length - 1]?.content,
  () => void scrollBottom(),
)

async function openSession(id: string): Promise<void> {
  if (!id) return
  await chat.openSession(id)
  const wanted = String(route.query.character ?? '')
  if (wanted && chat.xw.character_id !== wanted) {
    await chat.setSpeaker(wanted)
  }
}

/** 上下文窗口占用：圆环与二级弹窗都用它。 */
const ctxUsage = computed(() =>
  contextUsage(
    chat.lastUsage?.budget ?? null,
    chat.contextReport,
    chat.xw.max_context_tokens,
  ),
)

/** 圆环的悬停说明：把三个数一次说清。 */
const ctxLabel = computed(() => {
  const usage = ctxUsage.value
  if (!usage.limit) return '上下文窗口：等待装配回执'
  const parts = ['已用 ' + usage.used, '上限 ' + usage.limit]
  if (usage.reserved > 0) parts.splice(1, 0, '预留 ' + usage.reserved)
  return '上下文窗口：' + parts.join(' / ')
})

/** 右上角的两个开关互斥：点一个开一块，再点它自己收起，点另一个换过去。 */
function toggleCorner(name: 'state' | 'tree'): void {
  corner.value = corner.value === name ? '' : name
}

/** 浮层：上下文窗口的二级弹窗，点同一个按钮收起。再点输入框以外的地方也收起。 */
function togglePanel(name: 'context'): void {
  panel.value = panel.value === name ? '' : name
}

/** 点浮层以外的地方就收起浮层。 */
function onOutside(event: MouseEvent): void {
  const box = composerBoxEl.value
  const target = event.target as Node | null
  if (box && target && box.contains(target)) return
  panel.value = ''
}

watch(panel, (value) => {
  if (value) {
    document.addEventListener('mousedown', onOutside)
  } else {
    document.removeEventListener('mousedown', onOutside)
  }
})

onUnmounted(() => {
  document.removeEventListener('mousedown', onOutside)
})

async function onSend(): Promise<void> {
  const text = input.value
  input.value = ''
  await chat.send(text)
}

/**
 * 自动回合：用户这一轮不说话，由 AI 自己往下走。
 *
 * continue 让 AI 接住自己上一轮的话继续推进；describe 只推进场景描写。
 * 输入框里已写的内容这一轮不参与：这一轮按用户没说话处理。
 */
async function onAutoTurn(mode: 'continue' | 'describe'): Promise<void> {
  await chat.autoTurn(mode)
}

watch(
  () => route.query.session,
  (id) => {
    const value = String(id ?? '')
    panel.value = ''
    if (value) {
      if (value !== chat.session) void openSession(value)
      return
    }
    // 没选会话：清掉上一段对话的残留，状态面板不留旧信息
    chat.clearSession()
  },
)

onMounted(() => {
  if (sessionId.value) {
    void openSession(sessionId.value)
  } else {
    chat.clearSession()
  }
})
</script>

<template>
  <div class="chat">
    <div class="stream">
      <!-- 顶栏：只放两个互斥的开关 -->
      <div class="topbar">
        <button
          class="corner-tab"
          :class="{ on: corner === 'state' }"
          title="世界状态"
          @click="toggleCorner('state')"
        >
          <MapPin :size="13" />
          状态
        </button>
        <button
          class="corner-tab"
          :class="{ on: corner === 'tree' }"
          title="分支树"
          @click="toggleCorner('tree')"
        >
          <GitBranch :size="13" />
          分支
        </button>
      </div>

      <div ref="listEl" class="list" aria-label="消息流">
        <p v-if="!sessionId" class="hint">
          还没有选中会话。从会话列表里挑一段，或新建一段对话。
        </p>
        <p v-else-if="chat.messages.length === 0" class="hint">
          这段对话还是空的。说点什么开始。
        </p>
        <MessageBubble v-for="m in chat.messages" :key="m.id" :msg="m" />
        <p v-if="chat.pendingContextNotice" class="pending">装配中，等待 context 事件…</p>
      </div>

      <div class="composer">
        <div ref="composerBoxEl" class="composer-box">
          <textarea
            v-model="input"
            class="input"
            rows="2"
            placeholder="说点什么…"
            @keydown.enter.exact.prevent="onSend"
          />
          <div class="composer-row">
            <div class="tools-left">
              <!-- 上下文窗口圆环：点开二级弹窗看占用率与分段 -->
              <button
                class="ctx-btn"
                :title="ctxLabel"
                @click="togglePanel('context')"
              >
                <ContextRing :pct="ctxUsage.pct" :label="ctxLabel" />
              </button>
            </div>
            <!-- 发送键单独一格：窄屏时它跟工具行分开，贴着输入框右下角 -->
            <div class="send-slot">
              <button
                v-if="chat.busy"
                class="send-btn stop"
                title="停止"
                @click="chat.stop()"
              >
                <Square :size="13" />
                停止
              </button>
              <button
                v-else
                class="send-btn"
                :title="sessionId ? '发送' : '先选一段对话或新建对话'"
                :disabled="input.trim().length === 0 || !sessionId"
                @click="onSend"
              >
                <ArrowUp :size="14" />
                发送
              </button>
            </div>
            <!-- 自动回合：这一轮用户不说话，由 AI 自己往下走 -->
            <div class="tools">
              <button
                class="auto-btn"
                title="对话推进：AI 接住自己上一轮的话继续说（这一轮你不说话）"
                :disabled="!sessionId || chat.busy"
                @click="onAutoTurn('continue')"
              >
                <Play :size="13" />
                对话推进
              </button>
              <button
                class="auto-btn"
                title="场景推进：这一轮只推进场景与环境的描写（你不说话）"
                :disabled="!sessionId || chat.busy"
                @click="onAutoTurn('describe')"
              >
                <Camera :size="13" />
                场景推进
              </button>
            </div>
          </div>

          <!-- 上下文窗口：二级浮层，从输入框上方浮起的小卡片 -->
          <div v-if="panel === 'context'" class="panel-pop">
            <ContextWindowPanel
              :usage="ctxUsage"
              :report="chat.contextReport"
            />
          </div>
        </div>
      </div>
    </div>

    <!-- 浮层卡片：跟着开关出来，悬浮在对话区上方 -->
    <div v-if="corner === 'state'" class="corner-card">
      <WorldStatePanel
        :state="chat.worldState"
        :dropped="chat.droppedItems"
        :book-id="chat.xw.world_book_ids[0] ?? ''"
      />
    </div>
    <div v-else-if="corner === 'tree'" class="corner-card tree">
      <BranchTreeCard :tree="chat.tree" :head-id="chat.headId" />
    </div>
  </div>
</template>

<style scoped>
.chat {
  position: relative;
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
}
.stream {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-height: 0;
  min-width: 0;
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  background: var(--bg-1);
  overflow: visible;
  /* 容器查询的基准：窄屏时对话区内部按自己的宽度重排，不看视口 */
  container-type: inline-size;
}
.list {
  flex: 1;
  overflow-y: auto;
  padding: 14px;
  border-radius: 0;
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.hint {
  color: var(--text-3);
  font-size: 13px;
  text-align: center;
  margin-top: 24px;
}
.pending {
  color: var(--text-3);
  font-size: 12px;
  text-align: center;
}
.composer {
  position: relative;
  /* 安全区留给手势条；软键盘弹出时底部由 --app-h 让开 */
  padding: 10px 12px calc(12px + env(safe-area-inset-bottom));
  border-top: 1px solid var(--border);
  background: var(--bg-2);
  border-radius: 0 0 var(--radius-lg) var(--radius-lg);
}
.composer-box {
  position: relative;
  border: 1px solid var(--border);
  border-radius: 16px;
  background: var(--bg-0);
  padding: 10px 12px 8px;
  transition: border-color 0.15s ease;
}
.composer-box:focus-within {
  border-color: var(--accent);
}
.input {
  width: 100%;
  min-height: 52px;
  resize: none;
  background: transparent;
  border: none;
  border-radius: 0;
  padding: 0;
  font-family: inherit;
  font-size: 14px;
  line-height: 1.6;
  color: var(--text-1);
}
.input:focus {
  border: none;
}
.composer-row {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 4px;
}
.tools-left {
  order: 0;
  display: flex;
  align-items: center;
  gap: 8px;
}
/* 发送键单独一格：宽屏时靠右，与自动回合按钮排成一组 */
.send-slot {
  order: 1;
  margin-left: auto;
  display: flex;
  align-items: center;
}
/* 圆环按钮：只留环本身。不要方框底色，也不要点击特效——
 * 环自己就是状态：弧线的颜色表示上下文用了多少。 */
.ctx-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 26px;
  height: 26px;
  padding: 0;
  border: none;
  border-radius: 50%;
  background: transparent;
}
.tools {
  order: 2;
  display: flex;
  align-items: center;
  gap: 8px;
}
.ghost {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 4px 10px;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  color: var(--text-2);
  font-size: 12px;
  /* 不许被 flex 压窄：窄屏上压窄了文字会按字竖排 */
  white-space: nowrap;
  flex-shrink: 0;
}
.ghost:hover {
  background: var(--bg-2);
  color: var(--text-1);
}
.ghost.on {
  border-color: var(--accent);
  color: var(--accent);
  background: var(--accent-soft);
}
/* 浮层：输入框上方浮起的小卡片，与输入框留一段距离，层级高于对话区 */
.panel-pop {
  position: absolute;
  left: 12px;
  right: auto;
  width: min(420px, calc(100% - 24px));
  bottom: calc(100% + 14px);
  z-index: 40;
  max-height: min(calc(var(--app-h) * 0.4), 360px);
  overflow: auto;
  border-radius: var(--radius-lg);
  box-shadow: 0 20px 50px #00000073, 0 2px 6px #0000001f;
  animation: panel-in 0.16s ease-out;
}
@keyframes panel-in {
  from {
    opacity: 0;
    transform: translateY(8px) scale(0.98);
  }
  to {
    opacity: 1;
    transform: none;
  }
}
@media (prefers-reduced-motion: reduce) {
  .panel-pop {
    animation: none;
  }
}
/* 顶栏：只放两个互斥的开关，别的不占地方 */
.topbar {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 8px;
  height: 38px;
  padding: 0 12px;
  border-bottom: 1px solid var(--border);
  background: var(--bg-2);
  border-radius: var(--radius-lg) var(--radius-lg) 0 0;
}
.corner-tab {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 4px 10px;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  background: transparent;
  color: var(--text-2);
  font-size: 12px;
}
.corner-tab:hover {
  background: var(--bg-0);
  color: var(--text-1);
}
.corner-tab.on {
  border-color: var(--accent);
  color: var(--accent);
  background: var(--accent-soft);
}
/* 浮层卡片：顶栏下面，悬浮在对话区上方 */
.corner-card {
  position: absolute;
  top: 48px;
  right: 12px;
  z-index: 44;
  width: 300px;
  max-height: min(calc(var(--app-h) * 0.58), 520px);
  overflow: auto;
  border-radius: var(--radius-lg);
  box-shadow: 0 18px 44px #00000066;
  animation: corner-in 0.16s ease-out;
  container-type: inline-size;
}
/* 树里有固定定位的悬停提示，动画不做变换 */
.corner-card.tree {
  animation-name: corner-fade;
}
@keyframes corner-in {
  from {
    opacity: 0;
    transform: translateY(-6px) scale(0.98);
  }
  to {
    opacity: 1;
    transform: none;
  }
}
@keyframes corner-fade {
  from {
    opacity: 0;
  }
  to {
    opacity: 1;
  }
}
@media (prefers-reduced-motion: reduce) {
  .corner-card {
    animation: none;
  }
}
.auto-btn {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  height: 30px;
  padding: 0 12px;
  border-radius: 15px;
  border: 1px solid var(--border);
  background: transparent;
  color: var(--text-2);
  font-size: 12px;
  white-space: nowrap;
}
.auto-btn:hover:not(:disabled) {
  border-color: var(--accent);
  color: var(--accent);
}
.auto-btn:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}
.send-btn {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  height: 30px;
  padding: 0 14px;
  border-radius: 15px;
  border: none;
  background: var(--accent);
  color: #ffffff;
  font-size: 12px;
  white-space: nowrap;
}
.send-btn:hover:not(:disabled) {
  filter: brightness(1.12);
}
.send-btn:disabled {
  background: var(--bg-2);
  color: var(--text-3);
  cursor: not-allowed;
}
.send-btn.stop {
  background: var(--level-danger);
  color: #ffffff;
}
.send-btn.stop:hover {
  background: var(--level-danger);
  filter: brightness(1.12);
}

/* 窄容器：对话区内部按自己的宽度重排。
 * 输入区的控件排一行，二级弹窗贴着输入框铺开，内容自己滚。 */
@container (max-width: 460px) {
  /* 窄屏：五个控件排一行。360px 宽的机器上原本放不下，靠自动换行把三个
   * 动作按钮挤到第二行还右对齐，看着像散架。现在收紧内边距与间距，
   * 两个自动回合按钮的图标让位给文字——图标是装饰，文字才是标签。 */
  .composer-row {
    flex-wrap: nowrap;
    gap: 5px;
  }
  .tools-left {
    gap: 5px;
  }
  .tools {
    gap: 5px;
  }
  .ghost {
    height: 40px;
    padding: 0 6px;
    gap: 4px;
    flex-shrink: 0;
  }
  .send-btn {
    height: 40px;
    padding: 0 8px;
    gap: 4px;
    border-radius: 20px;
    flex-shrink: 0;
  }
  .auto-btn {
    height: 40px;
    padding: 0 7px;
    border-radius: 20px;
    flex-shrink: 0;
  }
  .auto-btn svg {
    display: none;
  }
  /* 圆环收窄到 22px，不占横向地方；触控范围另外撑到 40px */
  .ctx-btn {
    position: relative;
    width: 22px;
    height: 22px;
  }
  .ctx-btn::after {
    content: "";
    position: absolute;
    inset: -9px;
  }
  /* 二级浮层贴着输入框铺开，内容自己滚 */
  .panel-pop {
    left: 8px;
    right: 8px;
    width: auto;
    max-height: min(calc(var(--app-h) * 0.42), 320px);
  }
  .list {
    padding: 10px;
  }
}

/* 极窄屏（<340px）：一行确实放不下了，允许换行，别把按钮挤出边界 */
@container (max-width: 340px) {
  .composer-row {
    flex-wrap: wrap;
    row-gap: 6px;
  }
  .tools {
    width: 100%;
    justify-content: flex-end;
  }
}

/* 窄屏视口：浮层贴边铺开，不超出视口；顶栏按钮是 40px 高的触控目标。 */
@media (max-width: 768px) {
  .corner-card {
    left: 8px;
    right: 8px;
    width: auto;
    max-width: 420px;
    top: 56px;
  }
  .topbar {
    height: auto;
    min-height: 38px;
    padding: 4px 10px;
  }
}
</style>
