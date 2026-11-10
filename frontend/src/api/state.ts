// 世界状态接口：读取角色级世界状态。
// 状态按角色存：每个人有自己的位置与随身物品，世界本身是共用的。
// 状态由模型每轮回复自动更新，后端落盘，这里只读。
import { getJson } from './http'
import type { WorldState } from '../types/chat'

/** 读取世界状态；没有状态时后端返回字段齐备的空对象。 */
export function fetchWorldState(characterId?: string): Promise<WorldState> {
  const query = characterId ? `?character_id=${encodeURIComponent(characterId)}` : ''
  return getJson<WorldState>(`/v1/state/${query}`)
}
