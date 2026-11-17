// 文件作用：管理服务端身份初始化、角色、CSRF token 和登录生命周期。
import { defineStore } from 'pinia'
import { ref } from 'vue'

import {
  getCurrentUser,
  login as loginRequest,
  logout as logoutRequest,
  type AuthenticatedUser,
  type UserIdentity,
  type UserRole,
} from '@/api/auth'
import { ApiError, setCsrfToken } from '@/api/http'
import { useSessionStore } from '@/stores/session'

const AUTH_REQUIRED_CODE = 40102

/** 保存唯一由服务端确认的身份；Cookie 本身始终不暴露给 JavaScript。 */
export const useAuthStore = defineStore('auth', () => {
  const user = ref<UserIdentity | null>(null)
  const initialized = ref(false)
  let initializationPromise: Promise<boolean> | null = null

  /** 启动时通过 /auth/me 验证 HttpOnly Cookie，不依赖本地角色缓存。 */
  async function initialize(): Promise<boolean> {
    if (initialized.value) return user.value !== null
    if (initializationPromise) return initializationPromise

    initializationPromise = getCurrentUser()
      .then((result) => {
        applyAuthenticatedUser(result)
        initialized.value = true
        return true
      })
      .catch((error: unknown) => {
        if (error instanceof ApiError && error.code === AUTH_REQUIRED_CODE) {
          clearAuthentication()
          return false
        }
        throw error
      })
      .finally(() => {
        initializationPromise = null
      })
    return initializationPromise
  }

  /** 使用登录接口返回的服务端身份和 CSRF token 建立前端状态。 */
  async function signIn(identifier: string, password: string) {
    const result = await loginRequest(identifier, password)
    applyAuthenticatedUser(result)
    initialized.value = true
  }

  /** 先请求撤销服务端会话，再无条件清空本地身份与聊天状态。 */
  async function signOut() {
    try {
      await logoutRequest()
    } finally {
      clearAuthentication()
    }
  }

  /** 清除账号身份、内存 CSRF token 和上一身份的会话状态。 */
  function clearAuthentication() {
    user.value = null
    setCsrfToken('')
    useSessionStore().resetForIdentityChange()
    initialized.value = true
  }

  /** 统一应用登录或刷新身份响应，并在账号变化时清空旧会话列表。 */
  function applyAuthenticatedUser(result: AuthenticatedUser) {
    if (user.value?.id !== result.user.id) {
      useSessionStore().resetForIdentityChange()
    }
    user.value = result.user
    setCsrfToken(result.csrf_token)
  }

  function hasAnyRole(roles: UserRole[]) {
    return user.value !== null && roles.includes(user.value.role)
  }

  return {
    user,
    initialized,
    initialize,
    signIn,
    signOut,
    clearAuthentication,
    hasAnyRole,
  }
})
