// 路由表：会话对话为默认页，其余资源视图各自独立。
import { createRouter, createWebHashHistory } from 'vue-router'
import ChatView from '../views/ChatView.vue'
import SessionsView from '../views/SessionsView.vue'
import CardsView from '../views/CardsView.vue'
import WorldBookView from '../views/WorldBookView.vue'
import MemoryView from '../views/MemoryView.vue'
import PluginsView from '../views/PluginsView.vue'
import ModelsView from '../views/ModelsView.vue'
import SettingsView from '../views/SettingsView.vue'
import DebugView from '../views/DebugView.vue'

export const router = createRouter({
  history: createWebHashHistory(),
  routes: [
    { path: '/', redirect: '/chat' },
    { path: '/chat', name: 'chat', component: ChatView },
    { path: '/sessions', name: 'sessions', component: SessionsView },
    { path: '/cards', name: 'cards', component: CardsView },
    { path: '/worldbooks', name: 'worldbooks', component: WorldBookView },
    { path: '/memory', name: 'memory', component: MemoryView },
    { path: '/plugins', name: 'plugins', component: PluginsView },
    { path: '/models', name: 'models', component: ModelsView },
    { path: '/settings', name: 'settings', component: SettingsView },
    { path: '/debug', name: 'debug', component: DebugView },
  ],
})
