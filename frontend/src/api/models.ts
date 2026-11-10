// 模型档位与本地推理服务管理接口。
// 档位是后端声明的模型档案：权重路径、上下文长度、KV 量化与对话模板。
import { getJson, postJson } from './http'

/** 一个模型档位 */
export interface ModelTier {
  profile_id: string
  name: string
  model_path: string
  model_file: string
  model_exists: boolean
  size_mb: number | null
  n_ctx: number
  kv_type: string
  n_gpu_layers: number
  measured: Record<string, unknown>
  running: boolean
  base_url: string | null
}

/** 档位清单 */
export interface TierListResponse {
  items: ModelTier[]
}

/** 推理服务状态 */
export interface InferenceStatus {
  service: {
    reachable: boolean
    base_url: string
    models: string[]
    tokenizer_count: number | null
    error: string | null
  }
  profile: Record<string, unknown>
}

/** 启动回执 */
export interface StartTierResponse {
  ok: boolean
  service: Record<string, unknown>
}

export function fetchTiers(): Promise<TierListResponse> {
  return getJson<TierListResponse>('/v1/inference/tiers')
}

export function fetchInferenceStatus(): Promise<InferenceStatus> {
  return getJson<InferenceStatus>('/v1/inference/status')
}

/** 按档位拉起推理服务并切过去 */
export function startTier(profileId: string): Promise<StartTierResponse> {
  return postJson<StartTierResponse>('/v1/inference/start', { profile_id: profileId })
}

/** 停掉推理服务 */
export function stopTier(profileId?: string): Promise<{ ok: boolean; stopped: string[] }> {
  return postJson<{ ok: boolean; stopped: string[] }>('/v1/inference/stop', {
    profile_id: profileId ?? '',
  })
}
