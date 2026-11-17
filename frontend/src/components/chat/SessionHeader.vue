<!-- 文件作用：展示当前会话标题及会话相关操作入口。 -->
<script setup lang="ts">
import { ElButton } from 'element-plus'
import { CirclePlus, Document } from '@element-plus/icons-vue'

defineProps<{
  /** 当前会话标识，用于判断会话操作状态。 */
  sessionId: string
  /** 历史会话加载状态。 */
  historyLoading: boolean
  /** 聊天回答流是否进行中。 */
  streaming: boolean
}>()

defineEmits<{
  /** 请求父组件创建并切换到新会话。 */
  newSession: []
}>()
</script>

<template>
  <header class="session-header">
    <div class="session-header-actions">
      <ElButton
        class="new-session-button"
        type="primary"
        :disabled="historyLoading || streaming"
        @click="$emit('newSession')"
      >
        <CirclePlus aria-hidden="true" />
        <span>新建对话</span>
      </ElButton>
      <span class="knowledge-pill">
        <Document aria-hidden="true" />
        <span>基于已导入资料回答</span>
      </span>
    </div>
    <span class="session-status" aria-live="polite">
      <span v-if="historyLoading">正在恢复会话记录…</span>
      <span v-else-if="streaming">正在生成回答</span>
      <span v-else-if="sessionId">当前对话已保存</span>
    </span>
  </header>
</template>
