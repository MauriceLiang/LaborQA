<!-- 文件作用：收集用户问题并提供发送、停止和输入状态交互。 -->
<script setup lang="ts">
import { ref } from 'vue'
import { ElButton } from 'element-plus'
import { ArrowDown, ArrowUp } from '@element-plus/icons-vue'

import AnswerStyleSwitch from '@/components/chat/AnswerStyleSwitch.vue'
import type { AnswerStyle } from '@/types/sse'

defineProps<{
  /** 输入框当前内容，由父组件维护。 */
  modelValue: string
  /** 当前是否禁止提交或编辑。 */
  disabled: boolean
  /** 是否正在生成回答，决定显示停止操作。 */
  streaming: boolean
  /** 当前选中的回答表达风格。 */
  answerStyle: AnswerStyle
}>()

// 向父组件回传输入值、回答风格、折叠状态和提交控制事件。
const emit = defineEmits<{
  'update:modelValue': [value: string]
  'update:answerStyle': [value: AnswerStyle]
  'update:collapsed': [value: boolean]
  submit: []
  stop: []
}>()

// 标记问题输入区域是否折叠收起。
const collapsed = ref(false)

/** 切换输入区域的展开状态并通知父组件。 */
function toggleCollapsed() {
  collapsed.value = !collapsed.value
  emit('update:collapsed', collapsed.value)
}

/** 按下 Enter 且未组合输入时提交问题，Shift+Enter 保留换行。
 * @param event 当前文本框键盘事件。
 */
function handleKeydown(event: KeyboardEvent) {
  if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
    event.preventDefault()
    emit('submit')
  }
}
</script>

<template>
  <form
    v-if="!collapsed"
    class="question-input"
    @submit.prevent="emit('submit')"
  >
    <label class="sr-only" for="chat-question">输入你的劳动权益问题</label>
    <textarea
      id="chat-question"
      :value="modelValue"
      :disabled="disabled"
      maxlength="2000"
      rows="3"
      placeholder="描述你遇到的情况…"
      @input="emit('update:modelValue', ($event.target as HTMLTextAreaElement).value)"
      @keydown="handleKeydown"
    />
    <div class="question-input-footer">
      <div class="question-input-options">
        <AnswerStyleSwitch
          :model-value="answerStyle"
          :disabled="streaming"
          @update:model-value="emit('update:answerStyle', $event)"
        />
        <span class="input-hint">Enter 发送 · Shift+Enter 换行 · 最多 2000 字</span>
      </div>
      <span class="input-actions">
        <span class="question-count">{{ modelValue.trim().length }}/2000</span>
        <ElButton v-if="streaming" class="secondary-button stop-button" native-type="button" @click="emit('stop')">
          停止生成
        </ElButton>
        <ElButton
          v-else
          class="primary-button send-button"
          native-type="submit"
          :disabled="disabled || !modelValue.trim() || modelValue.trim().length > 2000"
        >
          发送问题
        </ElButton>
        <ElButton
          class="question-input-collapse"
          native-type="button"
          :aria-expanded="!collapsed"
          aria-controls="chat-question"
          aria-label="收起输入框"
          title="收起输入框"
          :disabled="streaming"
          @click="toggleCollapsed"
        >
          <ArrowDown aria-hidden="true" />
        </ElButton>
      </span>
    </div>
  </form>
  <ElButton
    v-else
    class="question-input-expand"
    native-type="button"
    aria-expanded="false"
    aria-controls="chat-question"
    aria-label="展开输入框"
    title="展开输入框"
    @click="toggleCollapsed"
  >
    <ArrowUp aria-hidden="true" />
  </ElButton>
</template>
