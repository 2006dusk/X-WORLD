// 插件接口：清单、hook 契约、启停、调用日志与手动调用。
import { getJson, postJson } from './http'

/** 插件入口描述 */
export interface PluginEntry {
  command: string[]
  args: string[]
}

/** 已装载插件 */
export interface PluginInfo {
  id: string
  name: string
  version: string
  apiVersion: string
  capabilities: string[]
  entry: PluginEntry
  status: string
  enabled: boolean
  directory?: string | null
  error?: string | null
}

/** GET /v1/plugins/ */
export interface PluginListResponse {
  items: PluginInfo[]
  hooks: string[]
  errors: string[]
}

/** hook 输入输出字段说明 */
export interface HookParam {
  name: string
  type: string
  required: boolean
}

/** 单个 hook 契约 */
export interface HookContract {
  name: string
  description: string
  input: HookParam[]
  output: HookParam[]
}

/** 一条调用日志 */
export interface PluginLogEntry {
  at: string
  action: string
  ok: boolean
  duration_ms: number
  error?: { code?: string; message?: string } | null
}

export interface PluginLogResponse {
  plugin_id: string
  items: PluginLogEntry[]
}

/** 手动调用的返回值，插件自定义 */
export type InvokeResult = Record<string, unknown>

function p(pluginId: string): string {
  return encodeURIComponent(pluginId)
}

export function fetchPlugins(): Promise<PluginListResponse> {
  return getJson<PluginListResponse>('/v1/plugins/')
}

export function fetchHooks(): Promise<{ items: HookContract[] }> {
  return getJson<{ items: HookContract[] }>('/v1/plugins/hooks')
}

export function reloadPlugins(): Promise<{ items: PluginInfo[]; errors: string[] }> {
  return postJson<{ items: PluginInfo[]; errors: string[] }>('/v1/plugins/reload', {})
}

export function enablePlugin(pluginId: string): Promise<PluginInfo> {
  return postJson<PluginInfo>(`/v1/plugins/${p(pluginId)}/enable`, {})
}

export function disablePlugin(pluginId: string): Promise<PluginInfo> {
  return postJson<PluginInfo>(`/v1/plugins/${p(pluginId)}/disable`, {})
}

export function fetchPluginLog(pluginId: string, limit = 50): Promise<PluginLogResponse> {
  return getJson<PluginLogResponse>(`/v1/plugins/${p(pluginId)}/log?limit=${limit}`)
}

export function invokePlugin(
  pluginId: string,
  hook: string,
  params: Record<string, unknown>,
): Promise<InvokeResult> {
  return postJson<InvokeResult>(`/v1/plugins/${p(pluginId)}/invoke`, { hook, params })
}
