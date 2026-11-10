// 预设 mock 数据。
import type { Preset, PresetPrompt, PresetSummary } from '../api/presets'

export const MOCK_PROMPT_GROUPS: PresetPrompt[] = [
  {
    id: 'pr_1',
    name: '角色扮演指令',
    content: '你正在扮演角色K，回复保持角色一致，不解释设定。',
    role: 'system',
    order: 0,
    enabled: true,
  },
  {
    id: 'pr_2',
    name: '叙述风格',
    content: '以短句和环境细节推进，不使用旁白总结。',
    role: 'system',
    order: 1,
    enabled: true,
  },
]

export const MOCK_PRESET_LIST: PresetSummary[] = [
  { id: 'p_default', name: '基础预设', updated_at: '2026-01-10T14:30:00+00:00' },
]

export const MOCK_PRESET: Preset = {
  id: 'p_default',
  name: '基础预设',
  prompts: MOCK_PROMPT_GROUPS,
  regex_rules: [],
  updated_at: '2026-01-10T14:30:00+00:00',
}

export const presetsMockHandlers: Record<
  string,
  (path: string, method: string, body: unknown) => unknown
> = {
  'GET /v1/presets/': () => ({ items: MOCK_PRESET_LIST }),
  'POST /v1/presets/': (_path, _method, body) => ({
    ...MOCK_PRESET,
    ...(body && typeof body === 'object' ? body : {}),
  }),
  'GET /v1/presets/:id/prompts': () => ({
    system: MOCK_PROMPT_GROUPS,
    others: [],
    all: MOCK_PROMPT_GROUPS,
  }),
  'GET /v1/presets/:id': (path) => {
    const id = decodeURIComponent(path.split('/').pop() ?? '')
    return id === MOCK_PRESET.id ? MOCK_PRESET : {
      error: { code: 'preset_not_found', message: '预设不存在', retryable: false },
    }
  },
  'PUT /v1/presets/:id': (path, _method, body) => ({
    ...MOCK_PRESET,
    ...(body && typeof body === 'object' ? body : {}),
    id: decodeURIComponent(path.split('/').pop() ?? ''),
  }),
  'DELETE /v1/presets/:id': (path) => ({
    ok: true,
    id: decodeURIComponent(path.split('/').pop() ?? ''),
  }),
}
