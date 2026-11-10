// 会话生成接口：流式与非流式共用同一渲染数据结构。
// 前端只做传输与事件归组，prompt 拼装、token 计数、chat template 全在后端。
import { getJson, postJson } from './http'
import type {
  ChatCompletionResponse,
  ChatMessage,
  ContextReport,
  PromptPreviewResponse,
  SessionSnapshot,
  StreamEvent,
  WorldState,
  XwRequestExtra,
} from '../types/chat'

/** 轮次动作：append 追加一轮；regenerate 重跑某一轮；revise 改写某一轮 */
export type TurnAction = 'append' | 'regenerate' | 'revise'

export interface ChatRequest {
  model: string
  messages: ChatMessage[]
  session: string
  stream: boolean
  xw: XwRequestExtra
  /** 缺省为 append */
  turn_action?: TurnAction
  /** 要改写的节点 id：重新生成给角色回复的节点，改写给用户输入的节点 */
  target_id?: string
}

/** 非流式会话生成，见契约 4.1 */
export function chatComplete(req: ChatRequest): Promise<ChatCompletionResponse> {
  return postJson<ChatCompletionResponse>('/v1/chat/completions', req)
}

/** 编辑角色回复并定稿，见契约 4.5。 */
export interface EditTurnRequest {
  session: string
  /** 被改那一轮的父节点，新回复挂在它下面 */
  parent_id: string
  /** 完整的编辑源文本，含动作、场景、对白与状态四段 */
  content: string
  xw: XwRequestExtra
}

export function editTurn(req: EditTurnRequest): Promise<SessionSnapshot> {
  return postJson<SessionSnapshot>('/v1/chat/edit', {
    model: 'xw-local-default',
    session: req.session,
    parent_id: req.parent_id,
    content: req.content,
    xw: req.xw,
  })
}

/** 装配预览，见契约 4.2。world_book_ids 以逗号分隔重复参数传递。 */
export function fetchPromptPreview(params: {
  session: string
  character_id: string
  preset_id: string
  world_book_ids: string[]
  user_input: string
}): Promise<PromptPreviewResponse> {
  const q = new URLSearchParams()
  q.set('session', params.session)
  q.set('character_id', params.character_id)
  q.set('preset_id', params.preset_id)
  for (const id of params.world_book_ids) q.append('world_book_ids', id)
  q.set('user_input', params.user_input)
  return getJson<PromptPreviewResponse>(`/v1/prompt-preview?${q.toString()}`)
}

export interface StreamCallbacks {
  onEvent: (ev: StreamEvent) => void
  /** 传输层失败（连接断开、解析失败）时回调，error 事件不会触发它 */
  onTransportError: (err: { message: string; retryable: boolean }) => void
}

/**
 * 流式会话生成，见契约 4.1 SSE。
 * 解析 `event:` + `data:` 帧，按固定六事件名分发；
 * 返回中止函数，调用后停止读取。
 */
export async function chatStream(req: ChatRequest, cb: StreamCallbacks): Promise<() => void> {
  let res: Response
  try {
    res = await fetch('/v1/chat/completions', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(req),
    })
  } catch {
    cb.onTransportError({ message: '无法连接本地后端', retryable: true })
    return () => undefined
  }
  if (!res.ok || !res.body) {
    let message = `请求失败（HTTP ${res.status}）`
    let retryable = true
    try {
      const body = (await res.json()) as { error?: { message?: string; retryable?: boolean } }
      if (body?.error?.message) message = body.error.message
      if (typeof body?.error?.retryable === 'boolean') retryable = body.error.retryable
    } catch {
      /* 保持默认 */
    }
    cb.onTransportError({ message, retryable })
    return () => undefined
  }

  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  let eventName = 'message'
  let closed = false
  // 是否收到过终止事件。收到 done 或 error 说明服务端正常收尾，
  // 此时连接关闭属于正常结束，不该报「流意外结束」。
  let sawTerminal = false

  const abort = (): void => {
    closed = true
    reader.cancel().catch(() => undefined)
  }

  const dispatch = (raw: string): void => {
    const data = raw.trim()
    if (!data) return
    let payload: unknown
    try {
      payload = JSON.parse(data)
    } catch {
      cb.onTransportError({ message: '流数据解析失败', retryable: true })
      return
    }
    const p = payload as Record<string, unknown>
    switch (eventName) {
      case 'context':
        cb.onEvent({
          type: 'context',
          segments: (p.segments as ContextReport['segments']) ?? [],
          template: (p.template as string) ?? '',
          tokenizer: (p.tokenizer as string) ?? '',
        })
        break
      case 'delta':
        cb.onEvent({
          type: 'delta',
          content: (p.content as string) ?? '',
          block: typeof p.block === 'string' ? p.block : undefined,
        })
        break
      case 'reasoning':
        cb.onEvent({ type: 'reasoning', content: (p.content as string) ?? '' })
        break
      case 'usage':
        cb.onEvent({ type: 'usage', usage: p as never })
        break
      case 'error':
        sawTerminal = true
        cb.onEvent({
          type: 'error',
          code: (p.code as string) ?? 'unknown_error',
          message: (p.message as string) ?? '未知错误',
          retryable: typeof p.retryable === 'boolean' ? p.retryable : false,
        })
        break
      case 'replace':
        cb.onEvent({
          type: 'replace',
          reason: (p.reason as string) ?? 'repetition',
        })
        break
      case 'blocks':
        cb.onEvent({
          type: 'blocks',
          blocks: (p.blocks as Record<string, string>) ?? {},
          labels: (p.labels as Record<string, string>) ?? {},
        })
        break
      case 'state':
        cb.onEvent({
          type: 'state',
          state: (p.state as WorldState) ?? {
            location: '',
            position: '',
            items: [],
            scene_items: [],
            map: '',
            time: '',
            updated_at: null,
          },
        })
        break
      case 'path':
        cb.onEvent({ type: 'path', snapshot: p as unknown as SessionSnapshot })
        break
      case 'done':
        sawTerminal = true
        cb.onEvent({ type: 'done', finish_reason: (p.finish_reason as string) ?? 'stop' })
        break
      default:
        /* 未知事件名忽略，保持连接 */
        break
    }
  }

  const pump = async (): Promise<void> => {
    while (!closed) {
      const { done, value } = await reader.read()
      if (closed) return
      if (done) {
        // 收到过 done 或 error 事件说明服务端正常收尾，连接关闭是正常结束；
        // 只有没收到终止事件就断开才算意外中断。
        if (!sawTerminal) {
          cb.onTransportError({ message: '流意外结束', retryable: true })
        }
        return
      }
      buffer += decoder.decode(value, { stream: true })
      // SSE 以空行分隔帧
      let sep: number
      while ((sep = buffer.indexOf('\n\n')) >= 0) {
        const frame = buffer.slice(0, sep)
        buffer = buffer.slice(sep + 2)
        for (const line of frame.split('\n')) {
          if (line.startsWith('event:')) {
            eventName = line.slice(6).trim()
          } else if (line.startsWith('data:')) {
            dispatch(line.slice(5))
          }
        }
      }
    }
  }

  pump().catch(() => {
    if (!closed) cb.onTransportError({ message: '连接中断', retryable: true })
  })
  return abort
}
