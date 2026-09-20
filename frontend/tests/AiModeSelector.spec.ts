import { createPinia } from 'pinia'
import { mount } from '@vue/test-utils'
import { nextTick } from 'vue'
import { beforeEach, describe, expect, it } from 'vitest'

import AiModeSelector from '@/components/AiModeSelector.vue'
import { usePreferencesStore } from '@/stores/preferences'

describe('AiModeSelector', () => {
  beforeEach(() => {
    localStorage.clear()
  })

  it('defaults to balanced mode and persists a selected mode', async () => {
    const pinia = createPinia()
    const wrapper = mount(AiModeSelector, { global: { plugins: [pinia] } })
    const store = usePreferencesStore(pinia)

    expect(store.aiMode).toBe('balanced')
    expect(wrapper.get('.ai-mode-trigger-label').text()).toBe('均衡')

    await wrapper.get('.ai-mode-trigger').trigger('click')
    await nextTick()
    const qualityOption = document.body.querySelector<HTMLElement>(
      '[data-mode="quality"]',
    )
    if (qualityOption) {
      qualityOption.click()
    } else {
      const options = [...document.body.querySelectorAll<HTMLElement>('.ai-mode-option')]
      options[2]?.click()
    }
    await nextTick()

    expect(store.aiMode).toBe('quality')
    expect(localStorage.getItem('labor-rights-qa.ai-mode')).toBe('quality')
    wrapper.unmount()
  })
})
