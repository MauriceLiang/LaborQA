// 文件作用：封装聊天请求、流式响应事件解析和聊天数据类型适配。
import type { ChatRequest, ChatStreamHandlers } from '@/types/sse'
import { ApiError, getCsrfToken, getErrorMessage, http } from '@/api/http'

/** 提交聊天请求并持续读取、分发服务端发送事件。
 * @param payload 聊天问题、会话及回答模式。
 * @param handlers 各类流事件对应的处理回调。
 * @param signal 可选的取消信号，用于主动中止请求。
 */
export async function streamChat(
  payload: ChatRequest,
  handlers: ChatStreamHandlers,
  signal?: AbortSignal,
): Promise<void> {
  // baseUrl 是移除末尾斜杠后的 API 根地址。
  const baseUrl = String(http.defaults.baseURL).replace(/\/+$/, '')
  // response 是原生 Fetch 返回的 HTTP 响应及其流式 body。
  const response = await fetch(`${baseUrl}/chat/stream`, {
    method: 'POST',
    headers: {
      Accept: 'text/event-stream',
      'Content-Type': 'application/json',
      'X-CSRF-Token': getCsrfToken(),
    },
    credentials: 'include',
    body: JSON.stringify(payload),
    signal,
  })

  if (!response.ok) {
    if (response.status === 401 && typeof window !== 'undefined') {
      window.dispatchEvent(new Event('laborqa:auth-expired'))
    }
    // body 暂存非成功响应的 JSON 内容；读取失败时置为 null。
    let body: unknown
    try {
      body = await response.json()
    } catch {
      body = null
    }
    // error 将响应体按可能包含业务错误码和提示文本的结构读取。
    const error = body as { code?: unknown; message?: unknown } | null
    throw new ApiError(
      typeof error?.code === 'number' ? error.code : response.status,
      typeof error?.message === 'string' ? error.message : '聊天请求失败',
    )
  }
  if (!response.body) throw new Error('后端未返回聊天数据流')

  // reader 用于逐块读取服务端发送事件流。
  const reader = response.body.getReader()
  // decoder 将网络字节块增量解码为 UTF-8 文本。
  const decoder = new TextDecoder()
  // buffer 暂存尚未组成完整 SSE 帧的文本。
  let buffer = ''
  // 标记是否已收到 done 或 error 等终止事件。
  let terminalEventReceived = false

  try {
    while (!terminalEventReceived) {
      // value 是当前字节块，done 表示流已到达末尾。
      const { value, done } = await reader.read()
      buffer += done ? decoder.decode() : decoder.decode(value, { stream: true })

      // boundary 指向缓冲区中首个 SSE 帧分隔符。
      let boundary = /\r?\n\r?\n/.exec(buffer)
      while (boundary) {
        // frame 是分隔符之前的一条完整 SSE 事件文本。
        const frame = buffer.slice(0, boundary.index)
        buffer = buffer.slice(boundary.index + boundary[0].length)
        terminalEventReceived = dispatchFrame(frame, handlers)
        if (terminalEventReceived) break
        boundary = /\r?\n\r?\n/.exec(buffer)
      }

      if (done) break
    }
  } finally {
    if (terminalEventReceived) await reader.cancel()
    reader.releaseLock()
  }

  if (!signal?.aborted && !terminalEventReceived) {
    throw new Error('聊天数据流意外结束')
  }
}

/** 解析单条 SSE 帧并调用对应回调。
 * @param frame 一条完整的服务端发送事件文本。
 * @param handlers 事件类型到处理回调的映射。
 * @returns 收到终止事件时返回 true。
 */
function dispatchFrame(frame: string, handlers: ChatStreamHandlers): boolean {
  // lines 是按 SSE 行分隔符拆开的事件内容。
  const lines = frame.split(/\r?\n/)
  // event 保存 SSE 帧声明的事件名称。
  const event = lines.find((line) => line.startsWith('event:'))?.slice(6).trim()
  // data 汇总帧内一行或多行 data 字段的 JSON 文本。
  const data = lines
    .filter((line) => line.startsWith('data:'))
    .map((line) => line.slice(5).trimStart())
    .join('\n')

  if (!event || !data) return false
  // payload 是解析后的事件内容，随后按事件名转换为对应类型。
  const payload: unknown = JSON.parse(data)

  switch (event) {
    case 'status':
      handlers.onStatus?.(
        payload as Parameters<NonNullable<ChatStreamHandlers['onStatus']>>[0],
      )
      return false
    case 'tool':
      handlers.onTool?.(payload as Parameters<NonNullable<ChatStreamHandlers['onTool']>>[0])
      return false
    case 'token':
      handlers.onToken(payload as Parameters<ChatStreamHandlers['onToken']>[0])
      return false
    case 'sources':
      handlers.onSources(payload as Parameters<ChatStreamHandlers['onSources']>[0])
      return false
    case 'done':
      handlers.onDone(payload as Parameters<ChatStreamHandlers['onDone']>[0])
      return true
    case 'error':
      handlers.onError(payload as Parameters<ChatStreamHandlers['onError']>[0])
      return true
    default:
      return false
  }
}

/** 将聊天请求异常转换为适合用户查看的错误提示。
 * @param error 捕获到的聊天异常。
 * @returns 后端连接或 API 错误提示。
 */
export function getChatApiError(error: unknown): string {
  if (error instanceof TypeError) return '无法连接后端，请检查服务是否已启动'
  return getErrorMessage(error)
}
