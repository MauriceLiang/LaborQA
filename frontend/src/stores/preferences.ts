// 文件作用：保存用户界面偏好和 AI 模式，并通过本地存储持久化。
import { defineStore } from 'pinia'
import { ref } from 'vue'

import {
  DEFAULT_AI_MODE,
  isAiMode,
  type AiMode,
} from '@/types/preferences'

// localStorage 中保存 AI 模式偏好的固定键名。
const AI_MODE_STORAGE_KEY = 'labor-rights-qa.ai-mode'

/** 创建并返回用户偏好状态仓库。 */
export const usePreferencesStore = defineStore('preferences', () => {
  // 从浏览器本地存储读取的原始模式字符串，尚未验证。
  const storedMode = localStorage.getItem(AI_MODE_STORAGE_KEY)
  // 当前有效 AI 模式；存储值无效时回退到默认模式。
  const aiMode = ref<AiMode>(isAiMode(storedMode) ? storedMode : DEFAULT_AI_MODE)

/** 更新当前 AI 模式并将选择写入浏览器本地存储。
   * @param value 需要保存的有效 AI 模式。
   */
  function setAiMode(value: AiMode) {
    aiMode.value = value
    localStorage.setItem(AI_MODE_STORAGE_KEY, value)
  }

  return { aiMode, setAiMode }
})
