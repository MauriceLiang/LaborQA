<!-- 文件作用：展示单个文档文本块的详细内容和检索元数据。 -->
<script setup lang="ts">
import { ElDialog } from 'element-plus'

import type { ChunkItem } from '@/api/documents'

defineProps<{
  /** 当前选中的文本块；未选中时不展示正文。 */
  chunk?: ChunkItem
  /** 控制详情弹窗的显示状态。 */
  modelValue: boolean
}>()
defineEmits<{
  /** 向父组件同步弹窗开关状态。 */
  'update:modelValue': [value: boolean]
}>()
</script>

<template>
  <ElDialog
    :model-value="modelValue"
    :title="chunk ? `Chunk ${chunk.chunk_no} 原文` : 'Chunk 原文'"
    width="min(760px, 92vw)"
    @update:model-value="$emit('update:modelValue', $event)"
  >
    <pre v-if="chunk" class="chunk-content">{{ chunk.content }}</pre>
  </ElDialog>
</template>
