<!-- 文件作用：提供普通表达与法律专业表达两种回答风格的切换控件。 -->
<script setup lang="ts">
import type { AnswerStyle } from '@/types/sse'

defineProps<{
  /** 当前选中的回答表达风格。 */
  modelValue: AnswerStyle
  /** 流式生成期间为 true，用于禁用切换。 */
  disabled: boolean
}>()

defineEmits<{
  /** 通知父组件回答风格发生改变。 */
  'update:modelValue': [value: AnswerStyle]
}>()
</script>

<template>
  <div class="answer-style-switch" role="group" aria-label="回答风格">
    <button
      type="button"
      :aria-pressed="modelValue === 'plain'"
      :disabled="disabled"
      @click="$emit('update:modelValue', 'plain')"
    >
      通俗解释
    </button>
    <span class="answer-style-divider" aria-hidden="true">/</span>
    <button
      type="button"
      :aria-pressed="modelValue === 'legal'"
      :disabled="disabled"
      @click="$emit('update:modelValue', 'legal')"
    >
      法律条文风格
    </button>
  </div>
</template>
