// 文件作用：管理服务健康状态及全局系统信息。
import { defineStore } from 'pinia'
import { ref } from 'vue'

import { getErrorMessage, getHealth, type HealthData } from '@/api/http'

/** 创建系统状态仓库，统一保存后端健康信息及其加载状态。 */
export const useSystemStore = defineStore('system', () => {
  // 最近一次成功获取的后端健康状态；尚未查询时为空。
  const health = ref<HealthData | null>(null)
  // 标记当前是否正在请求健康检查接口。
  const loading = ref(false)
  // 健康检查请求失败时显示的错误信息。
  const error = ref('')

/** 请求最新服务状态并更新健康数据、加载标记和错误信息。 */
  async function refreshHealth() {
    loading.value = true
    error.value = ''
    try {
      health.value = await getHealth()
    } catch (cause) {
      // cause 是本次健康检查失败的异常。
      error.value = getErrorMessage(cause)
      health.value = null
    } finally {
      loading.value = false
    }
  }

  return { health, loading, error, refreshHealth }
})
