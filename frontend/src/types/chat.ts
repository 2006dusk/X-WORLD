// X-WORLD 前后端接口类型。
// 形状严格遵循 docs/项目实现/构建契约.md 第 4 节，前端不扩展后端语义。

/** 统一错误形状，见契约 4.4 */
export interface XwError {
  code: string
  message: string
  retryable: boolean
}

/** 预算占用，见契约 4.1 usage.budget */
export interface BudgetInfo {
  limit: number
  reserved_output: number
  used: number
}

/** 计量结构，见契约 4.1 usage */
export interface UsageInfo {
  prompt_tokens: number
  completion_tokens: number
  reasoning_tokens: number
  cached_tokens: number
  budget: BudgetInfo
  /** 生成速率，每秒输出 token 数 */
  generation_tps?: number | null
}

/** 装配回执单段，见契约 4.1 context_report.segments */
export interface ContextSegment {
  name: string
  tokens: number
  /** world_info 段携带的命中条目 id */
  entries?: string[]
  /** memory 段携带的命中条数 */
  hits?: number
}

/** 装配回执，见契约 4.1 context_report */
export interface ContextReport {
  segments: ContextSegment[]
  template: string
  tokenizer: string
  truncated: boolean
}

/** 会话请求扩展字段，见契约 4.1 xw */
export interface XwRequestExtra {
  character_id: string
  preset_id: string
  world_book_ids: string[]
  /** 对话所在的世界区域：世界书里的一条区域条目 */
  region_id: string
  memory_mode: 'none' | 'summary' | 'vector' | 'hybrid'
  max_context_tokens: number
  /**
   * 自动回合：用户这一轮不说话，由 AI 自己往下走。
   * continue 是接住角色上一轮的话继续推进，describe 是只推进场景描写。
   * 空串是普通轮次，用户说了话。
   */
  auto_turn: AutoTurnMode
}

/** 自动回合模式，见契约 4.1 xw.auto_turn */
export type AutoTurnMode = '' | 'continue' | 'describe'

export type ChatRole = 'system' | 'user' | 'assistant'

export interface ChatMessage {
  role: ChatRole
  content: string
  /** 结构化回合的块内容，块名是客户端自己的输出契约 */
  blocks?: Record<string, string> | null
  /** 结构化回合的块展示标签 */
  block_labels?: Record<string, string> | null
}

/** 结构化回合的块内容与展示标签 */
export interface ChatStructure {
  blocks: Record<string, string>
  labels: Record<string, string>
}

/** 世界状态，见契约 4.1 xw.state */
/** 切换菜单里的一个区域：带所属世界书的名字。 */
export interface SwitchRegion {
  id: string
  name: string
  book_id: string
  book_name: string
}

/** 一个角色已有的对话。地区跟着人走，列表里显示他人在哪。 */
export interface SwitchConversation {
  id: string
  message_count: number
  region_id: string
}

export interface WorldState {
  /** 人物名称：状态按角色存，这里显示是谁在这儿 */
  character_name: string
  /** 当前场景（地点） */
  location: string
  /** 人物位置（地点内的具体站位） */
  position: string
  /** 人物物品（角色随身带的东西） */
  items: string[]
  /** 场景物品（当前场景里存在的物件，不属于角色） */
  scene_items: string[]
  /** 地图 */
  map: string
  /** 时间 */
  time: string
  /** 最近一次更新时间 */
  updated_at: string | null
}

/** 会话分支节点，见契约 4.5 */
export interface SessionNode {
  id: string
  parent_id: string
  role: 'user' | 'assistant'
  /** 当前路径上的节点带正文与块 */
  content?: string
  blocks?: Record<string, string> | null
  /** 节点摘要，树上显示用 */
  summary: string
  created_at?: string | null
  /** 子节点 id，分支树的连线依据 */
  children?: string[]
}

/** 会话快照：当前路径与整棵分支树 */
export interface SessionSnapshot {
  head_id: string
  /** 当前路径的消息，顺序即对话顺序 */
  items: SessionNode[]
  /** 全部节点，含兄弟分支 */
  tree: SessionNode[]
  /** 块名到展示标签 */
  labels: Record<string, string>
  count: number
  /** 编辑定稿时后端写回的世界状态 */
  state?: WorldState | null
}

/** 非流式响应，见契约 4.1 */
export interface ChatCompletionResponse {
  id: string
  choices: Array<{
    index: number
    message: ChatMessage
    finish_reason: string
  }>
  usage: UsageInfo
  xw: {
    context_report: ContextReport
    /** 世界状态，无状态时为 null */
    state?: WorldState | null
    /** 被过滤掉的场景陈设：模型编出来的，供用户确认 */
    dropped_items?: string[]
    /** 会话路径与分支树 */
    path?: SessionSnapshot
  }
}

/** 流式固定事件之一，见契约 4.1 SSE 表 */
export type StreamEvent =
  | { type: 'context'; segments: ContextSegment[]; template: string; tokenizer: string }
  | { type: 'delta'; content: string; block?: string }
  | { type: 'reasoning'; content: string }
  | { type: 'usage'; usage: UsageInfo }
  | { type: 'error'; code: string; message: string; retryable: boolean }
  | { type: 'replace'; reason: string }
  | { type: 'blocks'; blocks: Record<string, string>; labels: Record<string, string> }
  | { type: 'state'; state: WorldState; dropped_items?: string[] }
  | { type: 'path'; snapshot: SessionSnapshot }
  | { type: 'done'; finish_reason: string }

/** 装配预览单条消息，见契约 4.2 */
export interface PreviewMessage {
  role: ChatRole
  content: string
  source: string
}

/** 装配预览响应，见契约 4.2 */
export interface PromptPreviewResponse {
  segments: ContextSegment[]
  template: string
  tokenizer: string
  truncated: boolean
  messages: PreviewMessage[]
}

/** 模型清单项，见契约 4.3 /v1/models */
export interface ModelInfo {
  id: string
  label: string
  provider: string
  /** 上下文窗口长度，后端字段名为 context_length */
  context_length: number
  supports_reasoning: boolean
}

/** 参数 profile，见契约 4.3 /v1/profiles */
export interface Profile {
  id: string
  name: string
  params: Record<string, unknown>
}

/** 分词器清单项，见契约 4.3 /v1/tokenizers */
export interface TokenizerInfo {
  name: string
  available: boolean
  approximate: boolean
  description: string
}

/** 分词器清单响应，见契约 4.3 /v1/tokenizers */
export interface TokenizerListResponse {
  tokenizers: TokenizerInfo[]
  default: string
  probe_result: {
    llamacpp_available: boolean
    test_text: string
    approx_count: number
    llamacpp_count: number
  }
}
