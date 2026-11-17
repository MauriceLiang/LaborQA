// 文件作用：封装检索策略和检索实验的创建、执行、查询及比较 API。
import type { components, paths } from '@/types/api.generated'
import { http } from '@/api/http'
import type { AiMode } from '@/types/preferences'

export type ExperimentConfig = components['schemas']['ExperimentConfig']
export type ExperimentCaseScope = 'BUILTIN_BASELINE' | 'ALL_ACTIVE' | 'SELECTED'

/** 新建检索实验所需的策略、用例范围和回答模式。 */
export interface ExperimentCreate {
  name: string
  strategy_ids: number[]
  case_scope: ExperimentCaseScope
  case_ids: number[] | null
  answer_style: 'plain' | 'legal'
  ai_mode: AiMode
}

/** 实验执行时使用的检索策略不可变快照。 */
export interface RetrievalStrategySnapshot {
  strategy_id: number
  name: string
  version: number
  config: ExperimentConfig
}

/** 检索实验列表中的摘要数据。 */
export type ExperimentSummary = components['schemas']['ExperimentSummary'] & {
  archived_at?: string | null
}
/** 包含策略快照和配置指标的检索实验详情。 */
export type ExperimentDetail = Omit<components['schemas']['ExperimentDetail'], 'case_count' | 'strategy_snapshots'> & {
  case_count: number
  strategy_snapshots: RetrievalStrategySnapshot[]
  archived_at?: string | null
}
export type ExperimentJob = components['schemas']['ExperimentJob']
export type ExperimentConfigResult = components['schemas']['ExperimentConfigResult']
export type ExperimentStatus = components['schemas']['JobStatus']
export type CitationItem = components['schemas']['CitationItem']
type GeneratedExperimentPage = NonNullable<
  paths['/api/retrieval-experiments']['get']['responses'][200]['content']['application/json']['data']
>
export type ExperimentPage = Omit<GeneratedExperimentPage, 'items'> & {
  items: ExperimentSummary[]
}

/** 检索实验列表的分页和状态筛选参数。 */
export interface ExperimentQuery {
  page: number
  size: number
  status?: ExperimentStatus
  include_archived?: boolean
}

/** 可复用检索策略及其启用、归档和版本信息。 */
export interface RetrievalStrategy {
  id: number
  name: string
  description: string
  builtin_key: string | null
  config: ExperimentConfig
  is_builtin: boolean
  version: number
  is_active: boolean
  archived_at: string | null
  created_at: string
  updated_at: string
}

/** 新建或编辑检索策略时提交的可变字段。 */
export interface RetrievalStrategyPayload {
  name: string
  description: string
  config: ExperimentConfig
}

/** 检索策略历史版本的配置快照和创建信息。 */
export interface RetrievalStrategyVersion {
  id: number
  strategy_id: number
  version: number
  name: string
  description: string
  config: ExperimentConfig
  created_at: string
}

/** 检索预览流程中单个阶段的状态、说明和耗时。 */
export interface RetrievalTraceStage {
  stage: string
  status: 'completed' | 'skipped' | 'failed'
  detail: string
  duration_ms: number | null
}

/** 单次检索策略预览的回答、证据、耗时和诊断轨迹。 */
export interface RetrievalPreview {
  strategy_id: number
  strategy_name: string
  strategy_version: number
  index_mode: string
  limitations: string[]
  question: string
  rewritten_question: string
  answer: string
  refused: boolean
  answer_mode: 'RAG_ANSWER' | 'PARTIAL_ANSWER' | 'TOOL_ONLY' | 'REFUSAL'
  evidence_decision: 'SUFFICIENT' | 'PARTIAL' | 'INSUFFICIENT'
  evidence_reason: string
  supported_points: string[]
  missing_points: string[]
  tool_execution: components['schemas']['ToolExecutionItem'] | null
  retrieval_ms: number
  retrieved_sources: CitationItem[]
  citations: CitationItem[]
  trace: RetrievalTraceStage[]
  config: ExperimentConfig
}

/** 分页获取检索实验列表。
 * @param query 分页、状态和归档过滤条件。
 * @returns 检索实验分页数据。
 */
export async function getExperiments(query: ExperimentQuery): Promise<ExperimentPage> {
  // response 保存当前接口调用的 HTTP 响应，函数返回其业务数据部分。
  const response = await http.get<ExperimentPage>('/retrieval-experiments', { params: query })
  return response.data
}

/** 创建检索实验并启动后台运行。
 * @param payload 实验名称、策略和评估用例范围。
 * @returns 已受理的实验任务状态。
 */
export async function createExperiment(payload: ExperimentCreate): Promise<ExperimentJob> {
  // response 保存当前接口调用的 HTTP 响应，函数返回其业务数据部分。
  const response = await http.post<ExperimentJob>('/retrieval-experiments', payload)
  return response.data
}

/** 查询检索实验详情及各策略配置结果。
 * @param id 实验主键。
 * @returns 完整实验详情。
 */
export async function getExperiment(id: number): Promise<ExperimentDetail> {
  // response 保存当前接口调用的 HTTP 响应，函数返回其业务数据部分。
  const response = await http.get<ExperimentDetail>(`/retrieval-experiments/${id}`)
  return response.data
}

/** 获取检索策略列表。
 * @param params 可选的归档记录包含条件。
 * @returns 检索策略数组。
 */
export async function listRetrievalStrategies(params?: { include_archived?: boolean }): Promise<RetrievalStrategy[]> {
  // response 保存当前接口调用的 HTTP 响应，函数返回其业务数据部分。
  const response = await http.get<RetrievalStrategy[]>('/retrieval-strategies', { params })
  return response.data
}

/** 创建可复用检索策略。
 * @param payload 策略名称、说明和检索配置。
 * @returns 已创建策略。
 */
export async function createRetrievalStrategy(payload: RetrievalStrategyPayload): Promise<RetrievalStrategy> {
  // response 保存当前接口调用的 HTTP 响应，函数返回其业务数据部分。
  const response = await http.post<RetrievalStrategy>('/retrieval-strategies', payload)
  return response.data
}

/** 更新检索策略并生成新的配置版本。
 * @param id 策略主键。
 * @param payload 最新策略名称、说明和配置。
 * @returns 更新后的策略。
 */
export async function updateRetrievalStrategy(id: number, payload: RetrievalStrategyPayload): Promise<RetrievalStrategy> {
  // response 保存当前接口调用的 HTTP 响应，函数返回其业务数据部分。
  const response = await http.patch<RetrievalStrategy>(`/retrieval-strategies/${id}`, payload)
  return response.data
}

/** 查询指定策略的历史版本列表。
 * @param id 策略主键。
 * @returns 按后端定义顺序排列的策略版本。
 */
export async function listRetrievalStrategyVersions(id: number): Promise<RetrievalStrategyVersion[]> {
  // response 保存当前接口调用的 HTTP 响应，函数返回其业务数据部分。
  const response = await http.get<RetrievalStrategyVersion[]>(`/retrieval-strategies/${id}/versions`)
  return response.data
}

/** 将检索策略恢复为指定历史版本。
 * @param id 策略主键。
 * @param version 要恢复的历史版本号。
 * @returns 恢复后产生的新策略版本。
 */
export async function restoreRetrievalStrategyVersion(id: number, version: number): Promise<RetrievalStrategy> {
  // response 保存当前接口调用的 HTTP 响应，函数返回其业务数据部分。
  const response = await http.post<RetrievalStrategy>(`/retrieval-strategies/${id}/versions/${version}/restore`)
  return response.data
}

/** 启用或停用检索策略。
 * @param id 策略主键。
 * @param isActive true 表示启用，false 表示停用。
 * @returns 更新后的策略状态。
 */
export async function setRetrievalStrategyActive(id: number, isActive: boolean): Promise<RetrievalStrategy> {
  // response 保存当前接口调用的 HTTP 响应，函数返回其业务数据部分。
  const response = await http.patch<RetrievalStrategy>(`/retrieval-strategies/${id}/status`, { is_active: isActive })
  return response.data
}

/** 归档检索策略，使其不再作为活动策略使用。
 * @param id 策略主键。
 * @returns 归档后的策略。
 */
export async function archiveRetrievalStrategy(id: number): Promise<RetrievalStrategy> {
  // response 保存当前接口调用的 HTTP 响应，函数返回其业务数据部分。
  const response = await http.post<RetrievalStrategy>(`/retrieval-strategies/${id}/archive`)
  return response.data
}

/** 从归档状态恢复检索策略。
 * @param id 策略主键。
 * @returns 恢复后的策略。
 */
export async function restoreRetrievalStrategy(id: number): Promise<RetrievalStrategy> {
  // response 保存当前接口调用的 HTTP 响应，函数返回其业务数据部分。
  const response = await http.post<RetrievalStrategy>(`/retrieval-strategies/${id}/restore`)
  return response.data
}

/** 永久删除检索策略。
 * @param id 策略主键。
 */
export async function deleteRetrievalStrategy(id: number): Promise<void> {
  await http.delete(`/retrieval-strategies/${id}`)
}

/** 以指定策略和问题执行一次检索预览。
 * @param payload 问题、回答风格、策略标识及 AI 模式。
 * @returns 回答、引用和逐阶段检索轨迹。
 */
export async function previewRetrieval(payload: {
  question: string
  answer_style: 'plain' | 'legal'
  strategy_id: number
  ai_mode: AiMode
}): Promise<RetrievalPreview> {
  // response 保存当前接口调用的 HTTP 响应，函数返回其业务数据部分。
  const response = await http.post<RetrievalPreview>(
    '/retrieval-experiments/preview',
    payload,
    { timeout: 120_000 },
  )
  return response.data
}

/** 删除检索实验及其结果。
 * @param id 实验主键。
 */
export async function deleteExperiment(id: number): Promise<void> {
  await http.delete(`/retrieval-experiments/${id}`)
}

/** 归档检索实验。
 * @param id 实验主键。
 * @returns 归档后的实验摘要。
 */
export async function archiveRetrievalExperiment(id: number): Promise<ExperimentSummary> {
  // response 保存当前接口调用的 HTTP 响应，函数返回其业务数据部分。
  const response = await http.post<ExperimentSummary>(`/retrieval-experiments/${id}/archive`)
  return response.data
}

/** 从归档状态恢复检索实验。
 * @param id 实验主键。
 * @returns 恢复后的实验摘要。
 */
export async function restoreRetrievalExperiment(id: number): Promise<ExperimentSummary> {
  // response 保存当前接口调用的 HTTP 响应，函数返回其业务数据部分。
  const response = await http.post<ExperimentSummary>(`/retrieval-experiments/${id}/restore`)
  return response.data
}

/** 导出检索实验的可下载文本内容。
 * @param id 实验主键。
 * @returns 实验导出内容字符串。
 */
export async function exportRetrievalExperiment(id: number): Promise<string> {
  // response 保存当前接口调用的 HTTP 响应，函数返回其业务数据部分。
  const response = await http.get<string>(`/retrieval-experiments/${id}/export`)
  return response.data
}

/** 复制已有检索实验并以新名称创建任务。
 * @param id 源实验主键。
 * @param name 新实验名称。
 * @returns 已创建复制任务的状态。
 */
export async function copyExperiment(id: number, name: string): Promise<ExperimentJob> {
  // response 保存当前接口调用的 HTTP 响应，函数返回其业务数据部分。
  const response = await http.post<ExperimentJob>(`/retrieval-experiments/${id}/copy`, { name })
  return response.data
}
