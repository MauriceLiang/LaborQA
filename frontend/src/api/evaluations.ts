// 文件作用：封装评估用例、评估任务及评估结果相关 API。
import type { components, paths } from '@/types/api.generated'
import { http } from '@/api/http'
import type { AiMode } from '@/types/preferences'

export type EvaluationRunSummary = components['schemas']['EvaluationRunSummary']
export type EvaluationRunDetail = components['schemas']['EvaluationRunDetail']
export type EvaluationRunJob = components['schemas']['EvaluationRunJob']
export type EvaluationMetrics = components['schemas']['EvaluationMetrics']
export type EvaluationResultItem = components['schemas']['EvaluationResultItem']
/** 可筛选的评估用例分页结果。 */
export type EvaluationCasePage = {
  items: EvaluationCase[]
  page: number
  size: number
  total: number
  pages: number
}
export type EvaluationRunPage = NonNullable<
  paths['/api/evaluations/runs']['get']['responses'][200]['content']['application/json']['data']
>
export type EvaluationExpectedType = components['schemas']['EvaluationExpectedType']
export type EvaluationJobStatus = components['schemas']['JobStatus']
export type EvaluationAnswerStyle = components['schemas']['AnswerStyle']

export type EvaluationCaseOrigin = 'BUILTIN' | 'CUSTOM'
export type EvaluationCaseStatus = 'ACTIVE' | 'ARCHIVED'
export type EvaluationCaseScope = 'BUILTIN_BASELINE' | 'ALL_ACTIVE' | 'SELECTED'

/** 评估用例期望命中的劳动法律资料来源。 */
export interface EvaluationExpectedSource {
  file_name: string
  chunk_no: number | null
}

/** 后端保存的评估用例及生命周期元数据。 */
export interface EvaluationCase {
  id: number
  topic: string
  expected_type: EvaluationExpectedType
  turns: string[]
  expected_points: string[]
  expected_sources: EvaluationExpectedSource[]
  should_show_compliance: boolean
  origin: EvaluationCaseOrigin
  status: EvaluationCaseStatus
  version: number
  created_at: string
  updated_at: string
  archived_at: string | null
}

/** 新建评估用例时提交的业务字段。 */
export interface EvaluationCaseInput {
  topic: string
  expected_type: EvaluationExpectedType
  turns: string[]
  expected_points: string[]
  expected_sources: EvaluationExpectedSource[]
  should_show_compliance: boolean
}

/** 更新评估用例时附带版本号以进行并发冲突校验。 */
export interface EvaluationCaseUpdate extends EvaluationCaseInput {
  version: number
}

/** 评估用例列表支持的分页和筛选条件。 */
export interface EvaluationCaseQuery {
  page: number
  size: number
  topic?: string
  expected_type?: EvaluationExpectedType
  is_multi_turn?: boolean
  origin?: EvaluationCaseOrigin
  status?: EvaluationCaseStatus
  include_archived?: boolean
}

/** 评估运行列表的分页及状态筛选条件。 */
export interface EvaluationRunQuery {
  page: number
  size: number
  status?: EvaluationJobStatus
}

/** 创建评估运行时需要提交的名称、用例范围和回答模式。 */
export interface EvaluationRunCreate {
  name: string
  case_ids: number[] | null
  answer_style: EvaluationAnswerStyle
  case_scope: EvaluationCaseScope
  ai_mode: AiMode
}

/** 分页查询评估用例并应用筛选条件。
 * @param query 分页及用例筛选参数。
 * @returns 评估用例分页数据。
 */
export async function getEvaluationCases(
  query: EvaluationCaseQuery,
): Promise<EvaluationCasePage> {
  // response 保存当前接口调用的 HTTP 响应，函数返回其业务数据部分。
  const response = await http.get<EvaluationCasePage>('/evaluations/cases', { params: query })
  return response.data
}

/** 新增自定义评估用例。
 * @param payload 用例主题、对话轮次和预期结果。
 * @returns 后端保存的用例。
 */
export async function createEvaluationCase(
  payload: EvaluationCaseInput,
): Promise<EvaluationCase> {
  // response 保存当前接口调用的 HTTP 响应，函数返回其业务数据部分。
  const response = await http.post<EvaluationCase>('/evaluations/cases', payload)
  return response.data
}

/** 获取单个评估用例详情。
 * @param id 评估用例主键。
 * @returns 用例内容和生命周期信息。
 */
export async function getEvaluationCase(id: number): Promise<EvaluationCase> {
  // response 保存当前接口调用的 HTTP 响应，函数返回其业务数据部分。
  const response = await http.get<EvaluationCase>(`/evaluations/cases/${id}`)
  return response.data
}

/** 更新已有评估用例并提交其版本号。
 * @param id 评估用例主键。
 * @param payload 更新后的用例内容及版本。
 * @returns 更新完成的用例。
 */
export async function updateEvaluationCase(
  id: number,
  payload: EvaluationCaseUpdate,
): Promise<EvaluationCase> {
  // response 保存当前接口调用的 HTTP 响应，函数返回其业务数据部分。
  const response = await http.patch<EvaluationCase>(`/evaluations/cases/${id}`, payload)
  return response.data
}

/** 删除自定义评估用例。
 * @param id 待删除用例的主键。
 */
export async function deleteEvaluationCase(id: number): Promise<void> {
  await http.delete(`/evaluations/cases/${id}`)
}

/** 分页查询评估运行记录。
 * @param query 分页及运行状态筛选参数。
 * @returns 评估运行分页结果。
 */
export async function getEvaluationRuns(query: EvaluationRunQuery): Promise<EvaluationRunPage> {
  // response 保存当前接口调用的 HTTP 响应，函数返回其业务数据部分。
  const response = await http.get<EvaluationRunPage>('/evaluations/runs', { params: query })
  return response.data
}

/** 创建评估任务并启动后台执行。
 * @param payload 运行名称、用例范围、回答风格和 AI 模式。
 * @returns 已受理任务的标识和进度状态。
 */
export async function createEvaluationRun(payload: EvaluationRunCreate): Promise<EvaluationRunJob> {
  // response 保存当前接口调用的 HTTP 响应，函数返回其业务数据部分。
  const response = await http.post<EvaluationRunJob>('/evaluations/runs', payload)
  return response.data
}

/** 查询指定评估运行的详情、指标及逐用例结果。
 * @param id 评估运行主键。
 * @returns 评估运行详情。
 */
export async function getEvaluationRun(id: number): Promise<EvaluationRunDetail> {
  // response 保存当前接口调用的 HTTP 响应，函数返回其业务数据部分。
  const response = await http.get<EvaluationRunDetail>(`/evaluations/runs/${id}`)
  return response.data
}

/** 删除指定评估运行及其派生结果。
 * @param id 评估运行主键。
 */
export async function deleteEvaluationRun(id: number): Promise<void> {
  await http.delete(`/evaluations/runs/${id}`)
}
