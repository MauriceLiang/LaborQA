// 文件作用：封装缺失知识记录的查询和管理 API。
import type { components, paths } from '@/types/api.generated'
import { http } from '@/api/http'

export type MissingKnowledgeItem = components['schemas']['MissingKnowledgeItem']
export type MissingKnowledgeStatus = components['schemas']['MissingKnowledgeStatus']
export type MissingKnowledgePage = NonNullable<
  paths['/api/missing-knowledge']['get']['responses'][200]['content']['application/json']['data']
>
export type MissingKnowledgeSort = 'count_desc' | 'last_seen_desc'

export interface MissingKnowledgeQuery {
  /** 请求的分页页码，从 1 开始。 */
  page: number
  /** 单页最多返回的记录数。 */
  size: number
  /** 可选的知识缺口处理状态筛选条件。 */
  status?: MissingKnowledgeStatus
  /** 可选的关键词过滤条件。 */
  keyword?: string
  /** 列表采用的排序方式。 */
  sort: MissingKnowledgeSort
}

export interface MissingKnowledgeUpdate {
  /** 更新后的知识缺口状态。 */
  status: MissingKnowledgeStatus
  /** 处理备注；传 null 表示清除已有备注。 */
  note: string | null
}

/** 分页查询缺失知识记录。
 * @param query 页码、筛选、关键词和排序参数。
 * @returns 符合条件的分页结果。
 */
export async function getMissingKnowledge(
  query: MissingKnowledgeQuery,
): Promise<MissingKnowledgePage> {
  // response 保存后端返回的缺失知识分页响应。
  const response = await http.get<MissingKnowledgePage>('/missing-knowledge', {
    params: query,
  })
  return response.data
}

/** 更新单条缺失知识记录的状态及处理备注。
 * @param id 缺失知识记录主键。
 * @param payload 要保存的新状态和备注。
 * @returns 更新后的记录。
 */
export async function updateMissingKnowledge(
  id: number,
  payload: MissingKnowledgeUpdate,
): Promise<MissingKnowledgeItem> {
  // response 保存后端更新后的知识缺口记录。
  const response = await http.patch<MissingKnowledgeItem>(
    `/missing-knowledge/${id}`,
    payload,
  )
  return response.data
}
