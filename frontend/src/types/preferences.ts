// 文件作用：声明 AI 模式选项及模式校验、展示文案辅助函数。
export type AiMode = 'speed' | 'balanced' | 'quality'

/** 用户未选择模式时采用的默认 AI 回答模式。 */
export const DEFAULT_AI_MODE: AiMode = 'balanced'

/** 向用户展示的模式选项及其说明文案。 */
export const AI_MODE_OPTIONS: Array<{
  /** 传递给 API 和状态管理的稳定模式值。 */
  value: AiMode
  /** 选择器中显示的短标签。 */
  label: string
  /** 描述该模式对响应速度和回答质量的取舍。 */
  description: string
}> = [
  { value: 'speed', label: '快速', description: '优先响应速度' },
  { value: 'balanced', label: '均衡', description: '速度与质量平衡' },
  { value: 'quality', label: '高质量', description: '优先回答质量' },
]

/** 判断未知值是否为前端支持的 AI 模式之一。
 * @param value 待校验的任意输入值。
 * @returns 值属于 AiMode 时返回 true。
 */
export function isAiMode(value: unknown): value is AiMode {
  return value === 'speed' || value === 'balanced' || value === 'quality'
}

/** 获取模式对应的中文短标签，缺省值统一显示为“均衡”。
 * @param value 当前模式；为空时采用默认模式文案。
 * @returns 可在界面展示的模式名称。
 */
export function aiModeLabel(value: AiMode | null | undefined): string {
  // option 表示候选模式配置，匹配后读取其显示标签。
  return AI_MODE_OPTIONS.find((option) => option.value === value)?.label ?? '均衡'
}
