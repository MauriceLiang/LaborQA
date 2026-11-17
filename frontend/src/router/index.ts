// 文件作用：定义前端页面路由及其对应的视图组件。
import { createRouter, createWebHistory } from 'vue-router'

import HomeView from '@/views/HomeView.vue'
import DocumentsView from '@/views/DocumentsView.vue'
import ChatView from '@/views/ChatView.vue'
import MissingKnowledgeView from '@/views/MissingKnowledgeView.vue'
import EvaluationView from '@/views/EvaluationView.vue'
import RetrievalExperimentView from '@/views/RetrievalExperimentView.vue'
import LoginView from '@/views/LoginView.vue'
import UsersView from '@/views/UsersView.vue'
import { useAuthStore } from '@/stores/auth'
import type { UserRole } from '@/api/auth'

// 根据浏览器地址和页面配置创建的前端路由实例。
const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: '/login',
      name: 'login',
      component: LoginView,
      meta: { public: true },
    },
    {
      path: '/',
      name: 'chat',
      component: ChatView,
    },
    {
      path: '/system',
      name: 'system',
      component: HomeView,
      meta: { roles: ['SYSTEM_ADMIN'] satisfies UserRole[] },
    },
    {
      path: '/documents',
      name: 'documents',
      component: DocumentsView,
    },
    {
      path: '/missing-knowledge',
      name: 'missing-knowledge',
      component: MissingKnowledgeView,
      meta: { roles: ['KNOWLEDGE_ADMIN', 'SYSTEM_ADMIN'] satisfies UserRole[] },
    },
    {
      path: '/evaluations',
      name: 'evaluations',
      component: EvaluationView,
      meta: { roles: ['SYSTEM_ADMIN'] satisfies UserRole[] },
    },
    {
      path: '/retrieval-experiments',
      name: 'retrieval-experiments',
      component: RetrievalExperimentView,
      meta: { roles: ['SYSTEM_ADMIN'] satisfies UserRole[] },
    },
    {
      path: '/users',
      name: 'users',
      component: UsersView,
      meta: { roles: ['SYSTEM_ADMIN'] satisfies UserRole[] },
    },
  ],
})

router.beforeEach(async (to) => {
  const authStore = useAuthStore()
  let authenticated = false
  try {
    authenticated = await authStore.initialize()
  } catch {
    if (to.meta.public) return true
    return { name: 'login', query: { redirect: to.fullPath } }
  }

  if (to.meta.public) {
    return authenticated ? { name: 'chat' } : true
  }
  if (!authenticated) {
    return { name: 'login', query: { redirect: to.fullPath } }
  }
  if (to.meta.roles && !authStore.hasAnyRole(to.meta.roles)) {
    return { name: 'chat' }
  }
  return true
})

export default router
