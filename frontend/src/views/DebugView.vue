<script setup lang="ts">
// 调试视图：装配预览、分词器探测、模型与 profile 清单。
// 前端只展示后端返回的装配回执，不自行拼装 prompt、不做 token 计数。
import { onMounted, ref } from 'vue'
import { Play } from 'lucide-vue-next'
import {
  fetchModels,
  fetchPreview,
  fetchProfiles,
  fetchTokenizers,
  type PreviewResult,
} from '../api/debug'
import type { ModelInfo, Profile, TokenizerListResponse } from '../types/chat'

const sessionId = ref('')
const characterId = ref('')
const presetId = ref('')
const worldBookIds = ref('')
const userInput = ref('继续')

const preview = ref<PreviewResult | null>(null)
const tokenizers = ref<TokenizerListResponse | null>(null)
const models = ref<ModelInfo[]>([])
const profiles = ref<Profile[]>([])
const busy = ref(false)
const errorText = ref('')

async function onPreview(): Promise<void> {
  busy.value = true
  errorText.value = ''
  try {
    preview.value = await fetchPreview({
      session: sessionId.value.trim(),
      character_id: characterId.value.trim(),
      preset_id: presetId.value.trim(),
      world_book_ids: worldBookIds.value.trim(),
      user_input: userInput.value,
    })
  } catch (err) {
    errorText.value = err instanceof Error ? err.message : String(err)
  } finally {
    busy.value = false
  }
}

async function loadStatic(): Promise<void> {
  errorText.value = ''
  try {
    const [tk, ms, ps] = await Promise.all([fetchTokenizers(), fetchModels(), fetchProfiles()])
    tokenizers.value = tk
    models.value = ms
    profiles.value = ps
  } catch (err) {
    errorText.value = err instanceof Error ? err.message : String(err)
  }
}

onMounted(() => {
  void loadStatic()
})
</script>

<template>
  <div class="debug">
    <p v-if="errorText" class="error">{{ errorText }}</p>

    <section class="block">
      <h3 class="title">装配预览</h3>
      <div class="form">
        <label class="field">
          <span>会话 ID</span>
          <input v-model="sessionId" class="input" placeholder="s_..." />
        </label>
        <label class="field">
          <span>角色卡 ID</span>
          <input v-model="characterId" class="input" placeholder="c_..." />
        </label>
        <label class="field">
          <span>预设 ID</span>
          <input v-model="presetId" class="input" placeholder="p_..." />
        </label>
        <label class="field">
          <span>世界书 ID</span>
          <input v-model="worldBookIds" class="input" placeholder="多个用逗号分隔" />
        </label>
        <label class="field wide">
          <span>本轮输入</span>
          <input v-model="userInput" class="input" />
        </label>
        <button class="btn" :disabled="busy" @click="onPreview">
          <Play :size="13" />
          预览装配
        </button>
      </div>

      <template v-if="preview">
        <p class="meta">
          模板 {{ preview.template }} · 分词器 {{ preview.tokenizer }} ·
          截断 {{ preview.truncated ? '是' : '否' }}
        </p>
        <div class="table-scroll">
        <table class="table">
          <thead>
            <tr>
              <th>段</th>
              <th>token</th>
              <th>命中</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="seg in preview.segments" :key="seg.name">
              <td>{{ seg.name }}</td>
              <td class="mono">{{ seg.tokens }}</td>
              <td class="mono small">
                {{ seg.entries?.length ? seg.entries.join('、') : '' }}
                {{ seg.hits !== null && seg.hits !== undefined ? `命中 ${seg.hits}` : '' }}
              </td>
            </tr>
          </tbody>
        </table>
        </div>
        <ol class="messages">
          <li v-for="(msg, index) in preview.messages" :key="index">
            <span class="role">{{ msg.role }}</span>
            <span class="source mono">{{ msg.source }}</span>
            <p class="content">{{ msg.content }}</p>
          </li>
        </ol>
      </template>
      <p v-else class="meta">填入参数后点击预览，查看八段装配的实际结果。</p>
    </section>

    <section class="block">
      <h3 class="title">分词器探测</h3>
      <div class="table-scroll">
      <table v-if="tokenizers" class="table">
        <thead>
          <tr>
            <th>名称</th>
            <th>可用</th>
            <th>近似</th>
            <th>说明</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="tk in tokenizers.tokenizers" :key="tk.name">
            <td class="mono">{{ tk.name }}</td>
            <td :class="tk.available ? 'ok' : 'bad'">{{ tk.available ? '是' : '否' }}</td>
            <td>{{ tk.approximate ? '是' : '否' }}</td>
            <td>{{ tk.description }}</td>
          </tr>
        </tbody>
      </table>
      </div>
      <p v-if="tokenizers?.probe_result" class="meta mono">
        探测文本「{{ tokenizers.probe_result.test_text }}」：近似计数
        {{ tokenizers.probe_result.approx_count }}，真实计数
        {{ tokenizers.probe_result.llamacpp_count }}，llama.cpp 可用
        {{ tokenizers.probe_result.llamacpp_available ? '是' : '否' }}。
      </p>
    </section>

    <section class="block">
      <h3 class="title">模型与 Profile</h3>
      <div class="table-scroll">
      <table class="table">
        <thead>
          <tr>
            <th>模型 ID</th>
            <th>名称</th>
            <th>提供方</th>
            <th>上下文</th>
            <th>思考</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="m in models" :key="m.id">
            <td class="mono small">{{ m.id }}</td>
            <td>{{ m.label }}</td>
            <td>{{ m.provider }}</td>
            <td class="mono">{{ m.context_length }}</td>
            <td>{{ m.supports_reasoning ? '是' : '否' }}</td>
          </tr>
        </tbody>
      </table>
      </div>
      <div class="table-scroll">
      <table v-if="profiles.length" class="table">
        <thead>
          <tr>
            <th>Profile</th>
            <th>参数</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="p in profiles" :key="p.id">
            <td>{{ p.name }}</td>
            <td class="mono small">
              {{ Object.entries(p.params).map(([k, v]) => k + '=' + String(v)).join('、') || '—' }}
            </td>
          </tr>
        </tbody>
      </table>
      </div>
    </section>
  </div>
</template>

<style scoped>
.debug {
  display: flex;
  flex-direction: column;
  gap: 10px;
  height: 100%;
  overflow-y: auto;
  min-height: 0;
}
.block {
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
.error {
  margin: 0;
  padding: 6px 10px;
  border-radius: var(--radius);
  background: var(--danger-soft);
  color: var(--level-danger);
  font-size: 12px;
}
.form {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: flex-end;
  margin-bottom: 10px;
}
.field {
  display: flex;
  flex-direction: column;
  gap: 3px;
  font-size: 11px;
  color: var(--text-3);
}
.field.wide {
  flex: 1;
  min-width: 180px;
}
.input {
  background: var(--bg-0);
  font-size: 12px;
}
.btn {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 6px 12px;
  border: 1px solid var(--accent);
  border-radius: var(--radius);
  background: var(--accent-soft);
  color: var(--accent);
  font-size: 12px;
}
.btn:disabled {
  opacity: 0.55;
}
.meta {
  margin: 6px 0;
  color: var(--text-3);
  font-size: 11px;
}
.table {
  width: 100%;
  border-collapse: collapse;
  font-size: 12px;
  margin-top: 6px;
}
.table th {
  text-align: left;
  color: var(--text-3);
  font-weight: 500;
  border-bottom: 1px solid var(--border);
  padding: 4px 6px;
}
.table td {
  padding: 4px 6px;
  border-bottom: 1px solid var(--border);
  color: var(--text-2);
}
.table tbody tr:hover td {
  background: var(--bg-2);
}
.messages {
  margin: 10px 0 0;
  padding-left: 18px;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.messages li {
  font-size: 12px;
}
.role {
  color: var(--text-1);
  margin-right: 6px;
}
.source {
  color: var(--text-3);
  font-size: 11px;
}
.content {
  margin: 2px 0 0;
  color: var(--text-2);
  white-space: pre-wrap;
  word-break: break-word;
}
.mono {
  font-family: var(--font-mono);
}
.small {
  font-size: 11px;
}
.ok {
  color: var(--level-ok);
}
.bad {
  color: var(--level-danger);
}
/* 表格放进横向滚动容器：窄屏时列挤在一起也不撑破布局 */
.table-scroll {
  overflow-x: auto;
}
/* 窄屏：表单一列一行一项，表格保持最小宽度并横向滚动。 */
@media (max-width: 768px) {
  .form {
    flex-direction: column;
    align-items: stretch;
  }
  .field.wide {
    min-width: 0;
  }
  .table {
    min-width: 480px;
  }
}
</style>
