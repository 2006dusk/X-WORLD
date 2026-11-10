// 应用入口：装配 Pinia、路由与主题；VITE_XW_MOCK 为真时装配 mock 适配层。
import { createApp } from 'vue'
import { createPinia } from 'pinia'
import App from './App.vue'
import { router } from './router'
import { installMock } from './mock'
import './styles/theme.css'

if (import.meta.env.VITE_XW_MOCK) {
  installMock()
}

createApp(App).use(createPinia()).use(router).mount('#app')
