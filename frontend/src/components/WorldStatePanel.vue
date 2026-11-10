<script setup lang="ts">
// 世界状态面板：分两组结构化展示。人物组是名称、位置、随身道具与场景道具，
// 世界组是当前场景、地图与时间。状态由后端解析状态块并落盘，
// 人物名称由后端按角色卡带出，前端只显示，不做二次解析。
import { computed, ref } from 'vue'
import { Globe, TriangleAlert, User } from 'lucide-vue-next'
import { createWorldEntry } from '../api/worldbooks'
import type { WorldState } from '../types/chat'

const props = defineProps<{
  state: WorldState | null
  /** 被过滤掉的场景陈设：模型编的，不是角色卡里的 */
  dropped?: string[]
  /** 写入世界书的目标书籍 */
  bookId?: string
}>()

const saved = ref<string[]>([])
const busyItem = ref('')
const note = ref('')

// 写入世界书后这条陈设就有依据了，下次不再被过滤。
const pending = computed<string[]>(() =>
  (props.dropped ?? []).filter((item) => !saved.value.includes(item)),
)

async function keep(item: string): Promise<void> {
  if (!props.bookId) {
    note.value = '这个会话没有绑定世界书，先在卡片里写下这件陈设'
    return
  }
  busyItem.value = item
  note.value = ''
  try {
    await createWorldEntry(props.bookId, {
      keys: [item],
      content: '场景陈设：' + item,
      priority: 10,
      position: 'after',
      constant: false,
      selective: false,
      recursion: 0,
    })
    saved.value = [...saved.value, item]
  } catch (err) {
    note.value = err instanceof Error ? err.message : String(err)
  } finally {
    busyItem.value = ''
  }
}

const items = computed<string[]>(() => props.state?.items ?? [])
const sceneItems = computed<string[]>(() => props.state?.scene_items ?? [])

const empty = computed<boolean>(() => {
  const s = props.state
  if (!s) return true
  return (
    !s.location &&
    !s.position &&
    !s.map &&
    !s.time &&
    s.items.length === 0 &&
    s.scene_items.length === 0
  )
})

const updatedText = computed<string>(() => {
  const raw = props.state?.updated_at
  if (!raw) return ''
  return raw.replace('T', ' ').replace(/\+00:00$/, ' UTC')
})
</script>

<template>
  <section class="state" aria-label="世界状态">
    <h3 class="title">世界状态</h3>
    <p v-if="empty" class="empty">还没有状态。发一条消息，模型会随回合自动更新。</p>
    <template v-else>
      <dl class="group">
        <p class="group-title"><User :size="12" /> 人物</p>
        <div class="row">
          <dt>名称</dt>
          <dd>{{ state?.character_name || '—' }}</dd>
        </div>
        <div class="row">
          <dt>位置</dt>
          <dd>{{ state?.position || '—' }}</dd>
        </div>
        <div class="row">
          <dt>道具</dt>
          <dd>
            <template v-if="items.length === 0">
              <span class="none">—</span>
            </template>
            <template v-else>
              <span v-for="item in items" :key="item" class="chip">{{ item }}</span>
            </template>
          </dd>
        </div>
        <div class="row">
          <dt>场景道具</dt>
          <dd>
            <template v-if="sceneItems.length === 0">
              <span class="none">—</span>
            </template>
            <template v-else>
              <span v-for="item in sceneItems" :key="item" class="chip">{{ item }}</span>
            </template>
          </dd>
        </div>
      </dl>
      <dl class="group">
        <p class="group-title"><Globe :size="12" /> 世界</p>
        <div class="row">
          <dt>当前场景</dt>
          <dd>{{ state?.location || '—' }}</dd>
        </div>
        <div class="row">
          <dt>地图</dt>
          <dd>{{ state?.map || '—' }}</dd>
        </div>
        <div class="row">
          <dt>时间</dt>
          <dd>{{ state?.time || '—' }}</dd>
        </div>
      </dl>
      <p v-if="updatedText" class="updated">更新于 {{ updatedText }}</p>
    </template>

    <div v-if="pending.length" class="dropped">
      <p class="dropped-head">
        <TriangleAlert :size="12" />
        已忽略的陈设
      </p>
      <p class="dropped-note">
        模型自己编出来的陈设，按角色卡判定后不写进状态。确认要留就写进世界书。
      </p>
      <div class="dropped-list">
        <span v-for="item in pending" :key="item" class="dropped-item">
          {{ item }}
          <button
            class="keep"
            :disabled="busyItem === item"
            @click="keep(item)"
          >
            写入世界书
          </button>
        </span>
      </div>
      <p v-if="note" class="dropped-msg">{{ note }}</p>
    </div>
  </section>
</template>

<style scoped>
.state {
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
.empty {
  margin: 0;
  color: var(--text-3);
  font-size: 12px;
  line-height: 1.6;
}
.group {
  margin: 0 0 10px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.group:last-of-type {
  margin-bottom: 0;
}
.group-title {
  margin: 0;
  display: flex;
  align-items: center;
  gap: 4px;
  color: var(--text-2);
  font-size: 11px;
}
.row {
  display: flex;
  flex-direction: column;
  gap: 3px;
}
.row dt {
  display: flex;
  align-items: center;
  gap: 5px;
  color: var(--text-3);
  font-size: 11px;
}
.row dd {
  margin: 0;
  color: var(--text-1);
  font-size: 13px;
  line-height: 1.5;
  word-break: break-word;
}
.chip {
  display: inline-block;
  margin: 0 4px 4px 0;
  padding: 1px 8px;
  border-radius: var(--radius);
  background: var(--bg-0);
  border: 1px solid var(--border);
  color: var(--text-1);
  font-size: 12px;
}
.none {
  color: var(--text-3);
}
.updated {
  margin: 8px 0 0;
  padding-top: 6px;
  border-top: 1px dashed var(--border);
  color: var(--text-3);
  font-size: 11px;
}
.dropped {
  margin-top: 10px;
  padding-top: 8px;
  border-top: 1px dashed var(--border);
}
.dropped-head {
  margin: 0;
  display: flex;
  align-items: center;
  gap: 4px;
  color: var(--text-2);
  font-size: 12px;
}
.dropped-note {
  margin: 4px 0 6px;
  color: var(--text-3);
  font-size: 11px;
  line-height: 1.6;
}
.dropped-list {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
.dropped-item {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 2px 8px;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  background: var(--bg-0);
  color: var(--text-1);
  font-size: 12px;
}
.keep {
  padding: 1px 6px;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  background: var(--bg-2);
  color: var(--text-2);
  font-size: 11px;
}
.keep:hover:not(:disabled) {
  border-color: var(--accent);
  color: var(--accent);
}
.keep:disabled {
  opacity: 0.5;
}
.dropped-msg {
  margin: 6px 0 0;
  color: var(--level-danger);
  font-size: 11px;
}
/* PC 端：标签在左、值靠右对齐；移动端保持上下堆叠。断点与分支树一致。 */
@media (min-width: 768px) {
  .row {
    flex-direction: row;
    align-items: baseline;
    justify-content: space-between;
    gap: 20px;
  }
  .row dd {
    flex: 1;
    text-align: right;
  }
}
/* 侧边栏里容器窄，上下堆叠更稳。放在媒体查询之后：窄容器时覆盖上面那条。 */
@container (max-width: 339px) {
  .row {
    flex-direction: column;
    align-items: stretch;
    justify-content: flex-start;
    gap: 3px;
  }
  .row dd {
    flex: none;
    text-align: left;
  }
}
</style>
