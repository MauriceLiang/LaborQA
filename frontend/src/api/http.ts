// 文件作用：创建统一 HTTP 客户端，处理 API 错误格式和健康检查请求。
import axios from 'axios'

import type { paths } from '@/types/api.generated'

/** 从生成的 OpenAPI 路径类型中提取健康检查 HTTP 响应。 */
type HealthResponse = paths['/api/health']['get']['responses'][200]['content']['application/json']
/** 健康检查响应中非空的业务数据结构。 */
export type HealthData = NonNullable<HealthResponse['data']>

interface ApiResponse<T> {
  /** 后端约定的业务状态码；0 表示成功。 */
  code: number
  /** 面向调用方的状态或错误说明。 */
  message: string
  /** 成功时的业务数据，错误或无数据时为空。 */
  data: T | null
}

/** 携带后端业务错误码的前端 API 异常。 */
export class ApiError extends Error {
  constructor(
    readonly code: number,
    message: string,
  ) {
    super(message)
    this.name = 'ApiError'
  }
}

/** 将网络、超时和业务异常转换成适合界面展示的提示。
 * @param error 捕获到的任意异常对象。
 * @returns 中文错误提示文本。
 */
export function getErrorMessage(error: unknown): string {
  if (error instanceof ApiError) return error.message

  if (axios.isAxiosError(error)) {
    if (error.code === 'ECONNABORTED' || error.code === 'ETIMEDOUT') {
      return '请求处理超时，请稍后重试'
    }

    if (!error.response) {
      return '无法连接服务器，请检查网络或服务状态'
    }
  }

  if (error instanceof Error) return error.message
  return '请求失败，请稍后重试'
}

/** 项目共用 Axios 客户端，集中配置 API 地址和请求超时。 */
export const http = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL || 'http://127.0.0.1:8000/api',
  timeout: 8000,
})

http.interceptors.response.use(
  // response 是 Axios 收到的成功 HTTP 响应。
  (response) => {
    // body 是后端统一包装的 API 响应体。
    const body = response.data as ApiResponse<unknown>
    if (body.code !== 0) {
      throw new ApiError(body.code, body.message || '后端请求失败')
    }
    return { ...response, data: body.data }
  },
  // error 是 Axios 请求失败时传入的原始异常。
  (error: unknown) => {
    if (axios.isAxiosError(error)) {
      // requestId 用于将前端错误和后端请求日志关联起来。
      const requestId = error.response?.headers['x-request-id']
      if (requestId) console.error(`API request failed (X-Request-ID: ${requestId})`)

      // body 是失败响应中的未校验内容，后续需逐字段检查结构。
      const body: unknown = error.response?.data
      if (
        typeof body === 'object' &&
        body !== null &&
        'code' in body &&
        typeof body.code === 'number' &&
        'message' in body &&
        typeof body.message === 'string'
      ) {
        return Promise.reject(new ApiError(body.code, body.message))
      }
    }
    return Promise.reject(error)
  },
)

/** 请求后端健康检查端点并返回健康数据。
 * @returns 服务、数据库和检索组件状态。
 */
export async function getHealth(): Promise<HealthData> {
  // response 保存健康检查端点的 HTTP 响应。
  const response = await http.get<HealthData>('/health')
  return response.data
}
