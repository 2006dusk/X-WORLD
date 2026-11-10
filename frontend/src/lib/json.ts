// 响应式安全的深拷贝工具。
//
// 组件 props 里的对象多来自 ref 包裹的深层响应式对象，模板解包后传给子组件的
// 是 reactive Proxy。structuredClone 无法克隆 Proxy，浏览器会抛
// DOMException: Proxy object could not be cloned。
// 因此克隆前先逐层剥离代理，再交给 structuredClone 做真正的深拷贝。
import { toRaw } from 'vue'

function unwrap(value: unknown): unknown {
  const raw = toRaw(value as Record<string, unknown>)
  if (Array.isArray(raw)) return raw.map(unwrap)
  if (raw !== null && typeof raw === 'object') {
    const proto = Object.getPrototypeOf(raw)
    if (proto === Object.prototype || proto === null) {
      const out: Record<string, unknown> = {}
      for (const [key, item] of Object.entries(raw as Record<string, unknown>)) {
        out[key] = unwrap(item)
      }
      return out
    }
  }
  return raw
}

/** 深拷贝可能来自响应式代理的数据，返回不与源共享引用的普通对象。 */
export function cloneJson<T>(value: T): T {
  return structuredClone(unwrap(value)) as T
}
