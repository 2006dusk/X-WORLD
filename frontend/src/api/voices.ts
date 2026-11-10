// 角色声线接口：每个角色可挂一个控制向量文件与剂量。
// 向量随请求下发，推理端按请求挂载，换角色就换向量，服务端全局不挂。
import { deleteJson, getJson, postJson, putJson } from './http'

/** 一个角色的声线配置 */
export interface VoiceItem {
  character_id: string
  name: string
  role: string
  /** 向量文件绝对路径；没挂时为空 */
  file: string
  scale: number
  layer_start: number
  layer_end: number
  active: boolean
}

/** 数据根 vectors 目录里的向量文件 */
export interface VoiceVector {
  name: string
  bytes: number
  /** 是否与当前模型尺寸匹配；模型信息取不到时为 null */
  compatible: boolean | null
}

/** 当前模型对向量尺寸的要求 */
export interface VoiceModel {
  model_desc: string
  n_embd: number | null
  n_layers: number | null
  expected_bytes: number | null
}

export interface VoiceListResponse {
  items: VoiceItem[]
  vectors: VoiceVector[]
  model: VoiceModel
  max_scale: number
}

export function fetchVoices(): Promise<VoiceListResponse> {
  return getJson<VoiceListResponse>('/v1/voices/')
}

export function putVoice(
  characterId: string,
  file: string,
  scale: number,
): Promise<{ ok: boolean; character_id: string }> {
  return putJson(`/v1/voices/${encodeURIComponent(characterId)}`, { file, scale })
}

export function deleteVoice(
  characterId: string,
): Promise<{ ok: boolean; character_id: string; removed: boolean }> {
  return deleteJson(`/v1/voices/${encodeURIComponent(characterId)}`)
}

/** 声线判定结果 */
export interface VoiceVerdict {
  verdict: 'pass' | 'weak' | 'fail' | 'unknown'
  character_delta: number
  control_delta: number
  notes: string[]
}

/** 一档剂量的判定 */
export interface VoiceDose {
  scale: number
  verdict: string
  character_delta: number
  control_delta: number
  score: number
}

export interface VoiceGenerateResponse {
  ok: boolean
  character_id: string
  file: string
  scale: number
  ratio: number
  generate: { bytes: number; layers: number; n_embd: number; pairs: number }
  sweep: VoiceDose[]
  verify: VoiceVerdict
}

/** 按角色卡现场生成声线，并扫一遍剂量取最优。 */
export function generateVoice(characterId: string): Promise<VoiceGenerateResponse> {
  return postJson<VoiceGenerateResponse>(`/v1/voices/generate`, { character_id: characterId })
}

/** 判定当前挂着的声线是不是这个角色的声线。 */
export function verifyVoice(
  characterId: string,
): Promise<{ ok: boolean; verify: VoiceVerdict }> {
  return postJson<{ ok: boolean; verify: VoiceVerdict }>(`/v1/voices/verify`, {
    character_id: characterId,
  })
}

/** 一层符合度检查的结果 */
export interface ConformanceLayer {
  verdict: string
  issues: string[]
  metrics: Record<string, unknown>
}

/** 符合度总判定 */
export interface ConformanceResult {
  ok: boolean
  character_id: string
  verdict: string
  issues: string[]
  layers: ConformanceLayer[]
}

/**
 * 判定一段话算不算这个角色说的。
 * 三层：结构（成不成立）、身份（像不像这个角色）、声线（挂上向量后原话是否更可能出现）。
 */
export function checkConformance(
  characterId: string,
  text: string,
): Promise<ConformanceResult> {
  return postJson<ConformanceResult>(`/v1/voices/conformance`, {
    character_id: characterId,
    text,
  })
}