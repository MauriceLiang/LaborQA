<!-- 文件作用：渲染单行 Markdown 文本片段及其行内格式。 -->
<script setup lang="ts">
import type { MarkdownInlineToken } from '@/utils/markdown'

defineProps<{
  /** 按文本顺序排列、由解析器生成的行内 Markdown 标记。 */
  tokens: MarkdownInlineToken[]
}>()
</script>

<template>
  <template v-for="(token, index) in tokens" :key="`${token.type}-${index}`">
    <br v-if="token.type === 'line-break'" />
    <strong v-else-if="token.type === 'strong'">{{ token.text }}</strong>
    <em v-else-if="token.type === 'emphasis'">{{ token.text }}</em>
    <code v-else-if="token.type === 'code'">{{ token.text }}</code>
    <span v-else>{{ token.text }}</span>
  </template>
</template>
