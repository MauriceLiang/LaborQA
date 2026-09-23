<!-- 文件作用：展示聊天过程中调用的工具及其执行结果。 -->
<script setup lang="ts">
import type { ToolExecutionItem } from '@/types/sse'

defineProps<{
  /** 本条回答过程中产生的工具调用和输出列表。 */
  executions: ToolExecutionItem[]
}>()
</script>

<template>
  <section v-if="executions.length" class="tool-panel" aria-label="辅助材料清单">
    <h3>材料清单</h3>
    <ul v-for="execution in executions" :key="`${execution.tool_name}-${execution.input.description}`">
      <li v-for="material in execution.output.materials" :key="material">{{ material }}</li>
      <li class="tool-note">{{ execution.output.note }}</li>
    </ul>
  </section>
</template>
