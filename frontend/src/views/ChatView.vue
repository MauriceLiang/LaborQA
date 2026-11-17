<!-- 文件作用：组织聊天页面、会话状态、提问交互和回答展示。 -->
<script setup lang="ts">
import { nextTick, onBeforeUnmount, reactive, ref, watch } from 'vue'
import { ElButton } from 'element-plus'
import { ArrowRight } from '@element-plus/icons-vue'

import MessageList from '@/components/chat/MessageList.vue'
import QuestionInput from '@/components/chat/QuestionInput.vue'
import { getChatApiError, streamChat } from '@/api/chat'
import { createSession, getSessionMessages } from '@/api/sessions'
import { ApiError } from '@/api/http'
import { usePreferencesStore } from '@/stores/preferences'
import { useSessionStore } from '@/stores/session'
import type { UiMessage } from '@/types/chat'

// 当前会话仓库，提供会话标识、回答风格和历史会话状态。
const sessionStore = useSessionStore()
// 用户偏好仓库，用于读取本次回答选择的 AI 模式。
const preferencesStore = usePreferencesStore()
// 当前页面按显示顺序维护的用户消息和助手回答列表。
const messages = ref<UiMessage[]>([])
// 聊天输入框中的当前问题草稿。
const question = ref('')
// 当前聊天流程需要展示给用户的错误信息。
const chatError = ref('')
// 标记当前回答是否仍在通过 SSE 流式接收。
const streaming = ref(false)
// 标记会话历史是否正在从后端恢复。
const historyLoading = ref(false)
// 聊天消息滚动容器的 DOM 引用。
const conversation = ref<HTMLElement | null>(null)

// 当前聊天请求的取消控制器，用于停止生成或切换会话。
let activeController: AbortController | null = null
// 单调递增的请求序号，用于忽略过期请求产生的异步结果。
let activeRequestId = 0
// 待执行的动画帧标识，用于合并重复滚动操作。
let scrollFrame: number | null = null
// 会话历史恢复请求序号，用于丢弃过期的恢复结果。
let restoreRequestId = 0
// 新建会话后暂时跳过一次 watch 触发的历史恢复。
let skipSessionRestoreFor: string | null = null

// 输入框下方展示的劳动权益问题示例。
const suggestions = [
  '公司拖欠工资，我应该准备什么材料？',
  '没有签劳动合同，我可以怎么处理？',
  '试用期被辞退，工资应该怎么算？',
]

onBeforeUnmount(() => {
  activeController?.abort()
  sessionStore.setStreaming(false)
  sessionStore.setHistoryLoading(false)
  if (scrollFrame !== null) window.cancelAnimationFrame(scrollFrame)
})

watch(
  () => sessionStore.sessionId,
  (sessionId) => {
    // sessionId 是本次切换后需要显示或恢复的会话标识。
    if (sessionId && sessionId === skipSessionRestoreFor) {
      skipSessionRestoreFor = null
      return
    }
    // 为这次历史恢复分配序号，防止更晚的切换被旧响应覆盖。
    const requestId = ++restoreRequestId
    resetConversation()
    if (sessionId) void restoreSession(sessionId, requestId)
  },
  { immediate: true },
)

watch(
  messages,
  async () => {
    await nextTick()
    scheduleConversationScroll()
  },
  { deep: true },
)

/** 合并待执行的动画帧，并安排聊天区域滚动到底部。 */
function scheduleConversationScroll() {
  if (scrollFrame !== null) window.cancelAnimationFrame(scrollFrame)

  if (typeof window.requestAnimationFrame !== 'function') {
    scrollConversationToBottom()
    return
  }

  scrollFrame = window.requestAnimationFrame(() => {
    scrollFrame = null
    scrollConversationToBottom()
  })
}

/** 将聊天消息滚动容器定位到最新内容底部。 */
function scrollConversationToBottom() {
  if (conversation.value) conversation.value.scrollTop = conversation.value.scrollHeight
}

/** 输入区域折叠后重新调整会话滚动位置。 */
function handleInputCollapsed() {
  nextTick(() => scheduleConversationScroll())
}

/** 加载指定会话历史并构建前端消息状态。
 * @param sessionId 需要恢复的会话标识。
 * @param requestId 本次恢复请求序号，用于忽略过期结果。
 */
async function restoreSession(sessionId: string, requestId: number) {
  historyLoading.value = true
  sessionStore.setHistoryLoading(true)
  chatError.value = ''
  try {
    // history 是后端返回的会话历史消息数组。
    const history = await getSessionMessages(sessionId)
    if (requestId !== restoreRequestId || sessionStore.sessionId !== sessionId) return
    // message 表示一条后端消息，转换为前端统一的 UiMessage 结构。
    messages.value = history.map((message) => ({
      key: String(message.id),
      id: message.id,
      role: message.role,
      content: message.content,
      citations: message.citations,
      toolExecutions: message.tool_executions,
      answerStyle: message.answer_style,
      aiMode: message.ai_mode,
      refused: message.refused ?? false,
      progressText: null,
      status: 'complete',
    }))
  } catch (error) {
    // error 是读取会话历史时产生的异常。
    if (requestId !== restoreRequestId || sessionStore.sessionId !== sessionId) return
    messages.value = []
    if (error instanceof ApiError && error.code === 40402) sessionStore.clearSession()
    chatError.value = `无法恢复上次对话：${getChatApiError(error)}`
  } finally {
    if (requestId === restoreRequestId) {
      historyLoading.value = false
      sessionStore.setHistoryLoading(false)
    }
  }
}

/** 取消当前请求并清理会话消息、输入和加载状态。 */
function resetConversation() {
  activeRequestId += 1
  activeController?.abort()
  activeController = null
  streaming.value = false
  sessionStore.setStreaming(false)
  sessionStore.setHistoryLoading(false)
  messages.value = []
  question.value = ''
  chatError.value = ''
}

/** 更新问题输入内容并清除已经显示的旧错误。
 * @param value 输入框当前文本。
 */
function setQuestion(value: string) {
  question.value = value
  if (chatError.value) chatError.value = ''
}

/** 校验并发送用户问题，处理会话创建及完整流式回答生命周期。 */
async function sendQuestion() {
  // content 是去除首尾空白后实际提交的问题文本。
  const content = question.value.trim()
  if (!content || content.length > 2000 || streaming.value || historyLoading.value) return

  // 浏览器侧计时起点，用于诊断用户可见的请求延迟。
  const clientStarted = performance.now()
  // 记录是否已收到首个状态事件和首个回答 token。
  let firstStatusReceived = false
  // 本次回答流是否已收到首个文本 token，用于记录首 token 延迟。
  let firstTokenReceived = false
  // 新建会话请求的浏览器侧耗时。
  let sessionCreateMs = 0
  // 从提交问题到收到首个状态事件的耗时。
  let clientFirstStatusMs: number | null = null
  // 从提交问题到收到首个回答 token 的耗时。
  let clientTtftMs: number | null = null

  chatError.value = ''
  question.value = ''
  // 当前消息组的请求序号，用于防止旧流更新新的对话状态。
  const requestId = ++activeRequestId
  // userMessage 是立即显示在消息列表中的用户提问记录。
  const userMessage: UiMessage = {
    key: `user-${requestId}`,
    id: null,
    role: 'user',
    content,
    citations: [],
    toolExecutions: [],
    answerStyle: null,
    aiMode: preferencesStore.aiMode,
    refused: false,
    progressText: null,
    status: 'complete',
  }
  // assistantMessage 是随流式事件逐步更新的助手回答状态。
  const assistantMessage = reactive<UiMessage>({
    key: `assistant-${requestId}`,
    id: null,
    role: 'assistant',
    content: '',
    citations: [],
    toolExecutions: [],
    answerStyle: sessionStore.answerStyle,
    aiMode: preferencesStore.aiMode,
    refused: false,
    progressText: '正在准备回答…',
    status: 'streaming',
  })
  messages.value.push(userMessage, assistantMessage)
  streaming.value = true
  sessionStore.setStreaming(true)

  // controller 控制本次 SSE 请求的取消生命周期。
  const controller = new AbortController()
  activeController = controller

  try {
    // 本次请求使用的会话标识，首次提问时可能需要先创建。
    let sessionId = sessionStore.sessionId
    if (!sessionId) {
      // 新建会话前的计时点，用于计算会话初始化耗时。
      const sessionStarted = performance.now()
      // session 是后端新建并返回的会话记录。
      const session = await createSession(content.slice(0, 100))
      sessionCreateMs = Math.round(performance.now() - sessionStarted)
      if (requestId !== activeRequestId) return
      sessionId = session.id
      skipSessionRestoreFor = sessionId
      sessionStore.setSessionId(sessionId)
    }
    if (controller.signal.aborted) {
      removeEmptyAssistant(assistantMessage)
      return
    }

    await streamChat(
      {
        session_id: sessionId,
        question: content,
        answer_style: sessionStore.answerStyle,
        ai_mode: preferencesStore.aiMode,
      },
      {
        // message 是服务端状态事件中的当前阶段提示文本。
        onStatus: ({ message }) => {
          if (requestId !== activeRequestId) return
          if (!firstStatusReceived) {
            firstStatusReceived = true
            clientFirstStatusMs = Math.round(performance.now() - clientStarted)
          }
          assistantMessage.progressText = message
        },
        // execution 是服务端返回的单次工具执行记录。
        onTool: (execution) => {
          if (requestId === activeRequestId) assistantMessage.toolExecutions.push(execution)
        },
        // token 是本次流事件新增的回答文本片段。
        onToken: ({ content: token }) => {
          if (requestId !== activeRequestId) return
          if (!firstTokenReceived) {
            firstTokenReceived = true
            clientTtftMs = Math.round(performance.now() - clientStarted)
          }
          assistantMessage.progressText = null
          assistantMessage.content += token
        },
        // items 是服务端本次回答引用的来源列表。
        onSources: ({ items }) => {
          if (requestId === activeRequestId) assistantMessage.citations = items
        },
        // 结束事件中的字段分别是消息主键、回答风格、拒答标记和服务端耗时。
        onDone: ({ message_id, answer_style, refused, timings }) => {
          if (requestId !== activeRequestId) return
          assistantMessage.id = message_id
          assistantMessage.answerStyle = answer_style
          assistantMessage.refused = refused
          assistantMessage.progressText = null
          assistantMessage.status = 'complete'
          if (import.meta.env.DEV) {
            console.info('[chat-performance]', {
              sessionCreateMs,
              clientFirstStatusMs,
              clientTtftMs,
              serverTimings: timings ?? null,
            })
          }
        },
        // message 是服务端错误事件中的用户提示文本。
        onError: ({ message }) => {
          if (requestId !== activeRequestId) return
          assistantMessage.status = 'error'
          assistantMessage.progressText = null
          chatError.value = message
        },
      },
      controller.signal,
    )

    if (requestId === activeRequestId && controller.signal.aborted) {
      removeEmptyAssistant(assistantMessage)
      return
    }
    if (requestId === activeRequestId && assistantMessage.status === 'streaming') {
      assistantMessage.status = 'error'
      assistantMessage.progressText = null
      chatError.value = '回答流未正常结束，请重试。'
    }
  } catch (error) {
    // error 是会话创建或流式请求失败时捕获的异常。
    if (requestId !== activeRequestId) return
    assistantMessage.progressText = null
    if (controller.signal.aborted || (error instanceof Error && error.name === 'AbortError')) {
      if (assistantMessage.content) assistantMessage.status = 'stopped'
      else removeEmptyAssistant(assistantMessage)
    } else {
      assistantMessage.status = 'error'
      chatError.value = getChatApiError(error)
    }
  } finally {
    if (requestId === activeRequestId) {
      assistantMessage.progressText = null
      streaming.value = false
      sessionStore.setStreaming(false)
      activeController = null
      void sessionStore.refreshRecentSessions()
    }
  }
}

/** 中止当前聊天请求，结束助手回答生成。 */
function stopGeneration() {
  activeController?.abort()
}

/** 移除没有生成正文的助手消息，或将已有正文标记为已停止。
 * @param message 当前流式助手消息。
 */
function removeEmptyAssistant(message: UiMessage) {
  if (!message.content) {
    // item 表示待保留的其他消息；仅过滤掉当前空助手消息。
    messages.value = messages.value.filter((item) => item.key !== message.key)
  } else {
    message.status = 'stopped'
  }
}

/** 将示例问题填入聊天输入框。
 * @param value 被选中的示例问题文本。
 */
function useSuggestion(value: string) {
  question.value = value
}
</script>

<template>
  <section class="chat-page">
    <div class="chat-workspace">
      <p v-if="chatError" class="chat-error" role="alert">{{ chatError }}</p>

      <div ref="conversation" class="chat-conversation">
        <MessageList :messages="messages" :history-loading="historyLoading">
          <template #suggestions>
            <ElButton
              v-for="suggestion in suggestions"
              :key="suggestion"
              class="suggestion-button"
              @click="useSuggestion(suggestion)"
            >
              {{ suggestion }}
              <ArrowRight aria-hidden="true" />
            </ElButton>
          </template>
        </MessageList>
      </div>

      <QuestionInput
        :model-value="question"
        :disabled="historyLoading"
        :streaming="streaming"
        :answer-style="sessionStore.answerStyle"
        @update:model-value="setQuestion"
        @update:answer-style="sessionStore.answerStyle = $event"
        @update:collapsed="handleInputCollapsed"
        @submit="sendQuestion"
        @stop="stopGeneration"
      />
    </div>
  </section>
</template>
