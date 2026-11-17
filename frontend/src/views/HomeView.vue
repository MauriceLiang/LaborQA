<!-- 文件作用：展示应用首页、服务状态和主要功能入口。 -->
<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElButton } from 'element-plus'
import { Refresh } from '@element-plus/icons-vue'

import { useSystemStore } from '@/stores/system'

type StatusState = 'ok' | 'warning' | 'error' | 'pending'

interface StatusRow {
  /** 健康检查项目的中文名称。 */
  label: string
  /** 面向用户展示的当前状态文本。 */
  value: string
  /** 用于界面样式的标准状态类别。 */
  state: StatusState
}

// 全局系统仓库，提供后端健康检查结果和刷新操作。
const system = useSystemStore()
// 最近一次完成健康检查的时间戳。
const lastCheckedAt = ref<string | null>(null)

// 将服务状态转换为首页展示的逐项检查结果。
const statusRows = computed<StatusRow[]>(() => {
  // health 是最近一次从后端读取的健康数据。
  const health = system.health

  if (!health) {
    // 未取得健康数据时，根据请求状态区分检查中或连接失败。
    const state: StatusState = system.loading ? 'pending' : 'error'
    // value 是无健康数据时所有检查项共用的状态文案。
    const value = system.loading ? '检查中…' : '未连接'
    return [
      { label: 'API 服务', value, state },
      { label: 'SQLite 数据库', value, state },
      { label: '向量索引', value, state },
      { label: '本地 Embedding', value, state },
      { label: 'Embedding Provider', value, state },
      { label: 'Embedding 模型', value, state },
      { label: '索引兼容性', value, state },
      { label: '旧版 DOC 转换', value, state },
      { label: 'LLM API', value, state },
    ]
  }

  return [
    {
      label: 'API 服务',
      value: health.service === 'ok' ? '已连接' : '异常',
      state: health.service === 'ok' ? 'ok' : 'error',
    },
    {
      label: 'SQLite 数据库',
      value: health.database === 'ok' ? '正常' : '不可用',
      state: health.database === 'ok' ? 'ok' : 'error',
    },
    {
      label: '向量索引',
      value: health.vector_store === 'ready' ? '已就绪' : formatStatus(health.vector_store),
      state: health.vector_store === 'ready' ? 'ok' : 'warning',
    },
    {
      label: '本地 Embedding',
      value: health.embedding_ready ? '模型已缓存' : '模型未缓存',
      state: health.embedding_ready ? 'ok' : 'warning',
    },
    {
      label: 'Embedding Provider',
      value: health.embedding_provider === 'local' ? '本地模型' : 'API 模型',
      state: 'ok',
    },
    {
      label: 'Embedding 模型',
      value: health.embedding_model,
      state: 'ok',
    },
    {
      label: '索引兼容性',
      value: health.embedding_index_compatible ? '兼容' : '需要重建',
      state: health.embedding_index_compatible ? 'ok' : 'warning',
    },
    {
      label: '旧版 DOC 转换',
      value: health.doc_converter === 'ready' ? '可用' : '不可用',
      state: health.doc_converter === 'ready' ? 'ok' : 'warning',
    },
    {
      label: 'LLM API',
      value: health.llm_configured ? '已配置' : '未配置',
      state: health.llm_configured ? 'ok' : 'warning',
    },
  ]
})

// 依据所有检查项计算页面顶部展示的总体健康结论。
const overallStatus = computed(() => {
  if (system.loading && !system.health) return { label: '正在检查服务状态', state: 'pending' as StatusState }
  if (!system.health) return { label: '无法连接后端服务', state: 'error' as StatusState }

  // 只要任一 row 为异常或警告，总体状态就需要提示用户关注。
  const hasIssue = statusRows.value.some((row) => row.state === 'error' || row.state === 'warning')
  return {
    label: hasIssue ? '部分服务需要处理' : '服务运行正常',
    state: hasIssue ? 'warning' as StatusState : 'ok' as StatusState,
  }
})

// 将最近检查时间转换为界面显示格式。
const checkedAtLabel = computed(() => {
  if (!lastCheckedAt.value) return '尚未检查'

  // date 用于提取本地年月日及时分。
  const date = new Date(lastCheckedAt.value)
  // pad 将个位数补零，保证时间各部分均为两位。
  const pad = (value: number) => String(value).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`
})

/** 将向量存储内部状态映射为中文界面文案。
 * @param value 后端返回的原始状态字符串。
 */
function formatStatus(value: string) {
  // labels 保存需要翻译的内部状态和中文显示文本。
  const labels: Record<string, string> = {
    not_initialized: '未初始化',
    unavailable: '不可用',
    rebuilding: '重建中',
  }
  return labels[value] ?? value
}

/** 刷新后端健康状态并记录本次检查时间。 */
async function checkHealth() {
  await system.refreshHealth()
  lastCheckedAt.value = new Date().toISOString()
}

onMounted(() => {
  void checkHealth()
})
</script>

<template>
  <section class="system-page" :aria-busy="system.loading">
    <header class="page-intro documents-page-intro">
      <div class="system-page-heading">
        <h1>系统运行状态</h1>
        <p class="system-description">查看 API、数据库、向量索引与模型配置是否正常。</p>
      </div>

      <div class="system-page-actions">
        <ElButton class="system-check-button" type="primary" :loading="system.loading" :disabled="system.loading" @click="checkHealth">
          <Refresh :class="{ 'system-refresh-icon-spinning': system.loading }" aria-hidden="true" />
          <span>{{ system.loading ? '检查中…' : '检查后端连接' }}</span>
        </ElButton>
        <p class="system-last-checked">最近检查 · {{ checkedAtLabel }}</p>
      </div>
    </header>

    <p v-if="system.error" class="system-error" role="alert">{{ system.error }}</p>

    <section class="system-overall-status" :class="`system-status-${overallStatus.state}`" aria-live="polite">
      <span class="system-overall-dot" aria-hidden="true"></span>
      <strong>{{ overallStatus.label }}</strong>
    </section>

    <div class="system-status-list" role="list" aria-label="系统组件状态">
      <div v-for="row in statusRows" :key="row.label" class="system-status-row" role="listitem">
        <span class="system-status-label">{{ row.label }}</span>
        <span class="system-status-value" :class="`system-status-${row.state}`">
          <span class="system-status-dot" aria-hidden="true"></span>
          <span>{{ row.value }}</span>
        </span>
      </div>
    </div>
  </section>
</template>
