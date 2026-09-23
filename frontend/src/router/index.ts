// 文件作用：定义前端页面路由及其对应的视图组件。
import { createRouter, createWebHistory } from 'vue-router'

import HomeView from '@/views/HomeView.vue'
import DocumentsView from '@/views/DocumentsView.vue'
import ChatView from '@/views/ChatView.vue'
import MissingKnowledgeView from '@/views/MissingKnowledgeView.vue'
import EvaluationView from '@/views/EvaluationView.vue'
import RetrievalExperimentView from '@/views/RetrievalExperimentView.vue'

// 根据浏览器地址和页面配置创建的前端路由实例。
const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: '/',
      name: 'chat',
      component: ChatView,
    },
    {
      path: '/system',
      name: 'system',
      component: HomeView,
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
    },
    {
      path: '/evaluations',
      name: 'evaluations',
      component: EvaluationView,
    },
    {
      path: '/retrieval-experiments',
      name: 'retrieval-experiments',
      component: RetrievalExperimentView,
    },
  ],
})

export default router
