// 资源管理接口，见契约 4.3。
import { getJson, postJson } from './http'
import type { ModelInfo, Profile, TokenizerListResponse } from '../types/chat'

/** 模型清单 */
export function fetchModels(): Promise<ModelInfo[]> {
  return getJson<ModelInfo[]>('/v1/models')
}

/** 切换激活模型与 profile，见契约 4.3 */
export function setActiveModel(payload: {
  model_id: string
  profile_id: string
}): Promise<{ ok: boolean }> {
  return postJson<{ ok: boolean }>('/v1/models/active', payload)
}

/** 参数 profile 列表 */
export function fetchProfiles(): Promise<Profile[]> {
  return getJson<Profile[]>('/v1/profiles')
}

/** 新建或更新 profile（后端按 id 是否存在裁决） */
export function saveProfile(profile: Profile): Promise<Profile> {
  return postJson<Profile>('/v1/profiles', profile)
}

/** 分词器清单与探测结果 */
export function fetchTokenizers(): Promise<TokenizerListResponse> {
  return getJson<TokenizerListResponse>('/v1/tokenizers')
}
