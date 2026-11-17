// 文件作用：封装登录、当前身份、登出和账号管理接口的数据形状。
import { http } from '@/api/http'

export type UserRole = 'USER' | 'KNOWLEDGE_ADMIN' | 'SYSTEM_ADMIN'
export type UserStatus = 'ACTIVE' | 'DISABLED'

export interface UserIdentity {
  id: string
  login_identifier: string
  role: UserRole
  status: UserStatus
  created_at: string
  updated_at: string
}

export interface AuthenticatedUser {
  user: UserIdentity
  csrf_token: string
}

export interface ManagedUser extends UserIdentity {}

/** 使用管理员创建的账号登录，并接收 HttpOnly Cookie 与内存 CSRF token。 */
export async function login(
  loginIdentifier: string,
  password: string,
): Promise<AuthenticatedUser> {
  const response = await http.post<AuthenticatedUser>('/auth/login', {
    login_identifier: loginIdentifier,
    password,
  })
  return response.data
}

/** 恢复服务端身份，刷新页面时同时轮换 CSRF token。 */
export async function getCurrentUser(): Promise<AuthenticatedUser> {
  const response = await http.get<AuthenticatedUser>('/auth/me')
  return response.data
}

/** 撤销当前设备的服务端会话。 */
export async function logout(): Promise<void> {
  await http.post('/auth/logout')
}

/** 列出所有可管理普通账号与知识管理员的身份状态。 */
export async function listManagedUsers(): Promise<ManagedUser[]> {
  const response = await http.get<ManagedUser[]>('/admin/users')
  return response.data
}

/** 由系统管理员新建普通账号或知识管理员。 */
export async function createManagedUser(payload: {
  login_identifier: string
  password: string
  role: Exclude<UserRole, 'SYSTEM_ADMIN'>
}): Promise<ManagedUser> {
  const response = await http.post<ManagedUser>('/admin/users', payload)
  return response.data
}

/** 更新账号角色或停用状态；后端会同步撤销已有登录会话。 */
export async function updateManagedUser(
  id: string,
  payload: { role: Exclude<UserRole, 'SYSTEM_ADMIN'>; status: UserStatus },
): Promise<ManagedUser> {
  const response = await http.patch<ManagedUser>(`/admin/users/${id}`, payload)
  return response.data
}

/** 撤销指定账号的全部设备会话。 */
export async function revokeManagedUserSessions(id: string): Promise<void> {
  await http.post(`/admin/users/${id}/revoke-sessions`)
}
