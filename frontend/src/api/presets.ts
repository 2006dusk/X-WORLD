// 预设接口：预设清单、提示组与正则规则的读写。
// 提示组的 role 与 order 直接决定后端装配段的顺序，前端不做重排语义。
import { deleteJson, getJson, postJson, putJson } from './http'

/** 单个提示组 */
export interface PresetPrompt {
  id?: string
  name: string
  content: string
  role: string
  order: number
  enabled: boolean
}

/** 正则规则，后端原样保存 */
export interface RegexRule {
  id?: string
  pattern: string
  replacement?: string
  [key: string]: unknown
}

/** 输出契约：回合的块与提示里广告哪几块 */
export interface PresetOutputSpec {
  structure?: string[]
  structure_prompt?: string[]
  [key: string]: unknown
}

/** 预设 */
export interface Preset {
  id?: string
  name: string
  prompts: PresetPrompt[]
  regex_rules: RegexRule[]
  updated_at?: string
  /** 内置预设由后端按版本刷新，用户改不动 */
  builtin?: boolean
  builtin_version?: number
  output_spec?: PresetOutputSpec
}

/** 预设摘要 */
export interface PresetSummary {
  id: string
  name: string
  updated_at?: string
  /** 内置预设标记：预设页据此把它挪到设置页只读展示 */
  builtin?: boolean
  builtin_version?: number
}

/** 提示组按 system 与其余拆分 */
export interface PromptGroups {
  system: PresetPrompt[]
  others: PresetPrompt[]
  all: PresetPrompt[]
}

function p(presetId: string): string {
  return encodeURIComponent(presetId)
}

export function fetchPresets(): Promise<{ items: PresetSummary[] }> {
  return getJson<{ items: PresetSummary[] }>('/v1/presets/')
}

export function fetchPreset(id: string): Promise<Preset> {
  return getJson<Preset>(`/v1/presets/${p(id)}`)
}

export function createPreset(preset: Partial<Preset>): Promise<Preset> {
  return postJson<Preset>('/v1/presets/', preset)
}

export function updatePreset(id: string, preset: Partial<Preset>): Promise<Preset> {
  return putJson<Preset>(`/v1/presets/${p(id)}`, preset)
}

export function deletePreset(id: string): Promise<{ ok: boolean; id: string }> {
  return deleteJson<{ ok: boolean; id: string }>(`/v1/presets/${p(id)}`)
}

export function fetchPromptGroups(id: string): Promise<PromptGroups> {
  return getJson<PromptGroups>(`/v1/presets/${p(id)}/prompts`)
}
