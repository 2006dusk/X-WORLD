// Mock 适配层：fetch 拦截器。
// 仅当 VITE_XW_MOCK 为真时 install()，mock 数据严格遵循契约 4 节形状。
import type { ChatRequest } from '../api/chat'
import type { StreamEvent } from '../types/chat'
import {
  MOCK_MODELS,
  MOCK_PREVIEW,
  MOCK_PROFILES,
  MOCK_SEGMENTS,
  MOCK_TEMPLATE,
  MOCK_TOKENIZER_LIST,
  MOCK_TOKENIZER,
  MOCK_USAGE,
} from './data'
import { cardsMockHandlers } from './cards'
import { worldbooksMockHandlers } from './worldbooks'
import { pluginsMockHandlers } from './plugins'
import { settingsMockHandlers } from './settings'
import { sessionsMockHandlers } from './sessions'
import { presetsMockHandlers } from './presets'
import { memoryMockHandlers } from './memory'
import { voicesMockHandlers } from './voices'

/** mock 处理器签名：路径、方法与已解析的请求体 */
type MockHandler = (path: string, method: string, body: unknown) => unknown

const MOCK_ROUTES: Array<[string, MockHandler]> = [
  ...Object.entries(cardsMockHandlers),
  ...Object.entries(worldbooksMockHandlers),
  ...Object.entries(pluginsMockHandlers),
  ...Object.entries(settingsMockHandlers),
  ...Object.entries(sessionsMockHandlers),
  ...Object.entries(presetsMockHandlers),
  ...Object.entries(memoryMockHandlers),
  ...Object.entries(voicesMockHandlers),
]

/** 路径模式匹配：冒号段为通配，其余必须逐段相等。 */
function matchPattern(pattern: string, path: string): boolean {
  const patternParts = pattern.split('/')
  const pathParts = path.split('/')
  if (patternParts.length !== pathParts.length) return false
  return patternParts.every((part, index) => part.startsWith(':') || part === pathParts[index])
}

/**
 * 按 方法 + 路径 查处理器。
 * 精确路径优先于通配路径，长路径优先于短路径。
 */
function matchHandler(path: string, method: string): MockHandler | null {
  const candidates = MOCK_ROUTES.filter(([key]) => key.startsWith(`${method} `)).sort((a, b) => {
    const wildA = a[0].split(':').length - 1
    const wildB = b[0].split(':').length - 1
    if (wildA !== wildB) return wildA - wildB
    return b[0].length - a[0].length
  })
  for (const [key, handler] of candidates) {
    const pattern = key.slice(method.length + 1)
    if (matchPattern(pattern, path)) return handler
  }
  return null
}

const sleep = (ms: number): Promise<void> => new Promise((r) => setTimeout(r, ms))

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

function sseFrame(event: string, payload: unknown): string {
  return `event: ${event}\ndata: ${JSON.stringify(payload)}\n\n`
}

/** mock 的世界状态，与后端 xw.state 形状一致 */
const MOCK_STATE = {
  character_name: '角色K',
  location: '地址N·地址U二楼',
  position: '控制台前',
  items: ['磁带', '老花镜'],
  scene_items: ['推子', '落地监听音箱'],
  map: '地址N',
  time: '雨夜',
  updated_at: '2026-11-06T12:00:00+00:00',
}

const REASONING_TEXT =
  '用户要求续写当前场景。我需要先回顾设定：角色是图书管理员，场景在闭馆后的阅览室。'
const DELTA_TEXT =
  '她合上书，指尖还停留在扉页的烫金字迹上。“你已经站了很久了，”她没有抬头，“要借这本书吗？”'

/** 生成一段符合契约的 SSE 流（thinking 模型多一轮 reasoning 事件） */
export function buildMockSseStream(req: ChatRequest): string {
  const thinking = req.model === 'xw-local-thinking'
  const parts: string[] = []
  parts.push(
    sseFrame('context', {
      segments: MOCK_SEGMENTS,
      template: MOCK_TEMPLATE,
      tokenizer: MOCK_TOKENIZER,
    }),
  )
  if (thinking) {
    for (const ch of REASONING_TEXT) {
      parts.push(sseFrame('reasoning', { content: ch }))
    }
  }
  for (const ch of DELTA_TEXT) {
    parts.push(sseFrame('delta', { content: ch }))
  }
  parts.push(sseFrame('usage', MOCK_USAGE))
  parts.push(sseFrame('state', { state: MOCK_STATE }))
  parts.push(sseFrame('done', { finish_reason: 'stop' }))
  return parts.join('')
}

export function mockFetch(input: RequestInfo | URL, init?: RequestInit): Promise<Response> {
  const url = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url
  const path = url.replace(/^https?:\/\/[^/]+/, '')
  const method = init?.method ?? 'GET'

  // 资源管理
  if (path === '/v1/models' && method === 'GET') {
    return Promise.resolve(jsonResponse(MOCK_MODELS))
  }
  if (path === '/v1/models/active' && method === 'POST') {
    return Promise.resolve(jsonResponse({ ok: true }))
  }
  if (path === '/v1/profiles' && method === 'GET') {
    return Promise.resolve(jsonResponse(MOCK_PROFILES))
  }
  if (path === '/v1/profiles' && method === 'POST') {
    return Promise.resolve(jsonResponse(JSON.parse(String(init?.body))))
  }
  if (path === '/v1/tokenizers' && method === 'GET') {
    return Promise.resolve(jsonResponse(MOCK_TOKENIZER_LIST))
  }

  // 装配预览
  if (path.startsWith('/v1/prompt-preview') && method === 'GET') {
    return Promise.resolve(jsonResponse(MOCK_PREVIEW))
  }

  // 世界状态
  if (path.startsWith('/v1/state') && method === 'GET') {
    return Promise.resolve(jsonResponse(MOCK_STATE))
  }

  // 会话生成
  if (path === '/v1/chat/completions' && method === 'POST') {
    const req = JSON.parse(String(init?.body)) as ChatRequest
    if (req.stream) {
      const sse = buildMockSseStream(req)
      return Promise.resolve(
        new Response(sse, {
          status: 200,
          headers: { 'Content-Type': 'text/event-stream' },
        }),
      )
    }
    return Promise.resolve(
      jsonResponse({
        id: 'cmpl_mock',
        choices: [
          {
            index: 0,
            message: { role: 'assistant', content: DELTA_TEXT },
            finish_reason: 'stop',
          },
        ],
        usage: MOCK_USAGE,
        xw: {
          context_report: {
            segments: MOCK_SEGMENTS,
            template: MOCK_TEMPLATE,
            tokenizer: MOCK_TOKENIZER,
            truncated: false,
          },
        },
      }),
    )
  }

  // 会话逐字稿导出为文本，不走 JSON 包装
  if (method === 'GET' && /^\/v1\/sessions\/[^/]+\/export$/.test(path)) {
    return Promise.resolve(
      new Response('# 会话逐字稿\n\n## user\n\n你好\n', {
        status: 200,
        headers: { 'Content-Type': 'text/markdown; charset=utf-8' },
      }),
    )
  }

  // 资源视图：按 方法 + 路径模式 匹配
  const handler = matchHandler(path.split('?')[0], method)
  if (handler) {
    let body: unknown = null
    if (init?.body) {
      try {
        body = JSON.parse(String(init.body))
      } catch {
        body = null
      }
    }
    return Promise.resolve(jsonResponse(handler(path.split('?')[0], method, body)))
  }

  return Promise.resolve(
    jsonResponse(
      { error: { code: 'not_found', message: `mock 未实现 ${method} ${path}`, retryable: false } },
      404,
    ),
  )
}

/** 安装 mock fetch 拦截；返回卸载函数。 */
export function installMock(): () => void {
  const original = window.fetch.bind(window)
  const patched: typeof fetch = (input, init) => {
    const url = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url
    if (url.startsWith('/v1')) {
      // 模拟网络延迟，让流式可见
      return sleep(80).then(() => mockFetch(input, init))
    }
    return original(input, init)
  }
  window.fetch = patched
  return () => {
    window.fetch = original
  }
}

/** 供测试与组件复用的事件序列（与 SSE 文本同源） */
export function buildMockEvents(req: ChatRequest): StreamEvent[] {
  const thinking = req.model === 'xw-local-thinking'
  const events: StreamEvent[] = [
    {
      type: 'context',
      segments: MOCK_SEGMENTS,
      template: MOCK_TEMPLATE,
      tokenizer: MOCK_TOKENIZER,
    },
  ]
  if (thinking) {
    events.push({ type: 'reasoning', content: REASONING_TEXT })
  }
  events.push({ type: 'delta', content: DELTA_TEXT })
  events.push({ type: 'usage', usage: MOCK_USAGE })
  events.push({ type: 'state', state: MOCK_STATE })
  events.push({ type: 'done', finish_reason: 'stop' })
  return events
}
