import { defineStore } from 'pinia'
import { ref } from 'vue'

import {
  DEFAULT_AI_MODE,
  isAiMode,
  type AiMode,
} from '@/types/preferences'

const AI_MODE_STORAGE_KEY = 'labor-rights-qa.ai-mode'

export const usePreferencesStore = defineStore('preferences', () => {
  const storedMode = localStorage.getItem(AI_MODE_STORAGE_KEY)
  const aiMode = ref<AiMode>(isAiMode(storedMode) ? storedMode : DEFAULT_AI_MODE)

  function setAiMode(value: AiMode) {
    aiMode.value = value
    localStorage.setItem(AI_MODE_STORAGE_KEY, value)
  }

  return { aiMode, setAiMode }
})
