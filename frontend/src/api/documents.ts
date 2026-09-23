// 文件作用：封装文档上传、查询、删除、重导入及文本块接口。
import type { components, paths } from '@/types/api.generated'
import { http } from '@/api/http'

export type DocumentItem = components['schemas']['DocumentItem']
export type ChunkItem = components['schemas']['ChunkItem']
export type DocumentUploadAccepted = components['schemas']['DocumentUploadAccepted']
export type BuiltinDocumentStatus = components['schemas']['BuiltinDocumentStatus']
export type BuiltinDocumentImportAccepted =
  components['schemas']['BuiltinDocumentImportAccepted']
export type DocumentPage = NonNullable<
  paths['/api/documents']['get']['responses'][200]['content']['application/json']['data']
>
export type ChunkPage = NonNullable<
  paths['/api/documents/{id}/chunks']['get']['responses'][200]['content']['application/json']['data']
>

/** 文档分页查询使用的筛选条件。 */
export interface DocumentQuery {
  page: number
  size: number
  status?: DocumentItem['status']
  keyword?: string
}

/** 按分页和筛选条件获取文档列表。
 * @param query 页码、状态和关键词过滤条件。
 * @returns 文档分页结果。
 */
export async function getDocuments(query: DocumentQuery): Promise<DocumentPage> {
  // response 保存文档列表接口的 HTTP 响应。
  const response = await http.get<DocumentPage>('/documents', { params: query })
  return response.data
}

/** 获取单份文档的详情和导入状态。
 * @param id 文档主键。
 * @returns 文档数据。
 */
export async function getDocument(id: number): Promise<DocumentItem> {
  // response 保存文档详情接口的 HTTP 响应。
  const response = await http.get<DocumentItem>(`/documents/${id}`)
  return response.data
}

/** 获取项目内置文档的导入统计和逐项状态。
 * @returns 内置文档导入状态。
 */
export async function getBuiltinDocumentStatus(): Promise<BuiltinDocumentStatus> {
  // response 保存内置文档状态接口的 HTTP 响应。
  const response = await http.get<BuiltinDocumentStatus>('/documents/builtin/status')
  return response.data
}

/** 请求后端导入项目自带的法律文档。
 * @returns 已导入、处理中及新调度文档的统计信息。
 */
export async function importBuiltinDocuments(): Promise<BuiltinDocumentImportAccepted> {
  // response 保存内置文档导入任务的 HTTP 响应。
  const response = await http.post<BuiltinDocumentImportAccepted>('/documents/builtin/import')
  return response.data
}

/** 上传本地文档并启动后端解析和索引任务。
 * @param file 用户选择的待上传文件。
 * @returns 已接受的文档标识和初始状态。
 */
export async function uploadDocument(file: File): Promise<DocumentUploadAccepted> {
  // body 使用 multipart/form-data 传递浏览器文件对象。
  const body = new FormData()
  body.append('file', file)
  // response 保存上传接口返回的受理信息。
  const response = await http.post<DocumentUploadAccepted>('/documents/upload', body, {
    timeout: 60_000,
  })
  return response.data
}

/** 请求重新处理指定的失败文档。
 * @param id 待重新导入的文档主键。
 */
export async function reimportDocument(id: number): Promise<void> {
  await http.post(`/documents/${id}/reimport`)
}

/** 删除文档及后端派生的文本块和索引数据。
 * @param id 待删除的文档主键。
 */
export async function deleteDocument(id: number): Promise<void> {
  await http.delete(`/documents/${id}`)
}

/** 分页获取指定文档切分出的文本块。
 * @param id 文档主键。
 * @param page 请求页码。
 * @param size 每页文本块数量。
 * @returns 文本块分页结果。
 */
export async function getDocumentChunks(
  id: number,
  page: number,
  size: number,
): Promise<ChunkPage> {
  // response 保存文本块列表接口的 HTTP 响应。
  const response = await http.get<ChunkPage>(`/documents/${id}/chunks`, {
    params: { page, size },
  })
  return response.data
}
