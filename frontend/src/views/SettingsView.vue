<script setup lang="ts">
// 设置视图：按后端 settings.json 的键逐项读写。
// 键清单以接口返回的 keys 为准，编辑值只做类型转换，不在前端另立语义。
import { computed, onMounted, reactive, ref } from 'vue'
import { Save } from 'lucide-vue-next'
import { fetchSettings, putSetting } from '../api/settings'
import { fetchPreset, fetchPresets, type Preset } from '../api/presets'
import {
  checkConformance,
  deleteVoice,
  fetchVoices,
  generateVoice,
  putVoice,
  type ConformanceLayer,
  type VoiceItem,
  type VoiceVector,
} from '../api/voices'

type FieldKind = 'text' | 'number' | 'select' | 'boolean' | 'textarea'

interface FieldSpec {
  key: string
  label: string
  kind: FieldKind
  options?: string[]
  hint?: string
}

const FIELDS: FieldSpec[] = [
  { key: 'inference_url', label: '推理服务地址', kind: 'text', hint: '本地 llama.cpp 兼容服务根地址（默认自写服务端 world-work-cpp）' },
  { key: 'active_model', label: '激活模型', kind: 'text' },
  { key: 'active_profile', label: '激活 Profile', kind: 'text' },
  {
    key: 'persona',
    label: '用户档案',
    kind: 'textarea',
    hint: '对面是谁：称呼、身份、出现在这个故事里的理由。装进 persona 段给模型看，缺了它模型会替用户发挥',
  },
  { key: 'default_character_id', label: '默认角色卡', kind: 'text' },
  { key: 'default_preset_id', label: '默认预设', kind: 'text' },
  {
    key: 'memory_mode',
    label: '记忆模式',
    kind: 'select',
    options: ['none', 'summary', 'vector', 'hybrid'],
  },
  { key: 'max_context_tokens', label: '上下文上限', kind: 'number' },
  { key: 'reserved_output_tokens', label: '预留输出 token', kind: 'number' },
  { key: 'bm25_weight', label: 'BM25 权重', kind: 'number' },
  { key: 'vector_weight', label: '向量权重', kind: 'number' },
  { key: 'audit_enabled', label: '审计日志', kind: 'boolean' },
  { key: 'theme', label: '主题', kind: 'select', options: ['dark', 'light'] },
]

const drafts = reactive<Record<string, string>>({})
const flags = reactive<Record<string, boolean>>({})
const knownKeys = ref<string[]>([])
const busyKey = ref('')
const notice = ref('')
const errorText = ref('')
/** 内置 RP 预设：后端按版本刷新，用户改不动，这里只读展示。 */
const builtin = ref<Preset | null>(null)
const builtinBusy = ref(false)

/** 内置预设是不是当前默认预设。 */
const isBuiltinDefault = computed(
  () => Boolean(builtin.value?.id) && drafts.default_preset_id === builtin.value?.id,
)

function toText(value: unknown): string {
  if (value === null || value === undefined) return ''
  if (typeof value === 'string') return value
  if (typeof value === 'number' || typeof value === 'boolean') return String(value)
  return JSON.stringify(value)
}

function applyValues(items: Record<string, unknown>): void {
  for (const spec of FIELDS) {
    const raw = items[spec.key]
    if (spec.kind === 'boolean') {
      flags[spec.key] = raw === true || raw === 'true'
    } else {
      drafts[spec.key] = toText(raw)
    }
  }
}

async function load(): Promise<void> {
  errorText.value = ''
  try {
    const res = await fetchSettings()
    knownKeys.value = res.keys ?? []
    applyValues(res.items ?? {})
  } catch (err) {
    errorText.value = err instanceof Error ? err.message : String(err)
  }
  await loadBuiltin()
}

/** 读内置 RP 预设。清单里带 builtin 标记的那条就是。 */
async function loadBuiltin(): Promise<void> {
  try {
    const res = await fetchPresets()
    const found = res.items.find((p) => p.builtin)
    builtin.value = found ? await fetchPreset(found.id) : null
  } catch {
    builtin.value = null
  }
}

/** 把默认预设指向内置 RP 预设。 */
async function useBuiltinAsDefault(): Promise<void> {
  const id = builtin.value?.id
  if (!id) return
  builtinBusy.value = true
  errorText.value = ''
  notice.value = ''
  try {
    await putSetting('default_preset_id', id)
    drafts.default_preset_id = id
    notice.value = '默认预设已指向内置 RP 预设'
  } catch (err) {
    errorText.value = err instanceof Error ? err.message : String(err)
  } finally {
    builtinBusy.value = false
  }
}

async function save(spec: FieldSpec): Promise<void> {
  busyKey.value = spec.key
  errorText.value = ''
  notice.value = ''
  let value: unknown
  if (spec.kind === 'boolean') {
    value = flags[spec.key] === true
  } else if (spec.kind === 'number') {
    const parsed = Number(drafts[spec.key])
    if (!Number.isFinite(parsed)) {
      errorText.value = `${spec.label} 需要数值`
      busyKey.value = ''
      return
    }
    value = parsed
  } else {
    value = drafts[spec.key] ?? ''
  }
  try {
    await putSetting(spec.key, value)
    notice.value = `${spec.label} 已保存`
  } catch (err) {
    errorText.value = err instanceof Error ? err.message : String(err)
  } finally {
    busyKey.value = ''
  }
}

/** 角色声线：每个角色可挂一个控制向量文件与剂量。 */
const voices = ref<VoiceItem[]>([])
const voiceVectors = ref<VoiceVector[]>([])
const voiceModel = ref<{ model_desc: string; expected_bytes: number | null } | null>(null)
const voiceMaxScale = ref(0.6)
const voiceDrafts = reactive<Record<string, { file: string; scale: number }>>({})
const voiceBusy = ref('')
const voiceNotice = ref('')
const voiceError = ref('')

/** 绝对路径取文件名：接口给的是向量文件的绝对路径。 */
function voiceFileName(path: string): string {
  const parts = String(path || '').split(/[\\/]/)
  return parts[parts.length - 1] || ''
}

/** 读声线清单，并给每个角色准备一份草稿。 */
async function loadVoices(): Promise<void> {
  voiceError.value = ''
  try {
    const res = await fetchVoices()
    voices.value = res.items ?? []
    voiceVectors.value = res.vectors ?? []
    voiceModel.value = res.model ?? null
    voiceMaxScale.value = res.max_scale ?? 0.6
    for (const item of voices.value) {
      if (voiceDrafts[item.character_id]) continue
      voiceDrafts[item.character_id] = {
        file: item.active ? voiceFileName(item.file) : '',
        scale: item.active ? item.scale : 0.3,
      }
    }
  } catch (err) {
    voiceError.value = err instanceof Error ? err.message : String(err)
  }
}

/** 保存角色的声线：文件留空就是摘掉。 */
async function saveVoice(item: VoiceItem): Promise<void> {
  const draft = voiceDrafts[item.character_id]
  if (!draft) return
  voiceBusy.value = item.character_id
  voiceError.value = ''
  voiceNotice.value = ''
  try {
    if (!draft.file) {
      await deleteVoice(item.character_id)
      voiceNotice.value = `${item.name} 的声线已摘掉`
    } else {
      await putVoice(item.character_id, draft.file, draft.scale)
      voiceNotice.value = `${item.name} 的声线已保存`
    }
    await loadVoices()
  } catch (err) {
    voiceError.value = err instanceof Error ? err.message : String(err)
  } finally {
    voiceBusy.value = ''
  }
}

/** 按角色卡现场生成声线：推理端抽残差差分，再扫一遍剂量取最优。 */
async function generateFor(item: VoiceItem): Promise<void> {
  voiceBusy.value = item.character_id
  voiceError.value = ''
  voiceNotice.value = `${item.name} 正在生成声线，要抽残差差分，等一会儿`
  try {
    const res = await generateVoice(item.character_id)
    const verdict = res.verify?.verdict
    const label =
      verdict === 'pass' ? '判定通过' : verdict === 'weak' ? '判定偏弱' : '判定未通过'
    voiceNotice.value = `${item.name} 声线已生成：${label}，剂量 ${res.scale}`
    await loadVoices()
  } catch (err) {
    voiceError.value = err instanceof Error ? err.message : String(err)
    voiceNotice.value = ''
  } finally {
    voiceBusy.value = ''
  }
}

/** 符合度检查的输入与结果。 */
const conformCharacter = ref('')
const conformText = ref('')
const conformBusy = ref(false)
const conformError = ref('')
const conformVerdict = ref('')
const conformLayers = ref<ConformanceLayer[]>([])

/** 判定一段话算不算这个角色说的：结构、身份、声线三层。 */
async function runConformance(): Promise<void> {
  if (!conformCharacter.value || !conformText.value.trim()) {
    conformError.value = '先选角色，再贴一段话'
    conformVerdict.value = ''
    conformLayers.value = []
    return
  }
  conformBusy.value = true
  conformError.value = ''
  try {
    const res = await checkConformance(conformCharacter.value, conformText.value)
    conformVerdict.value = res.verdict
    conformLayers.value = res.layers ?? []
    if (res.issues?.length) {
      conformError.value = res.issues.join('；')
    }
  } catch (err) {
    conformError.value = err instanceof Error ? err.message : String(err)
    conformVerdict.value = ''
    conformLayers.value = []
  } finally {
    conformBusy.value = false
  }
}

/** 摘掉角色的声线。 */
async function clearVoice(item: VoiceItem): Promise<void> {
  voiceBusy.value = item.character_id
  voiceError.value = ''
  voiceNotice.value = ''
  try {
    await deleteVoice(item.character_id)
    voiceNotice.value = `${item.name} 的声线已摘掉`
    await loadVoices()
  } catch (err) {
    voiceError.value = err instanceof Error ? err.message : String(err)
  } finally {
    voiceBusy.value = ''
  }
}

onMounted(() => {
  void load()
  void loadVoices()
})
</script>

<template>
  <div class="settings">
    <header class="toolbar">
      <h2 class="heading">设置</h2>
      <button class="btn" @click="load">重新读取</button>
    </header>

    <p v-if="errorText" class="error">{{ errorText }}</p>
    <p v-if="notice" class="notice">{{ notice }}</p>

    <div class="grid">
      <div v-for="spec in FIELDS" :key="spec.key" class="field">
        <div class="field-head">
          <label class="label" :for="`set-${spec.key}`">{{ spec.label }}</label>
          <span class="key mono">{{ spec.key }}</span>
        </div>

        <div class="control">
          <select
            v-if="spec.kind === 'select'"
            :id="`set-${spec.key}`"
            v-model="drafts[spec.key]"
            class="input"
          >
            <option v-for="opt in spec.options ?? []" :key="opt" :value="opt">{{ opt }}</option>
          </select>

          <label v-else-if="spec.kind === 'boolean'" class="switch">
            <input :id="`set-${spec.key}`" v-model="flags[spec.key]" type="checkbox" />
            <span>{{ flags[spec.key] ? '已开启' : '已关闭' }}</span>
          </label>

          <textarea
            v-else-if="spec.kind === 'textarea'"
            :id="`set-${spec.key}`"
            v-model="drafts[spec.key]"
            class="input area"
            rows="4"
          />

          <input
            v-else
            :id="`set-${spec.key}`"
            v-model="drafts[spec.key]"
            class="input"
            :type="spec.kind === 'number' ? 'number' : 'text'"
          />

          <button class="save" :disabled="busyKey === spec.key" @click="save(spec)">
            <Save :size="13" />
            保存
          </button>
        </div>

        <p v-if="spec.hint" class="hint">{{ spec.hint }}</p>
      </div>
    </div>

    <section class="builtin">
      <div class="builtin-head">
        <h3 class="title">内置 RP 预设</h3>
        <span v-if="builtin" class="key mono">v{{ builtin.builtin_version }}</span>
        <span v-if="isBuiltinDefault" class="tag">当前默认</span>
        <button
          class="btn"
          :disabled="builtinBusy || !builtin"
          @click="useBuiltinAsDefault"
        >
          设为默认预设
        </button>
      </div>

      <p v-if="!builtin" class="meta">还没有内置预设。后端启动时会按版本号播种。</p>
      <template v-else>
        <p class="meta">
          内置预设由后端按版本号刷新，改不动：改了下次启动会被覆盖。要保留改动，
          在预设页复制一份自建预设。
        </p>
        <p v-if="builtin.output_spec?.structure?.length" class="spec">
          回合结构：
          <span class="mono">{{ builtin.output_spec.structure.join(' / ') }}</span>
        </p>
        <div v-for="(group, index) in builtin.prompts ?? []" :key="index" class="prompt">
          <div class="prompt-head">
            <span class="prompt-name">{{ group.name }}</span>
            <span class="key mono">{{ group.role }}</span>
            <span class="key mono">order {{ group.order }}</span>
          </div>
          <pre class="prompt-body">{{ group.content }}</pre>
        </div>
        <p v-if="(builtin.prompts ?? []).length === 0" class="meta">该预设还没有提示组。</p>
      </template>
    </section>

    <section class="voices">
      <div class="builtin-head">
        <h3 class="title">角色声线</h3>
        <span v-if="voiceModel" class="key mono">{{ voiceModel.model_desc }}</span>
        <button class="btn" @click="loadVoices">重新读取</button>
      </div>

      <p class="meta">
        声线是角色的声线层：给角色挂一个控制向量文件与剂量，向量随请求下发，
        推理端按请求挂载，换角色就换向量，服务端全局不挂。
      </p>
      <p v-if="voiceError" class="error">{{ voiceError }}</p>
      <p v-if="voiceNotice" class="notice">{{ voiceNotice }}</p>

      <div v-for="item in voices" :key="item.character_id" class="voice-row">
        <div class="field-head">
          <label class="label">{{ item.name }}</label>
          <span class="key mono">{{ item.character_id }}</span>
        </div>
        <div class="control">
          <select
            v-if="voiceDrafts[item.character_id]"
            v-model="voiceDrafts[item.character_id].file"
            class="input"
          >
            <option value="">不挂声线</option>
            <option v-for="vec in voiceVectors" :key="vec.name" :value="vec.name">
              {{ vec.name }}{{ vec.compatible === false ? '（与当前模型不匹配）' : '' }}
            </option>
          </select>
          <input
            v-if="voiceDrafts[item.character_id]"
            v-model.number="voiceDrafts[item.character_id].scale"
            class="input dose"
            type="number"
            min="0"
            :max="voiceMaxScale"
            step="0.05"
          />
          <button
            class="save"
            :disabled="voiceBusy === item.character_id"
            @click="generateFor(item)"
          >
            生成
          </button>
          <button
            class="save"
            :disabled="voiceBusy === item.character_id"
            @click="saveVoice(item)"
          >
            保存
          </button>
          <button
            class="save"
            :disabled="voiceBusy === item.character_id"
            @click="clearVoice(item)"
          >
            清除
          </button>
        </div>
      </div>
      <p v-if="voices.length === 0" class="meta">还没有角色卡。</p>

      <div class="field-head">
        <label class="label">符合度检查</label>
      </div>
      <p class="meta">
        贴一段话，程序按三层判定它算不算这个角色说的：结构（成不成立）、
        身份（自称、串场、替用户行动）、声线（挂上向量后角色原话是否更可能出现）。
      </p>
      <div class="control">
        <select v-model="conformCharacter" class="input">
          <option value="">选角色</option>
          <option v-for="item in voices" :key="item.character_id" :value="item.character_id">
            {{ item.name }}
          </option>
        </select>
        <button class="save" :disabled="conformBusy" @click="runConformance">
          检查
        </button>
      </div>
      <textarea
        v-model="conformText"
        class="input"
        rows="4"
        placeholder="贴一段模型的回复，问它算不算这个角色说的"
      />
      <p v-if="conformVerdict" class="notice">
        总判定：{{ conformVerdict === 'pass' ? '通过' : conformVerdict === 'weak' ? '偏弱' : '未通过' }}
      </p>
      <ul v-if="conformLayers.length" class="meta">
        <li v-for="(layer, index) in conformLayers" :key="index">
          第 {{ index + 1 }} 层 {{ layer.verdict }}
          <span v-if="layer.issues?.length">：{{ layer.issues.join('；') }}</span>
        </li>
      </ul>
      <p v-if="conformError" class="error">{{ conformError }}</p>
    </section>

    <p v-if="knownKeys.length" class="keys mono">
      后端声明的设置键：{{ knownKeys.join('、') }}
    </p>
  </div>
</template>

<style scoped>
.settings {
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
.grid {
  display: grid;
  /* min() 兜底：窄屏时列宽不超过容器，不会撑出横向滚动条 */
  grid-template-columns: repeat(auto-fill, minmax(min(320px, 100%), 1fr));
  gap: 8px;
}
.field {
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  background: var(--bg-1);
  padding: 9px 11px;
}
.field-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 8px;
}
.label {
  font-size: 12px;
  color: var(--text-1);
}
.key {
  font-size: 11px;
  color: var(--text-3);
}
.control {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 6px;
}
.input {
  flex: 1;
  min-width: 0;
  background: var(--bg-0);
  font-size: 12px;
}
.area {
  resize: vertical;
  line-height: 1.6;
}
.switch {
  display: flex;
  align-items: center;
  gap: 6px;
  flex: 1;
  font-size: 12px;
  color: var(--text-2);
}
.save {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 4px 9px;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  background: var(--bg-2);
  color: var(--text-2);
  font-size: 12px;
}
.save:hover:not(:disabled) {
  border-color: var(--accent);
  color: var(--accent);
}
.save:disabled {
  opacity: 0.55;
}
.hint {
  margin: 5px 0 0;
  font-size: 11px;
  color: var(--text-3);
}
.keys {
  margin: 0;
  font-size: 11px;
  color: var(--text-3);
}
.builtin {
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  background: var(--bg-1);
  padding: 10px 12px;
}
.builtin-head {
  display: flex;
  align-items: center;
  gap: 8px;
}
.builtin-head .btn {
  margin-left: auto;
}
.builtin-head .btn:disabled {
  opacity: 0.55;
}
.title {
  margin: 0;
  font-size: 12px;
  color: var(--text-2);
  text-transform: uppercase;
  letter-spacing: 0.05em;
}
.tag {
  padding: 1px 7px;
  border-radius: 999px;
  background: var(--accent-soft);
  color: var(--accent);
  font-size: 10px;
}
.meta {
  margin: 6px 0;
  font-size: 11px;
  color: var(--text-3);
}
.spec {
  margin: 6px 0;
  font-size: 11px;
  color: var(--text-2);
}
.prompt {
  border-top: 1px solid var(--border);
  padding: 8px 0;
}
.prompt-head {
  display: flex;
  align-items: baseline;
  gap: 8px;
}
.prompt-name {
  font-size: 12px;
  color: var(--text-1);
}
.prompt-body {
  margin: 5px 0 0;
  padding: 6px 8px;
  border-radius: var(--radius);
  background: var(--bg-0);
  color: var(--text-2);
  font-family: var(--font-mono);
  font-size: 11px;
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-word;
}
.mono {
  font-family: var(--font-mono);
}
.voices {
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  background: var(--bg-1);
  padding: 10px 12px;
}
.voice-row {
  border-top: 1px solid var(--border);
  padding: 8px 0;
}
.voice-row:first-of-type {
  border-top: none;
}
.dose {
  flex: 0 0 84px;
  max-width: 84px;
}
/* 窄屏：设置项一行一项，内置预设的标题行换行。 */
@media (max-width: 768px) {
  .control {
    flex-wrap: wrap;
  }
  .control .input {
    flex: 1 1 100%;
  }
  .save {
    width: 100%;
    justify-content: center;
  }
  .builtin-head {
    flex-wrap: wrap;
  }
  .builtin-head .btn {
    margin-left: 0;
  }
}
</style>
