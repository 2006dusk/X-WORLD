// 插件 mock 数据。handlers 供 mock 适配层按 方法 + 路径模式 匹配。
import type { HookContract, PluginListResponse } from '../api/plugins'

export const MOCK_HOOKS: HookContract[] = [
  {
    name: 'preAssembly',
    description: '装配前调用，可改写参与装配的输入或注入额外上下文。',
    input: [{ name: 'session', type: 'str', required: false }],
    output: [{ name: 'segments', type: 'list', required: false }],
  },
  {
    name: 'postAssembly',
    description: '装配后调用，可检查或改写最终消息序列。',
    input: [{ name: 'session', type: 'str', required: false }],
    output: [{ name: 'messages', type: 'list', required: false }],
  },
  {
    name: 'preRequest',
    description: '请求发往推理服务前调用，可改写请求体。',
    input: [{ name: 'messages', type: 'list', required: true }],
    output: [{ name: 'messages', type: 'list', required: false }],
  },
  {
    name: 'postResponse',
    description: '收到推理响应后调用，可改写输出文本。',
    input: [{ name: 'content', type: 'str', required: true }],
    output: [{ name: 'content', type: 'str', required: false }],
  },
  {
    name: 'turnStart',
    description: '一轮对话开始时调用。',
    input: [{ name: 'session', type: 'str', required: false }],
    output: [],
  },
  {
    name: 'turnEnd',
    description: '一轮对话结束时调用。',
    input: [{ name: 'session', type: 'str', required: false }],
    output: [],
  },
]

export const MOCK_PLUGIN_LIST: PluginListResponse = {
  items: [
    {
      id: 'builtin-regex',
      name: '内置正则插件',
      version: '1.0.0',
      apiVersion: '>=1.0.0 <2.0.0',
      capabilities: ['regex'],
      entry: { command: ['builtin'], args: ['regex_plugin'] },
      status: 'stopped',
      enabled: false,
      directory: null,
      error: null,
    },
    {
      id: 'builtin-worldbook',
      name: '内置世界书插件',
      version: '1.0.0',
      apiVersion: '>=1.0.0 <2.0.0',
      capabilities: ['worldbook'],
      entry: { command: ['builtin'], args: ['worldbook_plugin'] },
      status: 'stopped',
      enabled: false,
      directory: null,
      error: null,
    },
  ],
  hooks: MOCK_HOOKS.map((hook) => hook.name),
  errors: [],
}

export const MOCK_PLUGIN_LOG = [
  { at: '2026-01-10T14:30:00+00:00', action: 'disable', ok: true, duration_ms: 0 },
  { at: '2026-01-10T14:30:05+00:00', action: 'invoke:preAssembly', ok: true, duration_ms: 12 },
]

export const pluginsMockHandlers: Record<
  string,
  (path: string, method: string, body: unknown) => unknown
> = {
  'GET /v1/plugins/': () => MOCK_PLUGIN_LIST,
  'GET /v1/plugins/hooks': () => ({ items: MOCK_HOOKS }),
  'POST /v1/plugins/reload': () => ({
    items: MOCK_PLUGIN_LIST.items,
    errors: MOCK_PLUGIN_LIST.errors,
  }),
  'GET /v1/plugins/:id': (path) => {
    const id = decodeURIComponent(path.split('/').pop() ?? '')
    const found = MOCK_PLUGIN_LIST.items.find((item) => item.id === id)
    return found ?? {
      error: { code: 'plugin_not_found', message: `插件不存在：${id}`, retryable: false },
    }
  },
  'POST /v1/plugins/:id/enable': (path) => {
    const id = decodeURIComponent(path.split('/').slice(-2)[0] ?? '')
    const found = MOCK_PLUGIN_LIST.items.find((item) => item.id === id)
    return found ? { ...found, enabled: true, status: 'running' } : { ok: true, id }
  },
  'POST /v1/plugins/:id/disable': (path) => {
    const id = decodeURIComponent(path.split('/').slice(-2)[0] ?? '')
    const found = MOCK_PLUGIN_LIST.items.find((item) => item.id === id)
    return found ? { ...found, enabled: false, status: 'stopped' } : { ok: true, id }
  },
  'GET /v1/plugins/:id/log': (path) => {
    const id = decodeURIComponent(path.split('/').slice(-2)[0] ?? '')
    return { plugin_id: id, items: MOCK_PLUGIN_LOG }
  },
  'POST /v1/plugins/:id/invoke': (_path, _method, body) => ({
    ok: true,
    echoed: body,
  }),
}
