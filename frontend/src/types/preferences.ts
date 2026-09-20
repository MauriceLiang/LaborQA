export type AiMode = 'speed' | 'balanced' | 'quality'

export const DEFAULT_AI_MODE: AiMode = 'balanced'

export const AI_MODE_OPTIONS: Array<{
  value: AiMode
  label: string
  description: string
  icon: string
}> = [
  { value: 'speed', label: '快速', description: '优先响应速度', icon: '⚡' },
  { value: 'balanced', label: '均衡', description: '速度与质量平衡', icon: '⚖' },
  { value: 'quality', label: '高质量', description: '优先回答质量', icon: '◉' },
]

export function isAiMode(value: unknown): value is AiMode {
  return value === 'speed' || value === 'balanced' || value === 'quality'
}

export function aiModeLabel(value: AiMode | null | undefined): string {
  return AI_MODE_OPTIONS.find((option) => option.value === value)?.label ?? '均衡'
}
