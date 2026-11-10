// 会话在列表里的说法。
//
// 侧栏的最近会话只写角色名：一个人一条对话，认得出是谁就够。
// 分支数只在真的分叉时才写，没有分叉就不占地方。

/** 分支数的中文说法：没有分叉就不写。 */
export function branchCountLabel(branchCount: number): string {
  return branchCount > 0 ? `${branchCount} 条分支` : ''
}
