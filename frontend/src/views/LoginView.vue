<!-- 文件作用：用已完成的左右分栏登录页接入服务端账号认证。 -->
<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { getErrorMessage } from '@/api/http'
import { useAuthStore } from '@/stores/auth'
import LoginPrototype from '@/views/LoginPrototype.vue'

const authStore = useAuthStore()
const route = useRoute()
const router = useRouter()
const isSubmitting = ref(false)
const errorText = ref('')

onMounted(() => document.body.classList.add('login-preview-body'))
onUnmounted(() => document.body.classList.remove('login-preview-body'))

async function submitCredentials(payload: { account: string; password: string }) {
  if (isSubmitting.value) return
  if (!payload.account || !payload.password) {
    errorText.value = '请输入账号和密码。'
    return
  }

  isSubmitting.value = true
  errorText.value = ''
  try {
    await authStore.signIn(payload.account, payload.password)
    const redirect = typeof route.query.redirect === 'string'
      ? route.query.redirect
      : '/'
    const safeRedirect = redirect.startsWith('/') && !redirect.startsWith('//')
      ? redirect
      : '/'
    await router.replace(safeRedirect)
  } catch (error: unknown) {
    errorText.value = getErrorMessage(error)
  } finally {
    isSubmitting.value = false
  }
}
</script>

<template>
  <LoginPrototype
    :loading="isSubmitting"
    :error-text="errorText"
    @submit="submitCredentials"
  />
</template>
