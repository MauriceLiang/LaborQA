import { createPinia } from 'pinia'
import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createRouter, createMemoryHistory } from 'vue-router'

import * as sessionsApi from '@/api/sessions'
import type { UserRole } from '@/api/auth'
import { useAuthStore } from '@/stores/auth'
import { useSessionStore } from '@/stores/session'
import App from '@/App.vue'

vi.mock('@/api/sessions', () => ({
  createSession: vi.fn(),
  listSessions: vi.fn(),
  getSessionMessages: vi.fn(),
}))

const session = {
  id: '7ad8c9e1-62b2-4d40-9c6f-c48657f901d0',
  title: '公司拖欠工资怎么处理',
  created_at: '2026-09-15T00:00:00Z',
  updated_at: '2026-09-15T00:00:00Z',
}

const secondSession = {
  ...session,
  id: '88f4d026-b4d1-4128-8213-87663049e775',
  title: '未签劳动合同怎么办',
}

const page = { template: '<div />' }

function createTestRouter() {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', name: 'chat', component: page },
      { path: '/system', name: 'system', component: page },
      { path: '/documents', name: 'documents', component: page },
      { path: '/missing-knowledge', name: 'missing-knowledge', component: page },
      { path: '/evaluations', name: 'evaluations', component: page },
      { path: '/retrieval-experiments', name: 'retrieval-experiments', component: page },
    ],
  })
}

async function mountApp(path = '/system', sessionId = '', role: UserRole = 'SYSTEM_ADMIN') {
  const router = createTestRouter()
  await router.push(path)
  await router.isReady()
  const pinia = createPinia()
  useAuthStore(pinia).user = {
    id: 'acd6c5b7-f167-4711-bfe0-871a8cf052bf',
    login_identifier: 'test-admin',
    role,
    status: 'ACTIVE',
    created_at: '2026-09-15T00:00:00Z',
    updated_at: '2026-09-15T00:00:00Z',
  }
  const sessionStore = useSessionStore(pinia)
  if (sessionId) sessionStore.setSessionId(sessionId)
  const wrapper = mount(App, { global: { plugins: [pinia, router] } })
  await flushPromises()
  return { wrapper, router, sessionStore }
}

beforeEach(() => {
  vi.clearAllMocks()
  localStorage.clear()
  vi.mocked(sessionsApi.listSessions).mockResolvedValue([session, secondSession])
})

describe('App session navigation', () => {
  it('uses the official Chinese product name', async () => {
    const { wrapper } = await mountApp()

    expect(wrapper.get('.brand-lockup').text()).toContain('劳动权益咨询问答台')
    expect(wrapper.get('.brand-lockup').text()).not.toContain('LaborQA')
    wrapper.unmount()
  })

  it('shows navigation entries allowed for each account role', async () => {
    const ordinary = await mountApp('/', '', 'USER')
    const ordinaryNavigation = ordinary.wrapper.findAll('.app-nav .nav-item').map((item) => item.text())
    expect(ordinaryNavigation).toEqual(['AI 咨询', '资料管理'])
    ordinary.wrapper.unmount()

    const knowledgeAdmin = await mountApp('/', '', 'KNOWLEDGE_ADMIN')
    const knowledgeNavigation = knowledgeAdmin.wrapper.findAll('.app-nav .nav-item').map((item) => item.text())
    expect(knowledgeNavigation).toContain('知识缺口')
    expect(knowledgeNavigation).not.toContain('问答评测')
    expect(knowledgeNavigation).not.toContain('账号管理')
    knowledgeAdmin.wrapper.unmount()
  })

  it('loads recent sessions and selects the clicked conversation', async () => {
    const { wrapper, router, sessionStore } = await mountApp()

    const recentItems = wrapper.findAll('.recent-conversation')
    expect(recentItems).toHaveLength(2)
    expect(recentItems[1].text()).toContain(secondSession.title)

    await recentItems[1].trigger('click')
    await flushPromises()

    expect(sessionStore.sessionId).toBe(secondSession.id)
    expect(router.currentRoute.value.name).toBe('chat')
    wrapper.unmount()
  })

  it('does not clear the active session until recent sessions are refreshed', async () => {
    const { wrapper, sessionStore } = await mountApp('/system', session.id)
    let release!: (value: typeof session[]) => void
    vi.mocked(sessionsApi.listSessions).mockImplementationOnce(
      () => new Promise((resolve) => { release = resolve }),
    )

    await wrapper.get('.recent-add').trigger('click')
    await Promise.resolve()
    expect(sessionStore.sessionId).toBe(session.id)

    release([session])
    await flushPromises()
    expect(sessionStore.sessionId).toBe('')
    wrapper.unmount()
  })

  it('keeps the active session and reports a refresh failure', async () => {
    const { wrapper, sessionStore } = await mountApp('/system', session.id)
    vi.mocked(sessionsApi.listSessions).mockRejectedValueOnce(new Error('保存失败'))

    await wrapper.get('.recent-add').trigger('click')
    await flushPromises()

    expect(sessionStore.sessionId).toBe(session.id)
    expect(wrapper.get('[role="alert"]').text()).toContain('保存失败')
    wrapper.unmount()
  })
})
