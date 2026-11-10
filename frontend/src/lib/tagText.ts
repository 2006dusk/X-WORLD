// 角色卡标签的文本形态与数组形态互转。
//
// 拼接用顿号，切分就必须也认顿号。原来拼接用「、」而切分只按「，」与「,」，
// 在标签框里加一个词就会把整串变成一个标签；标签进提示词时于是变成
// 「角色类型标签（必须符合）：四爱、女性支配、女攻男受」一条。

/** 标签之间允许的分隔符：中英文逗号与顿号。 */
const SEPARATORS = /[、，,]/

/** 标签数组拼成输入框里的一行文本。 */
export function joinTags(tags: string[] | undefined): string {
  if (!Array.isArray(tags)) return ''
  return tags
    .map((item) => (typeof item === 'string' ? item.trim() : ''))
    .filter(Boolean)
    .join('、')
}

/** 输入框文本拆成标签数组：去掉空项与重复。 */
export function splitTags(raw: string): string[] {
  const result: string[] = []
  for (const part of String(raw ?? '').split(SEPARATORS)) {
    const name = part.trim()
    if (name && !result.includes(name)) result.push(name)
  }
  return result
}

/** 点一下候选标签：有就去掉，没有就加上。 */
export function toggleTag(tags: string[], name: string): string[] {
  if (tags.includes(name)) return tags.filter((item) => item !== name)
  return [...tags, name]
}
