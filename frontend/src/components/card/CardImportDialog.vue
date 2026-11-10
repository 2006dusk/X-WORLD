<script setup lang="ts">
import { computed, ref } from 'vue'
import { FileJson, Upload, X } from 'lucide-vue-next'

const props = defineProps<{
  busy: boolean
  error: string
}>()
const emit = defineEmits<{
  close: []
  submit: [jsonText: string]
}>()

const mode = ref<'paste' | 'file'>('paste')
const jsonText = ref('')
const fileName = ref('')
const localError = ref('')
const fileInput = ref<HTMLInputElement | null>(null)
const shownError = computed(() => localError.value || props.error)

function validateAndSubmit(): void {
  localError.value = ''
  if (!jsonText.value.trim()) {
    localError.value = mode.value === 'file' ? '请先选择一个 JSON 文件' : '请粘贴角色卡 JSON 文本'
    return
  }
  let parsed: unknown
  try {
    parsed = JSON.parse(jsonText.value)
  } catch (error) {
    localError.value = `JSON 解析失败：${error instanceof Error ? error.message : '格式无效'}`
    return
  }
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
    localError.value = '角色卡必须是一个 JSON 对象'
    return
  }
  emit('submit', jsonText.value)
}

async function readFile(file?: File): Promise<void> {
  if (!file) return
  localError.value = ''
  fileName.value = file.name
  try {
    jsonText.value = await file.text()
  } catch {
    jsonText.value = ''
    localError.value = '无法读取该文件，请重新选择'
  }
}

function onFileChange(event: Event): void {
  const input = event.target as HTMLInputElement
  void readFile(input.files?.[0])
}

function close(): void {
  if (!props.busy) emit('close')
}
</script>

<template>
  <div class="overlay" role="presentation" @click.self="close" @keydown.esc="close">
    <section class="dialog" role="dialog" aria-modal="true" aria-labelledby="card-import-title">
      <header class="head">
        <div>
          <h2 id="card-import-title">导入角色卡</h2>
          <p>支持 Character Card V1、V2 与 V3 JSON；不支持 PNG 隐写。</p>
        </div>
        <button class="close" title="关闭" aria-label="关闭导入" :disabled="busy" @click="close"><X :size="17" /></button>
      </header>

      <div class="tabs" role="tablist" aria-label="导入方式">
        <button role="tab" :aria-selected="mode === 'paste'" :class="{ active: mode === 'paste' }" @click="mode = 'paste'; localError = ''">粘贴 JSON 文本</button>
        <button role="tab" :aria-selected="mode === 'file'" :class="{ active: mode === 'file' }" @click="mode = 'file'; localError = ''">选择 JSON 文件</button>
      </div>

      <div v-if="mode === 'paste'" class="paste-panel">
        <label for="card-json">角色卡 JSON</label>
        <textarea
          id="card-json"
          v-model="jsonText"
          rows="13"
          spellcheck="false"
          placeholder='{"name":"角色名","identity":"身份与处境…","behavior":"行为与细节…","speech":"说话方式…","tags":["标签"]}'
          @input="localError = ''"
        />
      </div>
      <div v-else class="file-panel">
        <input ref="fileInput" class="file-input" type="file" accept=".json,application/json" @change="onFileChange" />
        <button class="file-picker" :disabled="busy" @click="fileInput?.click()">
          <FileJson :size="28" />
          <strong>{{ fileName || '选择本地 JSON 文件' }}</strong>
          <span>文件只在当前页面读取，并提交到本地后端</span>
        </button>
        <p v-if="jsonText" class="file-ready">已读取 {{ jsonText.length.toLocaleString() }} 个字符，可开始导入。</p>
      </div>

      <p v-if="shownError" class="error" role="alert">{{ shownError }}</p>
      <footer class="foot">
        <span>导入会创建新卡片，不覆盖列表中的现有卡片。</span>
        <div>
          <button class="secondary" :disabled="busy" @click="close">取消</button>
          <button class="primary" :disabled="busy" @click="validateAndSubmit">
            <Upload :size="14" /> {{ busy ? '正在导入…' : '导入' }}
          </button>
        </div>
      </footer>
    </section>
  </div>
</template>

<style scoped>
.overlay { position: fixed; inset: 0; z-index: 30; display: grid; place-items: center; padding: 20px; background: var(--bg-0); }
.dialog { width: min(680px, 100%); max-height: calc(var(--app-h) - 40px); overflow-y: auto; border: 1px solid var(--accent); border-radius: var(--radius-lg); background: var(--bg-1); padding: 14px; }
.head { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; }
h2 { margin: 0; font-size: 16px; }
.head p { margin: 3px 0 0; color: var(--text-3); font-size: 12px; }
.close { display: inline-flex; padding: 4px; color: var(--text-3); border-radius: var(--radius); }
.close:hover { color: var(--text-1); background: var(--bg-2); }
.tabs { display: flex; gap: 3px; margin: 14px 0 10px; padding-bottom: 6px; border-bottom: 1px solid var(--border); }
.tabs button { padding: 5px 9px; border-radius: var(--radius); color: var(--text-2); font-size: 12px; }
.tabs button:hover { background: var(--bg-2); color: var(--text-1); }
.tabs button.active { background: var(--accent-soft); color: var(--accent); }
.paste-panel label { display: block; margin-bottom: 5px; color: var(--text-2); font-size: 12px; }
textarea { display: block; width: 100%; resize: vertical; font: 12px/1.55 var(--font-mono); tab-size: 2; }
.file-input { display: none; }
.file-picker { display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 7px; width: 100%; min-height: 230px; padding: 20px; border: 1px dashed var(--border); border-radius: var(--radius-lg); background: var(--bg-0); color: var(--accent); }
.file-picker:hover { border-color: var(--accent); background: var(--accent-soft); }
.file-picker strong { max-width: 100%; overflow-wrap: anywhere; color: var(--text-1); font-size: 13px; }
.file-picker span { color: var(--text-3); font-size: 12px; }
.file-ready { margin: 8px 0 0; color: var(--level-ok); font-size: 12px; }
.error { margin: 9px 0 0; padding: 6px 8px; border: 1px solid var(--level-danger); border-radius: var(--radius); background: var(--danger-soft); color: var(--level-danger); font-size: 12px; overflow-wrap: anywhere; }
.foot { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-top: 12px; }
.foot > span { color: var(--text-3); font-size: 11px; }
.foot > div { display: flex; gap: 7px; flex-shrink: 0; }
.primary, .secondary { display: inline-flex; align-items: center; justify-content: center; gap: 5px; padding: 5px 12px; border-radius: var(--radius); font-size: 12px; }
.primary { border: 1px solid var(--accent); color: var(--accent); }
.primary:hover { background: var(--accent-soft); }
.secondary { border: 1px solid var(--border); color: var(--text-2); }
.secondary:hover { background: var(--bg-2); color: var(--text-1); }
button:disabled { cursor: not-allowed; opacity: 0.55; }
@media (max-width: 560px) { .foot { align-items: flex-end; } .foot > span { max-width: 180px; } }
</style>
