import type { AnswerStyle, CitationItem, ToolExecutionItem } from '@/types/sse'
import type { AiMode } from '@/types/preferences'

export interface UiMessage {
  key: string
  id: number | null
  role: 'user' | 'assistant'
  content: string
  citations: CitationItem[]
  toolExecutions: ToolExecutionItem[]
  answerStyle: AnswerStyle | null
  aiMode: AiMode | null
  refused: boolean
  progressText: string | null
  status: 'complete' | 'streaming' | 'stopped' | 'error'
}
