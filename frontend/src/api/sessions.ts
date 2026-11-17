// 文件作用：封装聊天会话及历史消息相关 API。
import type { components } from '@/types/api.generated'
import { http } from '@/api/http'

export type SessionItem = components['schemas']['SessionItem']
export type MessageItem = components['schemas']['MessageItem']

/** 创建一个聊天会话。
 * @param title 可选会话标题。
 * @returns 后端创建的会话数据。
 */
export async function createSession(title?: string): Promise<SessionItem> {
  // response 保存 HTTP 客户端返回的会话响应。
  const response = await http.post<SessionItem>('/sessions', title ? { title } : {})
  return response.data
}

/** 获取最近会话列表。
 * @param limit 最多返回的会话条数，默认 20 条。
 * @returns 会话数据数组。
 */
export async function listSessions(limit = 20): Promise<SessionItem[]> {
  // response 保存 HTTP 客户端返回的会话列表响应。
  const response = await http.get<SessionItem[]>('/sessions', { params: { limit } })
  return response.data
}

/** 查询指定会话的消息历史。
 * @param sessionId 会话标识。
 * @returns 按后端顺序返回的消息数据数组。
 */
export async function getSessionMessages(sessionId: string): Promise<MessageItem[]> {
  // response 保存 HTTP 客户端返回的消息列表响应。
  const response = await http.get<MessageItem[]>(`/sessions/${sessionId}/messages`)
  return response.data
}
