<script setup lang="ts">
// 插件视图：已装载插件清单、hook 契约、调用日志与手动调用。
// 启停与重载只调后端插件接口，前端不参与插件进程管理。
import { computed, onMounted, ref } from 'vue'
import { RefreshCw, Play, Square, Bug } from 'lucide-vue-next'
import {
  disablePlugin,
  enablePlugin,
  fetchHooks,
  fetchPluginLog,
  fetchPlugins,
  invokePlugin,
  reloadPlugins,
  type HookContract,
  type PluginInfo,
  type PluginLogEntry,
} from '../api/plugins'

const plugins = ref<PluginInfo[]>([])
const hooks = ref<HookContract[]>([])
const loadErrors = ref<string[]>([])
const selectedId = ref('')
const logs = ref<PluginLogEntry[]>([])
const filter = ref('')
const busy = ref(false)
const notice = ref('')
const errorText = ref('')

const invokeHook = ref('')
const invokeParams = ref('{}')
const invokeResult = ref<string>('')

const filtered = computed(() => {
  const key = filter.value.trim().toLowerCase()
  if (!key) return plugins.value
  return plugins.value.filter(
    (p) =>
      p.name.toLowerCase().includes(key) ||
      p.id.toLowerCase().includes(key) ||
      p.capabilities.some((c) => c.toLowerCase().includes(key)),
  )
})

const selected = computed<PluginInfo | null>(
  () => plugins.value.find((p) => p.id === selectedId.value) ?? null,
)

function describe(value: unknown): string {
  if (value === null || value === undefined) return ''
  if (typeof value === 'string') return value
  return JSON.stringify(value)
}

async function load(): Promise<void> {
  busy.value = true
  errorText.value = ''
  try {
    const res = await fetchPlugins()
    plugins.value = res.items
    loadErrors.value = res.errors ?? []
    if (!selectedId.value && res.items.length > 0) selectedId.value = res.items[0].id
  } catch (err) {
    errorText.value = err instanceof Error ? err.message : String(err)
  } finally {
    busy.value = false
  }
}

async function loadHooks(): Promise<void> {
  try {
    const res = await fetchHooks()
    hooks.value = res.items
    if (!invokeHook.value && res.items.length > 0) invokeHook.value = res.items[0].name
  } catch (err) {
    errorText.value = err instanceof Error ? err.message : String(err)
  }
}

async function loadLogs(): Promise<void> {
  if (!selectedId.value) return
  try {
    const res = await fetchPluginLog(selectedId.value, 50)
    logs.value = res.items
  } catch (err) {
    errorText.value = err instanceof Error ? err.message : String(err)
  }
}

async function toggle(plugin: PluginInfo): Promise<void> {
  busy.value = true
  errorText.value = ''
  try {
    const next = plugin.enabled ? await disablePlugin(plugin.id) : await enablePlugin(plugin.id)
    const index = plugins.value.findIndex((p) => p.id === next.id)
    if (index >= 0) plugins.value[index] = next
    notice.value = `${next.name} 已${next.enabled ? '启用' : '停用'}`
    await loadLogs()
  } catch (err) {
    errorText.value = err instanceof Error ? err.message : String(err)
  } finally {
    busy.value = false
  }
}

async function onReload(): Promise<void> {
  busy.value = true
  errorText.value = ''
  try {
    const res = await reloadPlugins()
    plugins.value = res.items
    loadErrors.value = res.errors ?? []
    notice.value = '插件目录已重新扫描'
    await loadLogs()
  } catch (err) {
    errorText.value = err instanceof Error ? err.message : String(err)
  } finally {
    busy.value = false
  }
}

async function onInvoke(): Promise<void> {
  errorText.value = ''
  invokeResult.value = ''
  let params: Record<string, unknown>
  try {
    const parsed: unknown = JSON.parse(invokeParams.value || '{}')
    if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed)) {
      throw new Error('参数必须是 JSON 对象')
    }
    params = parsed as Record<string, unknown>
  } catch (err) {
    errorText.value = err instanceof Error ? err.message : String(err)
    return
  }
  try {
    const result = await invokePlugin(selectedId.value, invokeHook.value, params)
    invokeResult.value = JSON.stringify(result, null, 2)
  } catch (err) {
    errorText.value = err instanceof Error ? err.message : String(err)
  }
}

onMounted(() => {
  void load()
  void loadHooks()
})
</script>

<template>
  <div class="plugins">
    <header class="toolbar">
      <h2 class="heading">插件</h2>
      <input v-model="filter" class="filter" type="search" placeholder="按名称或能力过滤" />
      <button class="btn" :disabled="busy" @click="onReload">
        <RefreshCw :size="14" />
        重新装载
      </button>
    </header>

    <p v-if="errorText" class="error">{{ errorText }}</p>
    <p v-if="notice" class="notice">{{ notice }}</p>
    <ul v-if="loadErrors.length" class="errors">
      <li v-for="(item, index) in loadErrors" :key="index">{{ item }}</li>
    </ul>

    <div class="body">
      <aside class="list" aria-label="插件清单">
        <button
          v-for="p in filtered"
          :key="p.id"
          class="row"
          :class="{ active: p.id === selectedId }"
          @click="selectedId = p.id; void loadLogs()"
        >
          <span class="row-name">{{ p.name }}</span>
          <span class="row-meta">v{{ p.version }} · {{ p.capabilities.join('、') }}</span>
          <span class="badge" :class="p.enabled ? 'on' : 'off'">
            {{ p.enabled ? '启用' : '停用' }}
          </span>
        </button>
        <p v-if="filtered.length === 0" class="empty">未找到插件。</p>
      </aside>

      <section class="detail">
        <template v-if="selected">
          <div class="block">
            <div class="block-head">
              <h3 class="title">{{ selected.name }}</h3>
              <button class="btn" :disabled="busy" @click="toggle(selected)">
                <Square v-if="selected.enabled" :size="13" />
                <Play v-else :size="13" />
                {{ selected.enabled ? '停用' : '启用' }}
              </button>
            </div>
            <dl class="kv">
              <div><dt>ID</dt><dd>{{ selected.id }}</dd></div>
              <div><dt>版本</dt><dd>{{ selected.version }}</dd></div>
              <div><dt>apiVersion</dt><dd>{{ selected.apiVersion }}</dd></div>
              <div><dt>状态</dt><dd>{{ selected.status }}</dd></div>
              <div><dt>入口</dt><dd>{{ selected.entry.command.join(' ') }} {{ selected.entry.args.join(' ') }}</dd></div>
              <div><dt>目录</dt><dd>{{ describe(selected.directory) || '内置' }}</dd></div>
            </dl>
          </div>

          <div class="block">
            <h3 class="title">Hook 契约</h3>
            <div class="table-scroll">
            <table class="table">
              <thead>
                <tr>
                  <th>Hook</th>
                  <th>说明</th>
                  <th>输入</th>
                  <th>输出</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="h in hooks" :key="h.name">
                  <td class="mono">{{ h.name }}</td>
                  <td>{{ h.description }}</td>
                  <td class="mono small">
                    {{ h.input.map((i) => i.name + (i.required ? '*' : '')).join('、') || '—' }}
                  </td>
                  <td class="mono small">
                    {{ h.output.map((o) => o.name + (o.required ? '*' : '')).join('、') || '—' }}
                  </td>
                </tr>
              </tbody>
            </table>
            </div>
          </div>

          <div class="block">
            <h3 class="title">调用日志</h3>
            <div class="table-scroll">
            <table class="table">
              <thead>
                <tr>
                  <th>时间</th>
                  <th>动作</th>
                  <th>结果</th>
                  <th>耗时</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="(entry, index) in logs" :key="index">
                  <td class="mono small">{{ entry.at }}</td>
                  <td>{{ entry.action }}</td>
                  <td :class="entry.ok ? 'ok' : 'bad'">{{ entry.ok ? '成功' : '失败' }}</td>
                  <td class="mono small">{{ entry.duration_ms }} ms</td>
                </tr>
                <tr v-if="logs.length === 0">
                  <td colspan="4" class="empty">暂无调用记录。</td>
                </tr>
              </tbody>
            </table>
            </div>
          </div>

          <div class="block">
            <h3 class="title">
              <Bug :size="13" />
              手动调用
            </h3>
            <div class="invoke-row">
              <select v-model="invokeHook" class="select">
                <option v-for="h in hooks" :key="h.name" :value="h.name">{{ h.name }}</option>
              </select>
              <button class="btn" :disabled="busy" @click="onInvoke">执行</button>
            </div>
            <textarea v-model="invokeParams" class="params mono" rows="4" spellcheck="false" />
            <pre v-if="invokeResult" class="result mono">{{ invokeResult }}</pre>
          </div>
        </template>
        <p v-else class="empty">左侧选择插件查看详情。</p>
      </section>
    </div>
  </div>
</template>

<style scoped>
.plugins {
  display: flex;
  flex-direction: column;
  gap: 10px;
  height: 100%;
  min-height: 0;
}
.toolbar {
  display: flex;
  align-items: center;
  gap: 8px;
}
.heading {
  margin: 0;
  font-size: 13px;
  color: var(--text-2);
  text-transform: uppercase;
  letter-spacing: 0.05em;
}
.filter {
  flex: 1;
  max-width: 320px;
  background: var(--bg-0);
}
.btn {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 5px 10px;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  background: var(--bg-1);
  color: var(--text-2);
  font-size: 12px;
}
.btn:hover:not(:disabled) {
  background: var(--bg-2);
  color: var(--text-1);
}
.btn:disabled {
  opacity: 0.55;
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
.errors {
  margin: 0;
  padding: 6px 10px 6px 26px;
  border-radius: var(--radius);
  background: var(--warn-soft);
  color: var(--level-warn);
  font-size: 12px;
}
.body {
  display: grid;
  grid-template-columns: 280px 1fr;
  gap: 10px;
  min-height: 0;
  flex: 1;
}
.list {
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 3px;
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  background: var(--bg-1);
  padding: 6px;
}
.row {
  display: grid;
  grid-template-columns: 1fr auto;
  grid-template-areas: 'name badge' 'meta badge';
  gap: 2px 8px;
  padding: 6px 8px;
  border-radius: var(--radius);
  text-align: left;
}
.row:hover {
  background: var(--bg-2);
}
.row.active {
  background: var(--accent-soft);
}
.row-name {
  grid-area: name;
  color: var(--text-1);
  font-size: 13px;
}
.row-meta {
  grid-area: meta;
  color: var(--text-3);
  font-size: 11px;
}
.badge {
  grid-area: badge;
  align-self: center;
  padding: 1px 6px;
  border-radius: 999px;
  font-size: 11px;
}
.badge.on {
  background: var(--ok-soft);
  color: var(--level-ok);
}
.badge.off {
  background: var(--bg-2);
  color: var(--text-3);
}
.detail {
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 10px;
  min-height: 0;
  min-width: 0;
}
.block {
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  background: var(--bg-1);
  padding: 10px 12px;
}
.block-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}
.title {
  display: flex;
  align-items: center;
  gap: 5px;
  margin: 0 0 8px;
  font-size: 12px;
  color: var(--text-2);
  text-transform: uppercase;
  letter-spacing: 0.05em;
}
.kv {
  margin: 0;
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 4px 14px;
}
.kv > div {
  display: flex;
  gap: 8px;
  font-size: 12px;
}
.kv dt {
  color: var(--text-3);
  min-width: 72px;
}
.kv dd {
  margin: 0;
  color: var(--text-1);
  word-break: break-all;
}
.table {
  width: 100%;
  border-collapse: collapse;
  font-size: 12px;
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
  vertical-align: top;
}
.table tbody tr:hover td {
  background: var(--bg-2);
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
.empty {
  color: var(--text-3);
  font-size: 12px;
  margin: 8px 0;
}
.invoke-row {
  display: flex;
  gap: 8px;
  margin-bottom: 8px;
}
.select {
  background: var(--bg-0);
  min-width: 180px;
}
.params {
  width: 100%;
  resize: vertical;
  background: var(--bg-0);
  font-size: 12px;
}
.result {
  margin: 8px 0 0;
  padding: 8px;
  border-radius: var(--radius);
  background: var(--bg-0);
  color: var(--text-2);
  font-size: 11px;
  max-height: 220px;
  overflow: auto;
}
/* 表格放进横向滚动容器：列多的时候表自己滚，不把布局撑破 */
.table-scroll {
  overflow-x: auto;
}
/* 窄屏：两栏改上下叠放，表单单列，表格保持最小宽度并横向滚动。 */
@media (max-width: 768px) {
  .body {
    grid-template-columns: 1fr;
    grid-template-rows: minmax(0, 2fr) minmax(0, 3fr);
  }
  .toolbar {
    flex-wrap: wrap;
  }
  .filter {
    max-width: none;
    flex: 1 1 100%;
  }
  .kv {
    grid-template-columns: 1fr;
  }
  .invoke-row {
    flex-wrap: wrap;
  }
  .select {
    min-width: 0;
    flex: 1 1 100%;
  }
  .table {
    min-width: 520px;
  }
}
</style>
