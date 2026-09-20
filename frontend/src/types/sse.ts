import type { components } from './api.generated'

export type AnswerStyle = 'plain' | 'legal'
export type CitationItem = components['schemas']['CitationItem']
export type ToolExecutionItem = components['schemas']['ToolExecutionItem']
export type ChatStage = 'understanding' | 'retrieving' | 'verifying' | 'generating'

export interface ChatStatusData {
  stage: ChatStage
  message: string
}

export interface ChatTimingData {
  session_ms: number
  rewrite_ms: number
  retrieval_ms: number
  evidence_judge_ms: number
  pre_answer_ms: number
  ttft_ms: number | null
  answer_generation_ms: number
  persist_ms: number
  total_ms: number
}

export interface ChatRequest {
  session_id: string
  question: string
  answer_style: AnswerStyle
}

export interface ChatStreamHandlers {
  onStatus?: (data: ChatStatusData) => void
  onTool?: (data: ToolExecutionItem) => void
  onToken: (data: { content: string }) => void
  onSources: (data: { items: CitationItem[] }) => void
  onDone: (data: {
    message_id: number
    answer_style: AnswerStyle
    refused: boolean
    timings?: ChatTimingData
  }) => void
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
