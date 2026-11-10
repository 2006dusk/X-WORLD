// 角色声线 mock 数据。写操作只回显，不改动模块常量。
export const MOCK_VOICE_VECTORS = [
  { name: 'voice_001.f32', bytes: 507904, compatible: true },
  { name: 'modern_dense_cvec.f32', bytes: 507904, compatible: true },
  { name: 'style_cvec_k4.f32', bytes: 507904, compatible: false },
]

export const MOCK_VOICE_ITEMS = [
  { character_id: 'c_demo_a', name: '角色K', role: 'primary', file: '', scale: 0, layer_start: 1, layer_end: -1, active: false },
  { character_id: 'c_imported', name: '导入角色', role: 'primary', file: '', scale: 0, layer_start: 1, layer_end: -1, active: false },
]

export const voicesMockHandlers: Record<
  string,
  (path: string, method: string, body: unknown) => unknown
> = {
  'GET /v1/voices/': () => ({
    items: MOCK_VOICE_ITEMS,
    vectors: MOCK_VOICE_VECTORS,
    model: {
      model_desc: 'mock model',
      n_embd: 4096,
      n_layers: 32,
      expected_bytes: 507904,
    },
    max_scale: 0.6,
  }),
  'PUT /v1/voices/:id': (path, _method, body) => ({
    ok: true,
    character_id: decodeURIComponent(path.split('/').pop() ?? ''),
    config: body,
  }),
  'DELETE /v1/voices/:id': (path) => ({
    ok: true,
    character_id: decodeURIComponent(path.split('/').pop() ?? ''),
    removed: true,
  }),
  'POST /v1/voices/generate': (_path, _method, body) => ({
    ok: true,
    character_id:
      body && typeof body === 'object' && 'character_id' in body
        ? String((body as { character_id: string }).character_id)
        : '',
    file: 'C:/mock/vectors/runtime.f32',
    scale: 0.35,
    ratio: 0.02,
    generate: { bytes: 507904, layers: 31, n_embd: 4096, pairs: 3 },
    sweep: [
      {
        scale: 0.2,
        verdict: 'weak',
        character_delta: 0.01,
        control_delta: 0,
        score: 0.01,
      },
      {
        scale: 0.35,
        verdict: 'pass',
        character_delta: 0.09,
        control_delta: -0.05,
        score: 0.09,
      },
      {
        scale: 0.5,
        verdict: 'pass',
        character_delta: 0.06,
        control_delta: -0.12,
        score: 0.06,
      },
    ],
    verify: {
      verdict: 'pass',
      character_delta: 0.09,
      control_delta: -0.05,
      notes: ['mock'],
    },
  }),
  'POST /v1/voices/verify': () => ({
    ok: true,
    verify: {
      verdict: 'pass',
      character_delta: 0.09,
      control_delta: -0.05,
      notes: ['mock'],
    },
  }),
  'POST /v1/voices/conformance': (_path, _method, body) => ({
    ok: true,
    character_id:
      body && typeof body === 'object' && 'character_id' in body
        ? String((body as { character_id: string }).character_id)
        : '',
    verdict: 'pass',
    issues: [],
    layers: [
      { verdict: 'pass', issues: [], metrics: { chars: 120, has_dialogue: true } },
      { verdict: 'pass', issues: [], metrics: { declared_self_reference: '我' } },
      {
        verdict: 'pass',
        issues: [],
        metrics: { character_delta: 0.09, control_delta: -0.05 },
      },
    ],
  }),
}