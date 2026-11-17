<!-- 文件作用：应用顶层页面结构，承载路由视图并提供全局布局。 -->
<script setup lang="ts">
import { ElButton, ElMessage } from 'element-plus'
import { computed, watch, type Component } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  ChatLineRound,
  CirclePlus,
  Document,
  Histogram,
  Monitor,
  Reading,
  Search,
  User,
} from '@element-plus/icons-vue'

import type { SessionItem } from '@/api/sessions'
import type { UserRole } from '@/api/auth'
import AiModeSelector from '@/components/AiModeSelector.vue'
import { useAuthStore } from '@/stores/auth'
import { useSessionStore } from '@/stores/session'

// Vue Router 当前路由，用于确定导航高亮和页面状态。
const route = useRoute()
// Vue Router 导航实例，用于切换页面。
const router = useRouter()
const authStore = useAuthStore()
// 聊天会话仓库，提供历史记录、流式状态和会话操作。
const sessionStore = useSessionStore()

// 侧栏导航项及其名称、路径和图标配置。
const navItems: Array<{
  name: string
  label: string
  to: string
  icon: Component
  roles?: UserRole[]
}> = [
  { name: 'chat', label: 'AI 咨询', to: '/', icon: ChatLineRound },
  { name: 'system', label: '系统状态', to: '/system', icon: Monitor, roles: ['SYSTEM_ADMIN'] },
  { name: 'documents', label: '资料管理', to: '/documents', icon: Document },
  { name: 'missing-knowledge', label: '知识缺口', to: '/missing-knowledge', icon: Reading, roles: ['KNOWLEDGE_ADMIN', 'SYSTEM_ADMIN'] },
  { name: 'evaluations', label: '问答评测', to: '/evaluations', icon: Histogram, roles: ['SYSTEM_ADMIN'] },
  { name: 'retrieval-experiments', label: '检索实验', to: '/retrieval-experiments', icon: Search, roles: ['SYSTEM_ADMIN'] },
  { name: 'users', label: '账号管理', to: '/users', icon: User, roles: ['SYSTEM_ADMIN'] },
]

// 当前页面是否为聊天咨询页。
const isChatRoute = computed(() => route.name === 'chat')
const visibleNavItems = computed(() => navItems.filter((item) => {
  if (!item.roles) return true
  return authStore.hasAnyRole(item.roles)
}))
const roleLabel = computed(() => ({
  USER: '普通用户',
  KNOWLEDGE_ADMIN: '知识管理员',
  SYSTEM_ADMIN: '系统管理员',
}[authStore.user?.role || 'USER']))
// 根据会话加载和生成状态计算的聊天区状态文案。
const chatStatusText = computed(() => {
  if (sessionStore.historyLoading) return '正在恢复会话记录…'
  if (sessionStore.streaming) return '正在生成回答…'
  return sessionStore.sessionId ? '当前会话' : '新会话'
})

watch(
  () => authStore.user?.id,
  (userId) => {
    if (userId) void sessionStore.refreshRecentSessions()
    else sessionStore.resetForIdentityChange()
  },
  { immediate: true },
)

/** 打开最近会话列表中的指定聊天会话。 */
// 参数 conversation：从最近会话列表中选择的会话记录。
function openRecentConversation(conversation: SessionItem) {
  if (sessionStore.streaming) return
  sessionStore.setSessionId(conversation.id)
  if (!isChatRoute.value) void router.push('/')
}

/** 刷新历史会话后清空当前会话并开始新对话。 */
async function startNewConversation() {
  if (sessionStore.streaming) return
  // 刷新最近会话记录是否成功。
  const saved = await sessionStore.refreshRecentSessions()
  if (!saved) return

  sessionStore.clearSession()
  if (!isChatRoute.value) void router.push('/')
}

/** 撤销当前设备登录状态并返回登录页。 */
async function handleLogout() {
  try {
    await authStore.signOut()
  } catch (error: unknown) {
    ElMessage.error(error instanceof Error ? error.message : '退出登录失败')
  } finally {
    await router.replace({ name: 'login' })
  }
}
</script>

<template>
  <RouterView v-if="route.name === 'login'" />
  <div v-else class="app-shell">
    <aside class="app-sidebar" aria-label="应用导航">
      <div class="brand-lockup">
        <div class="brand-symbol" aria-hidden="true">
          <img src="/assets/brand-mark.png" alt="" />
        </div>
        <div>
          <strong>劳动权益咨询问答台</strong>
          <span>劳动权益信息服务</span>
        </div>
      </div>

      <nav class="app-nav" aria-label="主导航">
        <RouterLink
          v-for="item in visibleNavItems"
          :key="item.name"
          :to="item.to"
          class="nav-item"
          :class="{ 'nav-item-active': route.name === item.name }"
        >
          <component :is="item.icon" class="nav-item-icon" aria-hidden="true" />
          <span>{{ item.label }}</span>
        </RouterLink>
      </nav>

      <section class="recent-conversations" aria-labelledby="recent-conversations-title">
        <div class="recent-heading">
          <h2 id="recent-conversations-title">最近对话</h2>
          <ElButton
            class="recent-add"
            text
            aria-label="新建对话"
            :disabled="sessionStore.streaming"
            @click="startNewConversation"
          >
            <CirclePlus aria-hidden="true" />
          </ElButton>
        </div>
        <p v-if="sessionStore.sessionsError" class="recent-error" role="alert">
          {{ sessionStore.sessionsError }}
        </p>
        <div class="recent-conversation-list">
          <ElButton
            v-for="conversation in sessionStore.recentSessions"
            :key="conversation.id"
            class="recent-conversation"
            text
            :class="{ 'recent-conversation-active': isChatRoute && conversation.id === sessionStore.sessionId }"
            :disabled="sessionStore.streaming"
            @click="openRecentConversation(conversation)"
          >
            <span>{{ conversation.title || '未命名对话' }}</span>
          </ElButton>
        </div>
      </section>

      <p class="legal-note">信息辅助，不替代正式法律意见</p>
    </aside>

    <section class="app-content">
      <header class="app-topbar">
        <div class="app-topbar-left">
          <ElButton
            class="topbar-new-chat"
            text
            aria-label="新建对话"
            :disabled="sessionStore.streaming"
            @click="startNewConversation"
          >
            <CirclePlus aria-hidden="true" />
            <span class="topbar-new-chat-label">新建对话</span>
          </ElButton>
          <span class="knowledge-pill">
            <Document aria-hidden="true" />
            <span>基于已导入资料回答</span>
          </span>
        </div>
        <div class="app-topbar-right" :class="{ 'app-topbar-right-non-chat': !isChatRoute }">
          <AiModeSelector />
          <span v-if="isChatRoute" class="session-status">{{ chatStatusText }}</span>
          <div v-if="authStore.user" class="account-identity">
            <span class="account-identity__name">{{ authStore.user.login_identifier }}</span>
            <span class="account-identity__role">{{ roleLabel }}</span>
            <ElButton text @click="handleLogout">退出</ElButton>
          </div>
        </div>
      </header>
      <main class="app-main" :class="{ 'app-main-chat': isChatRoute }">
        <router-view />
      </main>
    </section>
  </div>
</template>
