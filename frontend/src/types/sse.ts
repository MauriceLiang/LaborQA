// 文件作用：声明服务器推送事件的解析结果和回调类型。
import type { components } from './api.generated'
import type { AiMode } from './preferences'

export type AnswerStyle = 'plain' | 'legal'
export type CitationItem = components['schemas']['CitationItem']
export type ToolExecutionItem = components['schemas']['ToolExecutionItem']
export type ChatStage = 'understanding' | 'retrieving' | 'verifying' | 'generating'

export interface ChatStatusData {
  /** 当前聊天流程所处的处理阶段。 */
  stage: ChatStage
  /** 面向用户展示的阶段说明。 */
  message: string
}

export interface ChatTimingData {
  /** 读取会话及初始化上下文所耗毫秒数。 */
  session_ms: number
  /** 查询改写阶段耗时。 */
  rewrite_ms: number
  /** 检索与重排阶段耗时。 */
  retrieval_ms: number
  /** 证据判断阶段耗时。 */
  evidence_judge_ms: number
  /** 回答生成前准备阶段的累计耗时。 */
  pre_answer_ms: number
  /** 首个回答 token 到达所需毫秒数；不可用时为空。 */
  ttft_ms: number | null
  /** 最终答案生成耗时。 */
  answer_generation_ms: number
  /** 将回答和引用写入数据库的耗时。 */
  persist_ms: number
  /** 本次聊天请求端到端总耗时。 */
  total_ms: number
}

export interface ChatRequest {
  /** 本次提问所属会话的标识。 */
  session_id: string
  /** 用户输入的问题文本。 */
  question: string
  /** 最终回答采用的表达风格。 */
  answer_style: AnswerStyle
  /** 本次回答采用的 AI 生成模式。 */
  ai_mode: AiMode
}

export interface ChatStreamHandlers {
  /** 收到阶段状态事件时调用。 */
  onStatus?: (data: ChatStatusData) => void
  /** 收到工具调用及其执行结果时调用。 */
  onTool?: (data: ToolExecutionItem) => void
  /** 收到一段新增回答文本时调用。 */
  onToken: (data: { content: string }) => void
  /** 收到本次回答引用来源时调用。 */
  onSources: (data: { items: CitationItem[] }) => void
  /** 回答流正常结束并返回最终消息信息时调用。 */
  onDone: (data: {
    message_id: number
    answer_style: AnswerStyle
    refused: boolean
    timings?: ChatTimingData
  }) => void
  /** 服务端返回错误或流解析失败时调用。 */
  onError: (data: { code: number; message: string }) => void
}

export type ChatStreamEvent =
  | { event: 'status'; data: ChatStatusData }
  | { event: 'tool'; data: ToolExecutionItem }
  | { event: 'token'; data: { content: string } }
  | { event: 'sources'; data: { items: CitationItem[] } }
  | {
      event: 'done'
      data: {
        message_id: number
        answer_style: AnswerStyle
        refused: boolean
        timings?: ChatTimingData
      }
    }
  | { event: 'error'; data: { code: number; message: string } }
