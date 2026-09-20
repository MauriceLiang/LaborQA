<script setup lang="ts">
import { computed } from 'vue'
import {
  ElDropdown,
  ElDropdownItem,
  ElDropdownMenu,
  ElMessage,
} from 'element-plus'

import { usePreferencesStore } from '@/stores/preferences'
import {
  AI_MODE_OPTIONS,
  type AiMode,
  isAiMode,
} from '@/types/preferences'

const preferencesStore = usePreferencesStore()
const currentOption = computed(
  () => AI_MODE_OPTIONS.find((option) => option.value === preferencesStore.aiMode) ?? AI_MODE_OPTIONS[1],
)

function selectMode(command: string | number | object) {
  if (typeof command !== 'string' || !isAiMode(command)) return
  if (command === preferencesStore.aiMode) return
  preferencesStore.setAiMode(command)
  const option = AI_MODE_OPTIONS.find((item) => item.value === command)
  ElMessage.success(`已切换至${option?.label ?? '均衡'}模式，新任务将使用该模式`)
}
</script>

<template>
  <ElDropdown class="ai-mode-selector" trigger="click" popper-class="ai-mode-popper" @command="selectMode">
    <button type="button" class="ai-mode-trigger" aria-label="选择 AI 模式" aria-haspopup="menu">
      <span class="ai-mode-trigger-label">{{ currentOption.label }}</span>
    </button>
    <template #dropdown>
      <ElDropdownMenu>
        <ElDropdownItem
          v-for="option in AI_MODE_OPTIONS"
          :key="option.value"
          :command="option.value"
          :data-mode="option.value"
          class="ai-mode-option"
          :aria-current="option.value === preferencesStore.aiMode ? 'true' : undefined"
          :class="{ 'ai-mode-option-active': option.value === preferencesStore.aiMode }"
        >
          <span class="ai-mode-option-copy">
            <span class="ai-mode-option-label">{{ option.label }}</span>
            <span class="ai-mode-option-description">{{ option.description }}</span>
          </span>
        </ElDropdownItem>
      </ElDropdownMenu>
    </template>
  </ElDropdown>
</template>
