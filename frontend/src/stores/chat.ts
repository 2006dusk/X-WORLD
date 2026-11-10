// 会话核心状态：消息流、流式生成状态机、装配回执、预算条。
// 六事件流固定顺序消费；非流式与流式共用同一渲染结构。
import { defineStore } from 'pinia'
import { chatComplete, chatStream, editTurn, fetchPromptPreview } from '../api/chat'
import type { TurnAction } from '../api/chat'
import {
  deleteBranch as deleteBranchRequest,
  fetchSession,
  fetchSessionSnapshot,
  switchBranch,
} from '../api/sessions'
import { fetchWorldState } from '../api/state'
import type {
  ChatMessage,
  ChatStructure,
  ContextReport,
  SessionNode,
  SessionSnapshot,
  StreamEvent,
  UsageInfo,
  WorldState,
} from '../types/chat'
import type { SessionSummary } from '../api/sessions'
import type { AutoTurnMode, XwRequestExtra } from '../types/chat'

export type TurnStatus = 'idle' | 'waiting_context' | 'streaming' | 'done' | 'error'

export interface DisplayMessage {
  id: string
  /** 服务端节点 id，未落盘前为空 */
  parentId?: string
  role: 'user' | 'assistant'
  /** 正文增量累积 */
  content: string
  /** 思考内容折叠显示，独立于正文 */
  reasoning: string
  usage: UsageInfo | null
  contextReport: ContextReport | null
  /** 结构化回合的块内容与展示标签 */
  structure: ChatStructure | null
  /** 完整块内容，含状态块。编辑源文本要用它，结构块不进气泡 */
  rawBlocks?: Record<string, string> | null
  /** 中断 / 出错的轮次可续写 */
  interrupted: boolean
  error: { code: string; message: string; retryable: boolean } | null
  createdAt: number
}

let seq = 0
function nextId(): string {
  seq += 1
  return `m_${Date.now().toString(36)}_${seq}`
}

/** 状态块只在右侧面板显示，不进对话气泡。 */
function bodyBlocks(
  blocks: Record<string, string> | null | undefined,
): Record<string, string> {
  if (!blocks) return {}
  const result: Record<string, string> = {}
  for (const [name, text] of Object.entries(blocks)) {
    if (name === 'state') continue
    result[name] = text
  }
  return result
}

export const useChatStore = defineStore('chat', {
  state: () => ({
    session: '',
    /** 当前会话的元信息：世界位置、主角色与在场角色 */
    sessionMeta: null as SessionSummary | null,
    messages: [] as DisplayMessage[],
    status: 'idle' as TurnStatus,
    /** 装配回执（context 事件落点） */
    contextReport: null as ContextReport | null,
    /** 最近一次 usage 事件（预算条数据源） */
    lastUsage: null as UsageInfo | null,
    /** 世界状态：模型每轮自动更新，右侧面板显示 */
    worldState: null as WorldState | null,
    /** 被过滤掉的场景陈设：模型编的，右侧面板提示用户确认 */
    droppedItems: [] as string[],
    /** 分支树节点，含兄弟分支 */
    tree: [] as SessionNode[],
    /** 当前路径末端的节点 id */
    headId: '',
    activeModel: 'xw-local-default',
    activeProfileId: 'p_default',
    xw: {
      // 当前发言角色：对话里 @ 谁就用谁的卡，本轮结束前不变
      character_id: '',
      // 预设与世界位置留空时由后端按默认预设与会话头解析
      preset_id: '',
      world_book_ids: [] as string[],
      // 对话所在的世界区域
      region_id: '',
      memory_mode: 'hybrid',
      max_context_tokens: 16384,
      // 自动回合：用户这一轮不说话，由 AI 自己往下走
      auto_turn: '' as AutoTurnMode,
    } as XwRequestExtra,
    streamingAbort: null as null | (() => void),
    /** 当前轮是否已收到 context（未收到时提示等待装配） */
    pendingContextNotice: false,
  }),
  getters: {
    busy: (s) => s.status === 'waiting_context' || s.status === 'streaming',
    /** 最后一轮用户消息的 id：修改按钮只对这一轮开放 */
    lastUserId: (s) => {
      for (let i = s.messages.length - 1; i >= 0; i -= 1) {
        if (s.messages[i].role === 'user') return s.messages[i].id
      }
      return ''
    },
    /** 最后一轮角色回复的 id：重新生成按钮只对这一轮开放 */
    lastAssistantId: (s) => {
      for (let i = s.messages.length - 1; i >= 0; i -= 1) {
        if (s.messages[i].role === 'assistant') return s.messages[i].id
      }
      return ''
    },
  },
  actions: {
    /** 应用流事件，六事件固定顺序，delta/reasoning 增量追加 */
    applyStreamEvent(ev: StreamEvent): void {
      const tail = this.messages[this.messages.length - 1]
      switch (ev.type) {
        case 'context':
          this.contextReport = {
            segments: ev.segments,
            template: ev.template,
            tokenizer: ev.tokenizer,
            truncated: false,
          }
          this.pendingContextNotice = false
          break
        case 'delta':
          // 状态块只进右侧面板，不进对话气泡
          if (ev.block === 'state') break
          if (tail && tail.role === 'assistant') {
            tail.content += ev.content
            if (ev.block) {
              if (!tail.structure) tail.structure = { blocks: {}, labels: {} }
              tail.structure.blocks[ev.block] =
                (tail.structure.blocks[ev.block] ?? '') + ev.content
            }
          }
          break
        case 'reasoning':
          if (tail && tail.role === 'assistant') tail.reasoning += ev.content
          break
        case 'usage':
          this.lastUsage = ev.usage
          if (tail) tail.usage = ev.usage
          break
        case 'error':
          this.status = 'error'
          if (tail) {
            tail.error = { code: ev.code, message: ev.message, retryable: ev.retryable }
            tail.interrupted = true
          }
          break
        case 'blocks':
          // 结构化回合的最终块，以后端解析结果为准
          if (tail && tail.role === 'assistant') {
            tail.structure = {
              blocks: bodyBlocks(ev.blocks),
              labels: { ...ev.labels },
            }
            tail.rawBlocks = { ...ev.blocks }
          }
          break
        case 'replace':
          // 后端判定本轮照搬了上一轮，已重采样；丢掉已显示的正文
          if (tail && tail.role === 'assistant') {
            tail.content = ''
            tail.reasoning = ''
            tail.structure = null
          }
          break
        case 'state':
          // 世界状态由模型每轮自动更新，事件落点与消息流无关
          this.worldState = ev.state
          this.droppedItems = ev.dropped_items ?? []
          break
        case 'path':
          // 会话路径与分支树：服务端是唯一权威，按它重建消息列表
          this.applySnapshot(ev.snapshot)
          break
        case 'done':
          this.status = 'done'
          break
      }
    },

    /**
     * 按服务端快照重建消息列表与分支树。
     *
     * 服务端是会话的唯一权威：落盘后的路径、块与节点 id 都以它为准。
     * 流式期间这条消息在本地的用量与思考内容要带过去，重建后不丢。
     */
    applySnapshot(snapshot: SessionSnapshot): void {
      const previous = this.messages[this.messages.length - 1]
      const usage = previous && previous.role === 'assistant' ? previous.usage : null
      const reasoning = previous && previous.role === 'assistant' ? previous.reasoning : ''
      this.messages = (snapshot.items ?? []).map((node) => ({
        id: node.id,
        parentId: node.parent_id,
        role: node.role,
        content: node.content ?? '',
        reasoning: '',
        usage: null,
        contextReport: null,
        structure: node.blocks
          ? { blocks: bodyBlocks(node.blocks), labels: snapshot.labels ?? {} }
          : null,
        rawBlocks: node.blocks ? { ...node.blocks } : null,
        interrupted: false,
        error: null,
        createdAt: 0,
      }))
      const tail = this.messages[this.messages.length - 1]
      if (tail && tail.role === 'assistant') {
        tail.usage = usage
        tail.reasoning = reasoning
      }
      this.tree = snapshot.tree ?? []
      this.headId = snapshot.head_id ?? ''
    },

    /** 读取当前会话的路径与分支树，进入会话页时用。 */
    async loadSession(): Promise<void> {
      if (!this.session) return
      try {
        const snapshot = await fetchSessionSnapshot(this.session, this.xw.character_id)
        this.applySnapshot(snapshot)
      } catch {
        /* 会话还没有内容时忽略 */
      }
    },

    /**
     * 打开一个会话。
     *
     * 会话与角色解耦：会话文件位置由会话自身决定，@ 换角色不会改位置。
     * 打开时按会话头恢复世界位置与主角色。
     */
    async openSession(sessionId: string): Promise<void> {
      const id = sessionId.trim()
      if (!id) return
      try {
        const meta = await fetchSession(id)
        this.session = id
        this.sessionMeta = meta
        this.xw.world_book_ids = [...(meta.world_book_ids ?? [])]
        this.xw.region_id = meta.region_id ?? ''
        this.xw.character_id = meta.character_id ?? ''
        await this.loadSession()
        await this.loadWorldState()
        await this.refreshContextReport()
      } catch {
        /* 会话读不到时保留当前状态 */
      }
    },

    /**
     * 刷新上下文窗口的装配回执。
     *
     * 刚打开会话时还没有用量事件，环是空的：预览接口按当前会话装配一次，
     * 拿到的分段就是此刻的上下文窗口占用。生成一轮之后由流里的 context
     * 事件接管，这里只在打开会话时补一次。
     */
    async refreshContextReport(): Promise<void> {
      if (!this.session) return
      try {
        const preview = await fetchPromptPreview({
          session: this.session,
          character_id: this.xw.character_id,
          preset_id: this.xw.preset_id,
          world_book_ids: this.xw.world_book_ids,
          user_input: '',
        })
        this.contextReport = {
          segments: preview.segments ?? [],
          template: preview.template ?? '',
          tokenizer: preview.tokenizer ?? '',
          truncated: Boolean(preview.truncated),
        }
      } catch {
        /* 预览取不到时保留原值，环退回空 */
      }
    },

    /**
     * 清掉当前会话上下文。
     *
     * 会话是一段对话：没选会话时不该留着上一段的世界位置与发言角色，
     * 否则页头显示的是上一段对话的信息，发消息也会续到旧会话里。
     */
    clearSession(): void {
      this.session = ''
      this.sessionMeta = null
      this.messages = []
      this.contextReport = null
      this.lastUsage = null
      this.worldState = null
      this.droppedItems = []
      this.tree = []
      this.headId = ''
      this.xw.world_book_ids = []
      this.xw.region_id = ''
      this.xw.character_id = ''
    },

    /**
     * 切换本轮发言角色。
     *
     * 只换角色卡，不动会话文件：同一段对话里可以轮流 @ 不同的角色。
     * 世界状态按角色存，换人后右侧面板读的是新角色的位置与随身物品。
     */
    async setSpeaker(cardId: string): Promise<void> {
      this.xw.character_id = cardId
      await this.loadWorldState()
    },

    /** 会话涉及的角色：主角色排第一个，其余是 @ 过的人。 */
    roster(): string[] {
      return this.sessionMeta?.characters ?? []
    },

    /**
     * 编辑角色回复并定稿。
     *
     * 新回复挂在被改那一轮的父节点下，与旧回复互为兄弟分支，可切回。
     * 编辑源文本含动作、场景、对白与状态四段，后端解析后正文进气泡、状态写回面板。
     */
    async editAssistant(messageId: string, text: string): Promise<void> {
      if (this.busy) return
      const content = text.trim()
      if (!content) return
      const target = this.messages.find((m) => m.id === messageId)
      if (!target) return
      try {
        const snapshot = await editTurn({
          session: this.session,
          parent_id: target.parentId ?? '',
          content,
          xw: { ...this.xw },
        })
        this.applySnapshot(snapshot)
        if (snapshot.state) this.worldState = snapshot.state
      } catch {
        /* 分支操作失败时保留原消息，不改本地状态 */
      }
    },

    /** 切换到指定分支叶子。 */
    async switchBranch(leafId: string): Promise<void> {
      if (this.busy || !leafId) return
      try {
        const snapshot = await switchBranch(this.session, leafId, this.xw.character_id)
        this.applySnapshot(snapshot)
      } catch {
        /* 切换失败时保留当前路径 */
      }
    },

    /**
     * 删掉一条分支的一段。
     *
     * nodeIds 是要删的节点集合：调用方按折叠后的分组算出这一段含哪几个
     * 节点。删完按后端返回的路径与分支树重建消息列表，别的支线照旧留着。
     */
    async deleteBranch(nodeIds: string[]): Promise<void> {
      if (this.busy || !this.session || nodeIds.length === 0) return
      try {
        const snapshot = await deleteBranchRequest(
          this.session,
          nodeIds,
          this.xw.character_id,
        )
        this.applySnapshot(snapshot)
      } catch {
        /* 删除失败时保留当前路径 */
      }
    },

    /** 读取角色级世界状态，面板首屏与切角色时用。 */
    async loadWorldState(): Promise<void> {
      try {
        this.worldState = await fetchWorldState(this.xw.character_id)
      } catch {
        this.worldState = null
      }
    },

    /** 发送用户消息，流式生成回复 */
    async send(content: string): Promise<void> {
      if (this.busy) return
      const text = content.trim()
      if (!text) return
      this.messages.push({
        id: nextId(),
        role: 'user',
        content: text,
        reasoning: '',
        usage: null,
        contextReport: null,
        structure: null,
        rawBlocks: null,
        interrupted: false,
        error: null,
        createdAt: Date.now(),
      })
      await this.generate()
    },

    /**
     * 自动回合：用户这一轮不说话，由 AI 自己往下走。
     *
     * continue 让 AI 接住自己上一轮的话继续往下推进；describe 只推进场景
     * 描写。请求里带 xw.auto_turn，后端按模式装配输入、放宽对白闸门，
     * 并在落盘时不记一条空的用户消息。
     */
    async autoTurn(mode: AutoTurnMode): Promise<void> {
      if (this.busy) return
      if (!this.session) return
      await this.generate('append', '', mode)
    },

    /** 生成一个 assistant 回复（发送、续写、重新生成与改写共用） */
    async generate(
      action: TurnAction = 'append',
      targetId = '',
      autoTurn: AutoTurnMode = '',
    ): Promise<void> {
      if (this.busy) return
      this.status = 'waiting_context'
      this.pendingContextNotice = true
      const assistantMsg: DisplayMessage = {
        id: nextId(),
        role: 'assistant',
        content: '',
        reasoning: '',
        usage: null,
        contextReport: null,
        structure: null,
        rawBlocks: null,
        interrupted: false,
        error: null,
        createdAt: Date.now(),
      }
      this.messages.push(assistantMsg)

      const history: ChatMessage[] = []
      for (const m of this.messages) {
        if (m === assistantMsg) break
        history.push({ role: m.role, content: m.content })
      }

      const req = {
        model: this.activeModel,
        messages: history,
        session: this.session,
        stream: true,
        xw: { ...this.xw, auto_turn: autoTurn },
        turn_action: action,
        target_id: targetId || undefined,
      }

      const abort = await chatStream(req, {
        onEvent: (ev) => {
          this.applyStreamEvent(ev)
          if (ev.type === 'context') assistantMsg.contextReport = this.contextReport
          if (ev.type === 'error' || ev.type === 'done') this.streamingAbort = null
        },
        onTransportError: (err) => {
          this.status = 'error'
          assistantMsg.error = {
            code: 'transport_error',
            message: err.message,
            retryable: err.retryable,
          }
          assistantMsg.interrupted = true
          this.streamingAbort = null
        },
      })
      this.streamingAbort = abort
    },

    /** 非流式路径，与流式共用 applyStreamEvent 的渲染结构（无分支差异） */
    async sendNonStream(content: string): Promise<void> {
      if (this.busy) return
      const text = content.trim()
      if (!text) return
      this.messages.push({
        id: nextId(),
        role: 'user',
        content: text,
        reasoning: '',
        usage: null,
        contextReport: null,
        structure: null,
        interrupted: false,
        error: null,
        createdAt: Date.now(),
      })
      this.status = 'waiting_context'
      this.pendingContextNotice = true
      const res = await chatComplete({
        model: this.activeModel,
        messages: this.messages.map((m) => ({ role: m.role, content: m.content })),
        session: this.session,
        stream: false,
        xw: { ...this.xw },
      })
      const assistantMsg: DisplayMessage = {
        id: nextId(),
        role: 'assistant',
        content: res.choices[0]?.message.content ?? '',
        reasoning: '',
        usage: res.usage,
        contextReport: res.xw.context_report,
        structure: res.choices[0]?.message.blocks
          ? {
              blocks: bodyBlocks(res.choices[0].message.blocks),
              labels: res.choices[0].message.block_labels ?? {},
            }
          : null,
        rawBlocks: res.choices[0]?.message.blocks
          ? { ...res.choices[0].message.blocks }
          : null,
        interrupted: false,
        error: null,
        createdAt: Date.now(),
      }
      this.messages.push(assistantMsg)
      this.applyStreamEvent({
        type: 'context',
        segments: res.xw.context_report.segments,
        template: res.xw.context_report.template,
        tokenizer: res.xw.context_report.tokenizer,
      })
      this.applyStreamEvent({ type: 'usage', usage: res.usage })
      this.applyStreamEvent({
        type: 'done',
        finish_reason: res.choices[0]?.finish_reason ?? 'stop',
      })
      assistantMsg.contextReport = this.contextReport
    },

    /**
     * 重新生成某一轮：把那条角色回复原地换掉，用同样的输入再答一次。
     *
     * 重新生成不新开节点，回复占住原来的位置，所以不算分支。被改那条
     * 回复之后的几轮留在树上但不在当前路径上，继续对话时另起一条分支，
     * 同一轮于是有两个版本。
     */
    async regenerate(messageId: string): Promise<void> {
      if (this.busy) return
      const index = this.messages.findIndex(
        (m) => m.id === messageId && m.role === 'assistant',
      )
      if (index < 0) return
      // 丢掉这条回复与其后的几轮，它们留在树上但不在当前路径上
      this.messages.splice(index)
      await this.generate('regenerate', messageId)
    },

    /**
     * 改写某一轮的用户输入：换成本文后重跑该轮。
     *
     * 新输入与旧输入互为兄弟，是真正的分叉：旧的那条线留在树上可切回。
     */
    async revise(messageId: string, text: string): Promise<void> {
      if (this.busy) return
      const content = text.trim()
      if (!content) return
      const index = this.messages.findIndex(
        (m) => m.id === messageId && m.role === 'user',
      )
      if (index < 0) return
      // 丢掉该轮之后的角色回复，本轮重新生成
      this.messages.splice(index + 1)
      this.messages[index].content = content
      await this.generate('revise', messageId)
    },

    /** 流中断 / 出错后的重试（重新生成当前轮） */
    async retry(): Promise<void> {
      const tail = this.messages[this.messages.length - 1]
      if (tail && tail.role === 'assistant') {
        tail.content = ''
        tail.reasoning = ''
        tail.error = null
        tail.interrupted = false
        this.messages.pop()
        await this.generate()
      }
    },

    /** 中断流的续写入口：保留已生成部分，请求后端继续 */
    async continueWrite(): Promise<void> {
      const tail = this.messages[this.messages.length - 1]
      if (this.busy) return
      if (tail && tail.role === 'assistant') {
        tail.error = null
        tail.interrupted = false
        await this.generate()
      }
    },

    /** 手动停止当前流 */
    stop(): void {
      if (this.streamingAbort) {
        this.streamingAbort()
        this.streamingAbort = null
      }
      const tail = this.messages[this.messages.length - 1]
      if (tail && tail.role === 'assistant' && this.status !== 'done') {
        tail.interrupted = true
        tail.error = {
          code: 'aborted',
          message: '已手动停止',
          retryable: false,
        }
      }
      this.status = 'idle'
      this.pendingContextNotice = false
    },
  },
})
