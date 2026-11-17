// 文件作用：管理聊天会话、消息状态和流式回答生命周期。
import { defineStore } from 'pinia'
import { ref } from 'vue'

import { getErrorMessage } from '@/api/http'
import { listSessions, type SessionItem } from '@/api/sessions'
import type { AnswerStyle } from '@/types/sse'

// localStorage 中保存当前会话标识的固定键名。
const SESSION_STORAGE_KEY = 'labor-rights-qa.session-id'
if (typeof localStorage !== 'undefined') localStorage.removeItem(SESSION_STORAGE_KEY)

/** 创建聊天会话状态仓库并提供会话、历史及流式状态操作。 */
export const useSessionStore = defineStore('session', () => {
  // 当前活动会话标识仅保留在内存，避免匿名旧标识或其他账号记录被自动带入。
  const sessionId = ref('')
  // 当前会话默认采用的回答表达风格。
  const answerStyle = ref<AnswerStyle>('plain')
  // 最近访问的会话列表，用于会话切换界面。
  const recentSessions = ref<SessionItem[]>([])
  // 标记当前是否正在接收助手回答流。
  const streaming = ref(false)
  // 标记历史消息或会话列表是否正在加载。
  const historyLoading = ref(false)
  // 会话列表加载失败时供界面展示的错误文本。
  const sessionsError = ref('')
  // 正在进行的会话刷新请求，用于合并并发触发的重复请求。
  let refreshPromise: Promise<boolean> | null = null
  let refreshRequestId = 0

/** 更新当前会话标识并同步持久化到浏览器本地存储。
   * @param value 新的会话标识。
   */
  function setSessionId(value: string) {
    sessionId.value = value
  }

/** 清空当前会话标识及其本地持久化值。 */
  function clearSession() {
    sessionId.value = ''
  }

  /** 切换或退出账号时清空内存中的会话历史和所有异步加载状态。 */
  function resetForIdentityChange() {
    refreshRequestId += 1
    refreshPromise = null
    sessionId.value = ''
    recentSessions.value = []
    sessionsError.value = ''
    historyLoading.value = false
    streaming.value = false
  }

/** 加载最近会话列表，并合并尚未完成的重复刷新请求。
   * @returns 请求成功时为 true，失败时为 false。
   */
  async function refreshRecentSessions(): Promise<boolean> {
    if (refreshPromise) return refreshPromise

    sessionsError.value = ''
    const requestId = refreshRequestId
    refreshPromise = listSessions()
      .then((sessions) => {
        // sessions 是后端返回的最新会话列表。
        if (requestId === refreshRequestId) recentSessions.value = sessions
        return true
      })
      .catch((error: unknown) => {
        // error 保存本次列表请求失败的原始异常。
        if (requestId === refreshRequestId) sessionsError.value = getErrorMessage(error)
        return false
      })
      .finally(() => {
        if (requestId === refreshRequestId) refreshPromise = null
      })

    return refreshPromise
  }

/** 更新助手回答流的进行状态。
   * @param value 是否仍在接收流式回答。
   */
  function setStreaming(value: boolean) {
    streaming.value = value
  }

/** 更新会话历史或列表的加载状态。
   * @param value 是否正在加载历史数据。
   */
  function setHistoryLoading(value: boolean) {
    historyLoading.value = value
  }

  return {
    sessionId,
    answerStyle,
    recentSessions,
    streaming,
    historyLoading,
    sessionsError,
    setSessionId,
    clearSession,
    resetForIdentityChange,
    refreshRecentSessions,
    setStreaming,
    setHistoryLoading,
  }
})
