// 角色定位：primary 是主要人物（V1），supporting 是对话中引入的角色（V2）。
//
// 字段名用 role 而不是 v1/v2：卡片规范里已经有 spec_version 的 v1/v2/v3，
// 再用 v1/v2 命名角色分区会撞车。界面文案仍叫 V1 主要人物 / V2 引入角色。

export type CardRole = 'primary' | 'supporting'

export interface RoleOption {
  value: CardRole
  label: string
  hint: string
}

export const ROLE_OPTIONS: RoleOption[] = [
  {
    value: 'primary',
    label: 'V1 主要人物',
    hint: '由你构建的主要角色，进角色段，每轮都在，是这一轮扮演的对象。',
  },
  {
    value: 'supporting',
    label: 'V2 引入角色',
    hint: '对话中由模型或你带进来的角色，走配角段：名字被提到时才在场，位置在角色段之前。',
  },
]

/** 认不出来的定位按主要人物处理：配角是少数派，猜错时留在主角位更安全。 */
export function normalizeRole(role?: string | null): CardRole {
  return String(role ?? '').trim().toLowerCase() === 'supporting' ? 'supporting' : 'primary'
}

// 关系程度三档：配角与主角的关系是程度，不是自由句。
export const RELATION_LEVELS = ['相逢', '相识', '相熟'] as const
export type RelationLevel = (typeof RELATION_LEVELS)[number]

/** 认不出来的关系按「相逢」处理：随机路过的人只是相逢。 */
export function normalizeRelationLevel(relation?: string | null): RelationLevel {
  const text = String(relation ?? '').trim()
  return (RELATION_LEVELS as readonly string[]).includes(text)
    ? (text as RelationLevel)
    : '相逢'
}

// 引入来源：用户带出来的，还是模型自己带出来的。
export const INTRO_SOURCE_LABELS: Record<string, string> = {
  user: '用户引入',
  ai: '模型引入',
}

export function introSourceLabel(source?: string | null): string {
  const key = String(source ?? '').trim().toLowerCase()
  return INTRO_SOURCE_LABELS[key] ?? '模型引入'
}

/** 按角色定位分成两组：V1 主要人物在前，V2 引入角色在后。 */
export function groupCardsByRole<T extends { role?: string }>(cards: T[]): {
  primary: T[]
  supporting: T[]
} {
  const primary: T[] = []
  const supporting: T[] = []
  for (const card of cards) {
    if (normalizeRole(card.role) === 'supporting') supporting.push(card)
    else primary.push(card)
  }
  return { primary, supporting }
}
