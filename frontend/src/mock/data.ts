// Mock 数据：严格遵循 CONTRACT.md 第 4 节的响应形状。
// 仅在 VITE_XW_MOCK 为真时启用，供无后端环境演示与联调。
import type {
  ContextSegment,
  ModelInfo,
  Profile,
  PromptPreviewResponse,
  TokenizerInfo,
  TokenizerListResponse,
  UsageInfo,
} from '../types/chat'

export const MOCK_SEGMENTS: ContextSegment[] = [
  { name: 'system', tokens: 210 },
  { name: 'character', tokens: 480 },
  { name: 'preset_prompts', tokens: 640 },
  { name: 'post_history', tokens: 96 },
  { name: 'world_info', tokens: 380, entries: ['e_02', 'e_17'] },
  { name: 'memory', tokens: 220, hits: 3 },
  { name: 'history', tokens: 2197 },
]

export const MOCK_TEMPLATE = 'chatml'
export const MOCK_TOKENIZER = 'llama3'

export const MOCK_USAGE: UsageInfo = {
  prompt_tokens: 4127,
  completion_tokens: 186,
  reasoning_tokens: 42,
  cached_tokens: 3072,
  budget: { limit: 8192, reserved_output: 1024, used: 4127 },
}

export const MOCK_MODELS: ModelInfo[] = [
  {
    id: 'xw-local-default',
    label: '本地默认模型（llama.cpp）',
    provider: 'llama.cpp',
    context_length: 8192,
    supports_reasoning: false,
  },
  {
    id: 'xw-local-thinking',
    label: '本地思考模型',
    provider: 'llama.cpp',
    context_length: 16384,
    supports_reasoning: true,
  },
]

export const MOCK_PROFILES: Profile[] = [
  {
    id: 'p_default',
    name: '默认参数',
    params: {
      temperature: 0.7,
      top_p: 0.9,
      max_tokens: 1024,
      repetition_penalty: 1.05,
    },
  },
]

export const MOCK_TOKENIZERS: TokenizerInfo[] = [
  {
    name: 'approx',
    available: true,
    approximate: true,
    description: '字符启发式估算分词器',
  },
  {
    name: 'llamacpp',
    available: true,
    approximate: false,
    description: 'llama.cpp server 真实分词器',
  },
]

export const MOCK_TOKENIZER_LIST: TokenizerListResponse = {
  tokenizers: MOCK_TOKENIZERS,
  default: 'approx',
  probe_result: {
    llamacpp_available: true,
    test_text: '你好 world',
    approx_count: 3,
    llamacpp_count: 4,
  },
}

export const MOCK_PREVIEW: PromptPreviewResponse = {
  segments: MOCK_SEGMENTS,
  template: MOCK_TEMPLATE,
  tokenizer: MOCK_TOKENIZER,
  truncated: false,
  messages: [
    { role: 'system', content: '你是 roleplay 助手……', source: 'system' },
    { role: 'system', content: '角色描述摘要（截断至 200 字符）', source: 'character' },
    { role: 'user', content: '继续', source: 'history' },
  ],
}
