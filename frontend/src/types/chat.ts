// 文件作用：声明聊天请求、消息、引用、状态和流式事件的数据类型。
import type { AnswerStyle, CitationItem, ToolExecutionItem } from '@/types/sse'
import type { AiMode } from '@/types/preferences'

export interface UiMessage {
  /** 用于 Vue 列表稳定渲染的前端唯一键。 */
  key: string
  /** 后端消息主键；尚未持久化的消息为空。 */
  id: number | null
  /** 消息发送方角色。 */
  role: 'user' | 'assistant'
  /** 消息正文或当前流式生成的累积文本。 */
  content: string
  /** 助手回答引用的知识库文本块。 */
  citations: CitationItem[]
  /** 与该回答关联的工具执行记录。 */
  toolExecutions: ToolExecutionItem[]
  /** 生成回答采用的表达风格；用户消息没有该值。 */
  answerStyle: AnswerStyle | null
  /** 生成回答采用的 AI 模式；用户消息没有该值。 */
  aiMode: AiMode | null
  /** 标记回答是否因合规规则而拒答。 */
  refused: boolean
  /** 展示在回答下方的进度或状态提示文本。 */
  progressText: string | null
  /** 前端消息当前处于完成、流式、停止或错误状态。 */
  status: 'complete' | 'streaming' | 'stopped' | 'error'
}
