<script setup lang="ts">
// 三段式布局：左侧可折叠侧栏（导航 + 会话列表）+ 主内容区。
// 窄屏（≤768px）下侧栏变成抽屉：顶栏的菜单键拉出，点遮罩或导航项收起，
// 主区占满整宽。桌面端仍是固定侧栏加折叠键，折叠行为不变。
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import {
  PanelLeftClose,
  PanelLeftOpen,
  Menu,
  MessagesSquare,
  History,
  Library,
  BookOpen,
  Brain,
  Puzzle,
  Cpu,
  Settings,
  Bug,
  X,
} from 'lucide-vue-next'
import { RouterView, RouterLink, useRoute, useRouter } from 'vue-router'
import { fetchSessions, type SessionSummary } from './api/sessions'
import { fetchCards, type CardSummary } from './api/cards'

const route = useRoute()
const router = useRouter()
const collapsed = ref(false)
/** 窄屏断点：与状态浮层、分支树一致。 */
const narrow = ref(false)
const narrowQuery = window.matchMedia('(max-width: 768px)')
/** 抽屉是否拉出。只在窄屏有意义。 */
const drawerOpen = ref(false)
const sessions = ref<SessionSummary[]>([])
const cards = ref<CardSummary[]>([])

/** 侧栏折叠态：窄屏不折叠，抽屉里始终显示文字。 */
const sideCollapsed = computed(() => collapsed.value && !narrow.value)

/** 角色卡 id 到名字。会话列表显示角色名比显示 id 好认。 */
const cardNames = computed(() => {
  const map = new Map<string, string>()
  for (const card of cards.value) map.set(card.id, card.name)
  return map
})

/**
 * 侧栏里一段对话叫什么。
 *
 * 只显示角色名：一个人一条对话，认得出是谁就够。分支数在会话列表页
 * 单独一栏，不必挤在名字后面。
 */
function sessionLabel(session: SessionSummary): string {
  const roster = session.characters ?? []
  const name = roster.length > 0 ? cardNames.value.get(roster[0]) ?? roster[0] : ''
  return name || '未命名对话'
}

const NAV = [
  { to: '/chat', label: '对话', icon: MessagesSquare },
  { to: '/sessions', label: '会话列表', icon: History },
  { to: '/cards', label: '卡片库', icon: Library },
  { to: '/worldbooks', label: '世界书', icon: BookOpen },
  { to: '/memory', label: '记忆', icon: Brain },
  { to: '/plugins', label: '插件', icon: Puzzle },
  { to: '/models', label: '模型档位', icon: Cpu },
  { to: '/settings', label: '设置', icon: Settings },
  { to: '/debug', label: '调试', icon: Bug },
]

async function loadSessions(): Promise<void> {
  try {
    const res = await fetchSessions()
    sessions.value = res.items.slice(0, 8)
  } catch {
    sessions.value = []
  }
}

async function loadCards(): Promise<void> {
  try {
    const res = await fetchCards()
    cards.value = res.items
  } catch {
    cards.value = []
  }
}

/** 侧栏会话直接进对话页：每段对话独立，不经过管理页。 */
function openSession(id: string): void {
  drawerOpen.value = false
  void router.push({ path: '/chat', query: { session: id } })
}

/** 会话列表变了就重读侧栏：删掉一段分支后侧栏的分支数要跟着变。 */
function reloadSessions(): void {
  void loadSessions()
}

/** 断点切换：窄屏收起抽屉，宽屏恢复固定侧栏。 */
function syncNarrow(): void {
  narrow.value = narrowQuery.matches
  if (!narrow.value) drawerOpen.value = false
  syncViewport()
}

/**
 * 把应用高度压到可视视口。
 *
 * 窄屏上软键盘弹出时布局视口不变，dvh 也不变，输入区会被键盘盖住。
 * 这里读可视视口的实测高度写进 --app-h，壳就矮出键盘那截，输入区落在键盘上方。
 */
function syncViewport(): void {
  const root = document.documentElement
  const vv = window.visualViewport
  if (!narrow.value || !vv) {
    root.style.removeProperty('--app-h')
    root.style.removeProperty('--app-top')
    return
  }
  root.style.setProperty('--app-h', Math.round(vv.height) + 'px')
  root.style.setProperty('--app-top', Math.round(vv.offsetTop) + 'px')
}

// 换页面就收起抽屉：抽屉盖着主区，不该跟着换页还留在那儿。
watch(
  () => route.path,
  () => {
    drawerOpen.value = false
  },
)

onMounted(() => {
  void loadSessions()
  void loadCards()
  window.addEventListener('xworld:sessions-changed', reloadSessions)
  narrowQuery.addEventListener('change', syncNarrow)
  window.visualViewport?.addEventListener('resize', syncViewport)
  window.visualViewport?.addEventListener('scroll', syncViewport)
  syncNarrow()
})

onUnmounted(() => {
  window.removeEventListener('xworld:sessions-changed', reloadSessions)
  narrowQuery.removeEventListener('change', syncNarrow)
  window.visualViewport?.removeEventListener('resize', syncViewport)
  window.visualViewport?.removeEventListener('scroll', syncViewport)
})
</script>

<template>
  <div class="shell" :class="{ collapsed: sideCollapsed, 'drawer-open': drawerOpen }">
    <!-- 抽屉遮罩：点它收起侧栏 -->
    <div v-if="narrow && drawerOpen" class="scrim" @click="drawerOpen = false"></div>

    <aside class="sidebar">
      <div class="brand">
        <span class="logo">X-WORLD</span>
        <button
          class="collapse"
          :title="narrow ? '关闭菜单' : sideCollapsed ? '展开侧栏' : '折叠侧栏'"
          @click="narrow ? (drawerOpen = false) : (collapsed = !collapsed)"
        >
          <component
            :is="narrow ? X : sideCollapsed ? PanelLeftOpen : PanelLeftClose"
            :size="16"
          />
        </button>
      </div>

      <nav class="nav" aria-label="主导航">
        <RouterLink
          v-for="item in NAV"
          :key="item.to"
          :to="item.to"
          class="item"
          :class="{ active: route.path === item.to }"
          @click="drawerOpen = false"
        >
          <component :is="item.icon" :size="16" />
          <span v-if="!sideCollapsed" class="label">{{ item.label }}</span>
        </RouterLink>
      </nav>

      <div class="session-list" aria-label="会话列表">
        <p v-if="!sideCollapsed" class="section-title">最近会话</p>
        <button
          v-for="session in sessions"
          :key="session.id"
          class="session"
          :class="{ active: route.path === '/chat' && route.query.session === session.id }"
          :title="sessionLabel(session)"
          @click="openSession(session.id)"
        >
          <MessagesSquare :size="14" />
          <span v-if="!sideCollapsed" class="session-text">
            {{ sessionLabel(session) }}
          </span>
        </button>
        <p v-if="!sideCollapsed && sessions.length === 0" class="section-note">暂无会话。</p>
      </div>
    </aside>

    <!-- 顶栏：只在窄屏出现，里面是抽屉的开关 -->
    <header class="mobile-bar">
      <button class="menu-btn" title="菜单" aria-label="打开导航菜单" @click="drawerOpen = true">
        <Menu :size="18" />
      </button>
      <span class="bar-title">X-WORLD</span>
    </header>

    <main class="main">
      <RouterView />
    </main>
  </div>
</template>

<style scoped>
.shell {
  display: grid;
  grid-template-columns: var(--sidebar-w) 1fr;
  height: 100%;
  transition: grid-template-columns 0.15s ease;
}
.shell.collapsed {
  grid-template-columns: 56px 1fr;
}
.sidebar {
  display: flex;
  flex-direction: column;
  gap: 10px;
  background: var(--bg-1);
  border-right: 1px solid var(--border);
  padding: 10px 8px;
  overflow-y: auto;
}
.brand {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 2px 6px;
}
.logo {
  font-weight: 600;
  letter-spacing: 0.08em;
  font-size: 13px;
  color: var(--text-1);
}
.collapsed .logo {
  display: none;
}
.collapse {
  color: var(--text-3);
  display: inline-flex;
}
.collapse:hover {
  color: var(--text-1);
}
.nav {
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 7px 10px;
  border-radius: var(--radius);
  color: var(--text-2);
  text-decoration: none;
  font-size: 13px;
}
.item:hover {
  background: var(--bg-2);
  color: var(--text-1);
}
.item.active {
  background: var(--accent-soft);
  color: var(--accent);
}
/* 最近会话：紧跟导航往下排，中间用一条虚线分开 */
.session-list {
  display: flex;
  flex-direction: column;
  gap: 4px;
  margin-top: 4px;
  padding-top: 10px;
  border-top: 1px dashed var(--border);
}
.section-title {
  margin: 0 0 4px;
  padding: 0 6px;
  font-size: 11px;
  color: var(--text-3);
  text-transform: uppercase;
  letter-spacing: 0.05em;
}
.section-note {
  margin: 0;
  padding: 0 6px;
  font-size: 11px;
  color: var(--text-3);
}
.session {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 10px;
  border-radius: var(--radius);
  color: var(--text-2);
  font-size: 13px;
  text-align: left;
}
.session:hover {
  background: var(--bg-2);
}
.session.active {
  background: var(--accent-soft);
  color: var(--accent);
}
.session-text {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.main {
  min-width: 0;
  min-height: 0;
  padding: 12px;
  overflow: hidden;
}
/* 顶栏只在窄屏出现，宽屏侧栏自带品牌行，不需要顶栏 */
.mobile-bar {
  display: none;
}
.scrim {
  display: none;
}
/* 窄屏：侧栏变抽屉，顶栏加菜单键，主区占满整宽。 */
@media (max-width: 768px) {
  .shell,
  .shell.collapsed {
    grid-template-columns: 1fr;
    grid-template-rows: auto minmax(0, 1fr);
  }
  .mobile-bar {
    grid-column: 1;
    grid-row: 1;
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 6px 8px;
    border-bottom: 1px solid var(--border);
    background: var(--bg-1);
  }
  .menu-btn {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 40px;
    height: 40px;
    border-radius: var(--radius);
    color: var(--text-2);
  }
  .menu-btn:hover {
    background: var(--bg-2);
    color: var(--text-1);
  }
  .bar-title {
    font-size: 13px;
    font-weight: 600;
    letter-spacing: 0.08em;
    color: var(--text-1);
  }
  .main {
    grid-column: 1;
    grid-row: 2;
    padding: 8px;
  }
  /* 抽屉盖在主区上面，不占布局宽度 */
  .sidebar {
    position: fixed;
    top: 0;
    bottom: 0;
    left: 0;
    z-index: 70;
    width: min(var(--sidebar-w), 82vw);
    transform: translateX(-100%);
    transition: transform 0.18s ease;
    box-shadow: 0 0 30px #00000059;
  }
  .shell.drawer-open .sidebar {
    transform: none;
  }
  .scrim {
    display: block;
    position: fixed;
    inset: 0;
    z-index: 60;
    background: var(--bg-0);
    opacity: 0.62;
  }
}
</style>
