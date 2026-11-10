// 设置 mock 数据。写操作只回显，不改动模块常量。
export const MOCK_SETTINGS: Record<string, unknown> = {
  inference_url: 'http://127.0.0.1:8080',
  active_model: 'xw-local-default',
  active_profile: 'p_default',
  default_character_id: '',
  default_preset_id: '',
  memory_mode: 'hybrid',
  max_context_tokens: 8192,
  reserved_output_tokens: 1024,
  bm25_weight: 0.6,
  vector_weight: 0.4,
  audit_enabled: false,
  theme: 'dark',
}

export const MOCK_SETTING_KEYS = Object.keys(MOCK_SETTINGS)

export const settingsMockHandlers: Record<
  string,
  (path: string, method: string, body: unknown) => unknown
> = {
  'GET /v1/settings/': () => ({ items: MOCK_SETTINGS, keys: MOCK_SETTING_KEYS }),
  'GET /v1/settings/:key': (path) => {
    const key = decodeURIComponent(path.split('/').pop() ?? '')
    if (!(key in MOCK_SETTINGS)) {
      return { error: { code: 'setting_not_found', message: '设置项不存在', retryable: false } }
    }
    return { key, value: MOCK_SETTINGS[key] }
  },
  'PUT /v1/settings/:key': (path, _method, body) => {
    const key = decodeURIComponent(path.split('/').pop() ?? '')
    const value =
      body && typeof body === 'object' && 'value' in body
        ? (body as { value: unknown }).value
        : null
    return { ok: true, key, value }
  },
  'POST /v1/settings/': () => ({ ok: true, items: MOCK_SETTINGS }),
}
