// 编辑源文本：把角色回复的块拼成一段可编辑文本。
// 段头用后端给的展示标签加书名号，与后端解析用的标记一致，
// 提交时整段发回后端，由后端按预设声明的结构拆块。
const BLOCK_ORDER = ['action', 'scene', 'dialogue', 'state']

/** 块顺序固定：动作、场景、对白、状态，其余按原顺序跟在后面。 */
function orderedBlocks(blocks: Record<string, string>): string[] {
  const names = Object.keys(blocks)
  const known = BLOCK_ORDER.filter((name) => names.includes(name))
  const rest = names.filter((name) => !BLOCK_ORDER.includes(name))
  return [...known, ...rest]
}

/**
 * 拼出可编辑的源文本。
 *
 * 没有结构化块时返回空串，调用方退回纯正文编辑。
 */
export function buildEditSource(
  blocks: Record<string, string> | null | undefined,
  labels: Record<string, string>,
): string {
  if (!blocks) return ''
  const parts: string[] = []
  for (const name of orderedBlocks(blocks)) {
    const body = (blocks[name] ?? '').trim()
    if (!body) continue
    const label = labels[name] ?? name
    parts.push('【' + label + '】\n' + body)
  }
  return parts.join('\n\n')
}
