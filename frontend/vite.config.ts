import { defineConfig, loadEnv } from 'vite'
import vue from '@vitejs/plugin-vue'

// X-WORLD 前端构建配置。
// 开发服务器将 /v1 代理到本地后端，地址由 VITE_XW_BACKEND 决定，
// 默认 http://127.0.0.1:8760；运行期前端不访问任何其他地址。
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  const backend = env.VITE_XW_BACKEND || 'http://127.0.0.1:8760'
  return {
    plugins: [vue()],
    server: {
      proxy: {
        '/v1': {
          target: backend,
          changeOrigin: false,
        },
      },
    },
    build: {
      outDir: 'dist',
      sourcemap: false,
    },
    test: {
      include: ['src/**/*.test.ts'],
      environment: 'node',
    },
  }
})
