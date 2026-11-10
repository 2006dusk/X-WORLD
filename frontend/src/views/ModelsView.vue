<script setup lang="ts">
// 模型档位页：列出内置档位与自建 profile，一键拉起或停掉本地推理服务。
// 8GB 显存装不下两个大档位并存，切档位是先停后起，切换期间要等权重加载。
import { onMounted, ref } from 'vue'
import { Cpu, Gauge, Play, RefreshCw, Square } from 'lucide-vue-next'
import {
  fetchInferenceStatus,
  fetchTiers,
  startTier,
  stopTier,
  type InferenceStatus,
  type ModelTier,
} from '../api/models'

const tiers = ref<ModelTier[]>([])
const status = ref<InferenceStatus | null>(null)
const busy = ref('')
const notice = ref('')
const errorText = ref('')

function measured(tier: ModelTier, key: string): string {
  const value = tier.measured?.[key]
  if (value === null || value === undefined || value === '') return '—'
  return String(value)
}

function sizeText(tier: ModelTier): string {
  if (tier.size_mb === null) return '—'
  return tier.size_mb >= 1024
    ? (tier.size_mb / 1024).toFixed(1) + ' GB'
    : tier.size_mb + ' MB'
}

async function load(): Promise<void> {
  errorText.value = ''
  try {
    const [list, state] = await Promise.all([fetchTiers(), fetchInferenceStatus()])
    tiers.value = list.items ?? []
    status.value = state
  } catch (err) {
    errorText.value = err instanceof Error ? err.message : String(err)
  }
}

async function start(tier: ModelTier): Promise<void> {
  busy.value = tier.profile_id
  errorText.value = ''
  notice.value = ''
  try {
    await startTier(tier.profile_id)
    notice.value = tier.name + ' 已启动，推理服务地址已切换'
    await load()
  } catch (err) {
    errorText.value = err instanceof Error ? err.message : String(err)
  } finally {
    busy.value = ''
  }
}

async function stop(tier: ModelTier): Promise<void> {
  busy.value = tier.profile_id
  errorText.value = ''
  notice.value = ''
  try {
    await stopTier(tier.profile_id)
    notice.value = tier.name + ' 已停止'
    await load()
  } catch (err) {
    errorText.value = err instanceof Error ? err.message : String(err)
  } finally {
    busy.value = ''
  }
}

onMounted(() => {
  void load()
})
</script>

<template>
  <div class="models">
    <header class="toolbar">
      <h2 class="heading">模型档位</h2>
      <button class="btn" @click="load">
        <RefreshCw :size="13" />
        刷新
      </button>
    </header>

    <p v-if="errorText" class="error">{{ errorText }}</p>
    <p v-if="notice" class="notice">{{ notice }}</p>

    <section class="current">
      <h3 class="title">当前推理服务</h3>
      <dl class="rows">
        <div class="row">
          <dt>地址</dt>
          <dd class="mono">{{ status?.service?.base_url ?? '—' }}</dd>
        </div>
        <div class="row">
          <dt>可达</dt>
          <dd>{{ status?.service?.reachable ? '是' : '否' }}</dd>
        </div>
        <div class="row">
          <dt>生效档位</dt>
          <dd>{{ (status?.profile?.name as string) ?? '—' }}</dd>
        </div>
      </dl>
      <p class="hint">
        同时只跑一个档位：8GB 显存装不下两个大模型并存。切档位会先停掉当前服务再拉起新的，
        首次加载要十几秒到一分钟。
      </p>
    </section>

    <div class="grid">
      <article v-for="tier in tiers" :key="tier.profile_id" class="tier" :class="{ on: tier.running }">
        <header class="tier-head">
          <Cpu :size="14" />
          <h3 class="tier-name">{{ tier.name }}</h3>
          <span v-if="tier.running" class="badge">运行中</span>
        </header>

        <p class="file mono">{{ tier.model_file }}</p>

        <dl class="rows">
          <div class="row">
            <dt>权重</dt>
            <dd>{{ sizeText(tier) }}</dd>
          </div>
          <div class="row">
            <dt>上下文</dt>
            <dd>{{ tier.n_ctx }}</dd>
          </div>
          <div class="row">
            <dt>KV 缓存</dt>
            <dd>{{ tier.kv_type }}</dd>
          </div>
          <div class="row">
            <dt><Gauge :size="12" /> 解码速率</dt>
            <dd>{{ measured(tier, 'decode_tok_per_s') }} tok/s</dd>
          </div>
          <div class="row">
            <dt>每轮耗时</dt>
            <dd>{{ measured(tier, 'turn_wait_s') }} s</dd>
          </div>
          <div class="row">
            <dt>回合字数</dt>
            <dd>{{ measured(tier, 'turn_chars') }}</dd>
          </div>
        </dl>

        <p v-if="measured(tier, 'note') !== '—'" class="note">{{ measured(tier, 'note') }}</p>
        <p v-if="!tier.model_exists" class="warn">权重文件不在本机：{{ tier.model_path }}</p>

        <div class="actions">
          <button
            class="primary"
            :disabled="busy === tier.profile_id || tier.running || !tier.model_exists"
            @click="start(tier)"
          >
            <Play :size="13" />
            {{ tier.running ? '已生效' : '启动并切换' }}
          </button>
          <button class="ghost" :disabled="busy === tier.profile_id || !tier.running" @click="stop(tier)">
            <Square :size="13" />
            停止
          </button>
        </div>
      </article>
    </div>
  </div>
</template>

<style scoped>
.models {
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
  justify-content: space-between;
}
.heading {
  margin: 0;
  font-size: 13px;
  color: var(--text-2);
  text-transform: uppercase;
  letter-spacing: 0.05em;
}
.btn {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 5px 10px;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  background: var(--bg-1);
  color: var(--text-2);
  font-size: 12px;
}
.btn:hover {
  background: var(--bg-2);
  color: var(--text-1);
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
.current {
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
.rows {
  margin: 0;
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.row {
  display: flex;
  gap: 8px;
  align-items: baseline;
}
.row dt {
  flex: 0 0 84px;
  display: flex;
  align-items: center;
  gap: 4px;
  color: var(--text-3);
  font-size: 11px;
}
.row dd {
  margin: 0;
  color: var(--text-1);
  font-size: 12px;
  word-break: break-all;
}
.hint {
  margin: 8px 0 0;
  color: var(--text-3);
  font-size: 11px;
  line-height: 1.6;
}
.grid {
  display: grid;
  /* min() 兜底：窄屏时列宽不超过容器，不会撑出横向滚动条 */
  grid-template-columns: repeat(auto-fill, minmax(min(300px, 100%), 1fr));
  gap: 8px;
}
.tier {
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  background: var(--bg-1);
  padding: 10px 12px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.tier.on {
  border-color: var(--accent);
}
.tier-head {
  display: flex;
  align-items: center;
  gap: 6px;
  color: var(--text-2);
}
.tier-name {
  margin: 0;
  font-size: 13px;
  color: var(--text-1);
  font-weight: 600;
}
.badge {
  margin-left: auto;
  padding: 1px 8px;
  border-radius: var(--radius);
  background: var(--accent-soft);
  color: var(--accent);
  font-size: 11px;
}
.file {
  margin: 0;
  font-size: 11px;
  color: var(--text-3);
  word-break: break-all;
}
.note {
  margin: 0;
  font-size: 11px;
  color: var(--text-3);
  line-height: 1.6;
}
.warn {
  margin: 0;
  font-size: 11px;
  color: var(--level-danger);
  line-height: 1.6;
}
.actions {
  display: flex;
  gap: 6px;
  margin-top: auto;
}
.primary,
.ghost {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 4px 10px;
  border-radius: var(--radius);
  font-size: 12px;
  border: 1px solid var(--border);
  background: var(--bg-2);
  color: var(--text-2);
}
.primary {
  background: var(--accent-soft);
  color: var(--accent);
  border-color: transparent;
}
.primary:hover:not(:disabled),
.ghost:hover:not(:disabled) {
  border-color: var(--accent);
  color: var(--accent);
}
.primary:disabled,
.ghost:disabled {
  opacity: 0.5;
}
</style>
