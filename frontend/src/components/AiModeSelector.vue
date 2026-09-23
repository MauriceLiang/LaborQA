<!-- 文件作用：提供快速、均衡和高质量 AI 回答模式选择控件。 -->
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

/* 保存用户 AI 模式选择的 Pinia 状态仓库。 */
const preferencesStore = usePreferencesStore()
/* 由当前模式匹配得到的选项，用于显示标签和说明。 */
const currentOption = computed(
  () => AI_MODE_OPTIONS.find((option) => option.value === preferencesStore.aiMode) ?? AI_MODE_OPTIONS[1],
)

/** 校验下拉菜单命令并更新用户默认 AI 模式。
 * @param command 下拉组件传出的候选模式值。
 */
function selectMode(command: string | number | object) {
  if (typeof command !== 'string' || !isAiMode(command)) return
  if (command === preferencesStore.aiMode) return
  preferencesStore.setAiMode(command)
  // option 是与所选模式对应的展示配置，用于取得成功提示标签。
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
