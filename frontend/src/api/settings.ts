// 全局设置接口：设置以 JSON 文件存放于后端数据目录，前端只经 /v1/settings 读写。
import { getJson, postJson, putJson } from './http'

/** GET /v1/settings/ */
export interface SettingsListResponse {
  items: Record<string, unknown>
  keys: string[]
}

/** 单项写入回执 */
export interface SettingWriteResponse {
  ok: boolean
  key: string
  value: unknown
}

export function fetchSettings(): Promise<SettingsListResponse> {
  return getJson<SettingsListResponse>('/v1/settings/')
}

export function fetchSetting(key: string): Promise<{ key: string; value: unknown }> {
  return getJson<{ key: string; value: unknown }>(`/v1/settings/${encodeURIComponent(key)}`)
}

export function putSetting(key: string, value: unknown): Promise<SettingWriteResponse> {
  return putJson<SettingWriteResponse>(`/v1/settings/${encodeURIComponent(key)}`, { value })
}

export function saveSettings(
  payload: Record<string, unknown>,
): Promise<{ ok: boolean; items: Record<string, unknown> }> {
  return postJson<{ ok: boolean; items: Record<string, unknown> }>('/v1/settings/', payload)
}
