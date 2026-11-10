// 上下文窗口：把装配回执的段归成看得懂的分类，算出占比。
//
// 组件只负责画，计算都在这里，方便单测。
// 数据来源是后端的 context_report（分段 token）与 usage.budget（占用与上限），
// 前端不自己数 token。

import type { BudgetInfo, ContextReport } from '../types/chat'

/** 分类定义：标签、色板序号、归到这一类的段名。 */
const CATEGORIES: { label: string; tone: number; names: string[] }[] = [
  { label: '对话消息', tone: 0, names: ['history', 'user'] },
  { label: '系统提示词', tone: 1, names: ['system', 'persona', 'preset_prompts'] },
  { label: '记忆', tone: 2, names: ['memory'] },
  { label: '世界书', tone: 3, names: ['world_info'] },
  { label: '角色卡', tone: 4, names: ['character'] },
  { label: '后置提示', tone: 5, names: ['post_history'] },
]

/** 认不出来的段归到这里。 */
const OTHER_LABEL = '其他'
const OTHER_TONE = 6

export interface ContextCategory {
  label: string
  tokens: number
  /** 占总量的百分数，保留一位小数 */
  pct: number
  /** 色板序号，对应样式里的 tone-N */
  tone: number
}

export interface ContextUsage {
  /** 已占用的上下文 token */
  used: number
  /** 上下文窗口上限 */
  limit: number
  /** 预留给输出的 token，没有用量回执时是零 */
  reserved: number
  /** 占用率百分数 */
  pct: number
}

/**
 * 上下文窗口的占用。
 *
 * 有用量回执时用回执的三个数；没有回执时按装配回执的分段合计算已用，
 * 上限取请求里的 max_context_tokens。刚打开会话时就是这个口径：
 * 上一轮的用量还没到，先按此刻的装配结果算。
 */
export function contextUsage(
  budget: BudgetInfo | null,
  report: ContextReport | null,
  limit = 0,
): ContextUsage {
  const fromReport = (report?.segments ?? []).reduce(
    (sum, segment) => sum + Math.max(0, Number(segment?.tokens ?? 0)),
    0,
  )
  const used = budget ? budget.used : fromReport
  const total = budget ? budget.limit : limit
  const reserved = budget ? budget.reserved_output : 0
  return {
    used,
    limit: total,
    reserved,
    pct: total > 0 ? Math.min(100, (used / total) * 100) : 0,
  }
}

/**
 * 把装配回执的段归类并算占比。
 *
 * 占比是这一类在总量里的份额，按份额从大到小排：最多的在最上面，
 * 与参考样式一致。段名认不出来的归「其他」。
 */
export function contextCategories(report: ContextReport | null): ContextCategory[] {
  const buckets = new Map<string, { label: string; tone: number; tokens: number }>()
  let total = 0
  for (const segment of report?.segments ?? []) {
    const tokens = Math.max(0, Number(segment?.tokens ?? 0))
    if (!tokens) continue
    const hit = CATEGORIES.find((item) => item.names.includes(segment.name))
    const label = hit ? hit.label : OTHER_LABEL
    const tone = hit ? hit.tone : OTHER_TONE
    const bucket = buckets.get(label) ?? { label, tone, tokens: 0 }
    bucket.tokens += tokens
    buckets.set(label, bucket)
    total += tokens
  }
  const rows = [...buckets.values()]
    .filter((row) => row.tokens > 0)
    .map((row) => ({
      label: row.label,
      tone: row.tone,
      tokens: row.tokens,
      pct: total > 0 ? Math.round((row.tokens / total) * 1000) / 10 : 0,
    }))
    .sort((a, b) => b.tokens - a.tokens)
  // 一位小数四舍五入会把合计带偏（三个 33.3 加起来是 99.9）。
  // 差额补给最大的一项：界面上这一列加起来正好一百。
  if (rows.length > 0) {
    const sum = rows.reduce((acc, row) => acc + row.pct, 0)
    const diff = Math.round((100 - sum) * 10) / 10
    if (diff !== 0) {
      rows[0].pct = Math.round((rows[0].pct + diff) * 10) / 10
    }
  }
  return rows
}
