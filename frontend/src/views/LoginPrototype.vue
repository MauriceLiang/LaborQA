<script setup lang="ts">
import { computed, ref } from 'vue'
import { ElButton, ElInput } from 'element-plus'
import { Hide, View } from '@element-plus/icons-vue'

const props = withDefaults(defineProps<{
  previewMode?: boolean
  loading?: boolean
  errorText?: string
}>(), {
  previewMode: false,
  loading: false,
  errorText: '',
})
const emit = defineEmits<{
  submit: [payload: { account: string; password: string }]
}>()

const account = ref('')
const password = ref('')
const passwordVisible = ref(false)
const previewState = ref('normal')

const isLoading = computed(() => props.previewMode
  ? previewState.value === 'loading'
  : props.loading)
const isError = computed(() => props.previewMode
  ? previewState.value === 'error'
  : Boolean(props.errorText))
const shownErrorText = computed(() => props.previewMode
  ? '账号或密码错误，请重试。'
  : props.errorText)
const viewState = computed(() => props.previewMode
  ? previewState.value
  : isLoading.value ? 'loading' : isError.value ? 'error' : 'normal')

function handleSubmit() {
  if (isLoading.value) return
  if (props.previewMode) {
    previewState.value = 'error'
    return
  }
  emit('submit', { account: account.value.trim(), password: password.value })
}
</script>

<template>
  <div class="login-preview" :data-state="viewState">
    <div v-if="previewMode" class="login-preview__toolbar" aria-label="原型状态预览">
      <span class="login-preview__toolbar-label">登录页状态</span>
      <select
        v-model="previewState"
        class="login-preview__state-picker"
        aria-label="切换登录页预览状态"
      >
        <option value="normal">默认</option>
        <option value="error">验证失败</option>
        <option value="loading">提交中</option>
      </select>
      <span class="login-preview__toolbar-note">视觉示例，不连接认证服务</span>
    </div>

    <main class="login-preview__layout">
      <section class="login-preview__story" aria-labelledby="product-title">
        <div class="login-preview__story-brand">
          <span class="login-preview__story-mark-well">
            <img src="/assets/brand-mark.png" alt="" />
          </span>
        </div>

        <div class="login-preview__story-copy">
          <h1 id="product-title">劳动权益<br />咨询问答台</h1>
          <p>劳动权益信息辅助工具</p>
        </div>

        <p class="login-preview__story-note">
          不替代律师、仲裁机构或行政机关提供的正式法律意见。
        </p>
      </section>

      <section class="login-preview__panel" aria-labelledby="login-title">
        <div class="login-preview__body">
          <div class="login-preview__intro">
            <h2 id="login-title">登录工作台</h2>
            <p>使用管理员创建的账号登录。</p>
          </div>

          <form class="login-preview__form" novalidate @submit.prevent="handleSubmit">
            <div class="login-preview__field">
              <label for="login-account">账号</label>
              <ElInput
                id="login-account"
                v-model="account"
                name="account"
                type="text"
                autocomplete="username"
                placeholder="请输入账号"
                size="large"
                :disabled="isLoading"
              />
            </div>

            <div class="login-preview__field">
              <label for="login-password">密码</label>
              <ElInput
                id="login-password"
                v-model="password"
                name="password"
                :type="passwordVisible ? 'text' : 'password'"
                autocomplete="current-password"
                placeholder="请输入密码"
                size="large"
                :disabled="isLoading"
              >
                <template #suffix>
                  <button
                    class="login-preview__password-toggle"
                    type="button"
                    :aria-label="passwordVisible ? '隐藏密码' : '显示密码'"
                    :disabled="isLoading"
                    @click="passwordVisible = !passwordVisible"
                  >
                    <View v-if="!passwordVisible" aria-hidden="true" />
                    <Hide v-else aria-hidden="true" />
                  </button>
                </template>
              </ElInput>
            </div>

            <p v-if="isError" class="login-preview__error" role="alert" aria-live="polite">
              {{ shownErrorText }}
            </p>

            <ElButton
              class="login-preview__submit"
              type="primary"
              size="large"
              native-type="submit"
              :loading="isLoading"
              :aria-busy="isLoading"
            >
              {{ isLoading ? '正在登录' : '登录' }}
            </ElButton>
          </form>

          <p class="login-preview__account-note">
            账号由系统管理员创建，当前不开放自助注册。
          </p>
        </div>
      </section>
    </main>
  </div>
</template>

<style scoped>
:global(body.login-preview-body) {
  min-width: 320px;
  min-height: 100vh;
  overflow: auto;
  background: var(--el-color-primary-light-9);
}

:global(body.login-preview-body #app) {
  min-height: 100vh;
}

.login-preview {
  position: relative;
  isolation: isolate;
  display: grid;
  min-height: 100vh;
  min-height: 100dvh;
  padding: 76px 32px 36px;
  place-items: center;
  background:
    radial-gradient(ellipse at 12% 8%, rgb(255 255 255 / 76%), transparent 38%),
    radial-gradient(ellipse at 92% 94%, rgb(204 228 213 / 52%), transparent 40%),
    var(--el-color-primary-light-9);
}

.login-preview__toolbar {
  position: fixed;
  z-index: 2;
  top: 18px;
  left: 50%;
  display: flex;
  align-items: center;
  gap: 12px;
  max-width: calc(100vw - 24px);
  padding: 7px 10px 7px 14px;
  color: var(--el-text-color-regular);
  background: rgb(255 255 255 / 94%);
  border: 1px solid var(--el-border-color-light);
  border-radius: 999px;
  box-shadow: 0 4px 14px rgb(31 68 54 / 6%);
  transform: translateX(-50%);
  backdrop-filter: blur(12px);
  white-space: nowrap;
}

.login-preview__toolbar-label {
  font-size: 12px;
  font-weight: 600;
}

.login-preview__state-picker {
  min-height: 34px;
  padding: 0 11px;
  color: var(--el-text-color-primary);
  background: var(--el-bg-color);
  border: 1px solid var(--el-border-color);
  border-radius: 8px;
  font: inherit;
  font-size: 12px;
  cursor: pointer;
}

.login-preview__toolbar-note {
  color: var(--el-text-color-secondary);
  font-size: 11px;
}

.login-preview__layout {
  display: grid;
  width: min(100%, 1120px);
  min-height: min(660px, calc(100dvh - 136px));
  grid-template-columns: minmax(210px, 1.05fr) minmax(320px, 0.95fr);
  overflow: hidden;
  background: var(--el-bg-color);
  border: 1px solid rgb(255 255 255 / 88%);
  border-radius: 28px;
  box-shadow:
    0 28px 72px rgb(31 68 54 / 12%),
    0 8px 24px rgb(31 68 54 / 7%),
    0 1px 2px rgb(31 68 54 / 5%);
}

.login-preview__story {
  position: relative;
  isolation: isolate;
  display: flex;
  min-width: 0;
  min-height: 620px;
  flex-direction: column;
  overflow: hidden;
  padding: 38px 32px 32px;
  background:
    radial-gradient(ellipse at 72% 54%, rgb(204 228 213 / 58%), transparent 52%),
    linear-gradient(148deg, #f8fbf8 0%, var(--el-color-primary-light-9) 54%, #e8f3ec 100%);
}

.login-preview__story::before {
  position: absolute;
  z-index: -1;
  right: -64px;
  bottom: -58px;
  width: 360px;
  height: 360px;
  background: url('/assets/brand-mark.png') center / contain no-repeat;
  content: '';
  opacity: 0.055;
  transform: rotate(-12deg);
}

.login-preview__story-brand {
  display: flex;
  align-items: center;
  gap: 14px;
}

.login-preview__story-mark-well {
  display: grid;
  width: 52px;
  height: 52px;
  flex: 0 0 auto;
  place-items: center;
  background: rgb(255 255 255 / 90%);
  border: 1px solid rgb(18 96 71 / 8%);
  border-radius: 16px;
  box-shadow: 0 5px 14px rgb(31 68 54 / 8%), inset 0 1px 0 #fff;
}

.login-preview__story-mark-well img {
  display: block;
  width: 36px;
  height: 36px;
  object-fit: contain;
}

.login-preview__story-copy {
  margin: auto 0;
  padding: 48px 0;
}

.login-preview__story-copy h1 {
  margin: 0;
  color: var(--el-text-color-primary);
  font-size: clamp(30px, 3.5vw, 42px);
  font-weight: 700;
  letter-spacing: -0.04em;
  line-height: 1.28;
}

.login-preview__story-copy p {
  margin: 18px 0 0;
  color: var(--el-text-color-regular);
  font-size: 15px;
  font-weight: 500;
  line-height: 1.7;
}

.login-preview__story-note {
  margin: 0;
  padding-top: 18px;
  color: var(--el-text-color-secondary);
  border-top: 1px solid rgb(18 96 71 / 12%);
  font-size: 12px;
  line-height: 1.7;
}

.login-preview__panel {
  display: grid;
  min-width: 0;
  place-items: center;
  padding: 44px clamp(28px, 4vw, 56px);
  background: var(--el-bg-color);
  border-left: 1px solid var(--el-border-color-extra-light);
}

.login-preview__body {
  width: min(100%, 370px);
}

.login-preview__intro { margin: 0; }

.login-preview__intro h2 {
  margin: 0;
  color: var(--el-text-color-primary);
  font-size: 26px;
  font-weight: 700;
  letter-spacing: -0.025em;
  line-height: 1.3;
}

.login-preview__intro p {
  margin: 8px 0 0;
  color: var(--el-text-color-regular);
  font-size: 14px;
  line-height: 1.65;
}

.login-preview__form {
  display: grid;
  gap: 18px;
  margin-top: 27px;
}

.login-preview__field {
  display: grid;
  gap: 8px;
  min-width: 0;
}

.login-preview__field label {
  color: var(--el-text-color-primary);
  font-size: 14px;
  font-weight: 600;
  line-height: 1.4;
}

.login-preview__field :deep(.el-input__wrapper) {
  min-height: 48px;
  padding: 1px 13px;
  border-radius: 8px;
  box-shadow: 0 0 0 1px var(--el-border-color) inset;
  transition: box-shadow 140ms ease, background-color 140ms ease;
}

.login-preview__field :deep(.el-input__wrapper.is-focus) {
  box-shadow: 0 0 0 1px var(--el-color-primary) inset, 0 0 0 3px rgb(18 96 71 / 12%);
}

.login-preview__field :deep(.el-input__inner) {
  color: var(--el-text-color-primary);
  font-size: 14px;
}

.login-preview__field :deep(.el-input__inner::placeholder) {
  color: var(--el-text-color-secondary);
  opacity: 1;
}

.login-preview__password-toggle {
  display: inline-grid;
  width: 30px;
  height: 30px;
  padding: 0;
  place-items: center;
  color: var(--el-text-color-secondary);
  background: transparent;
  border: 0;
  border-radius: 6px;
  cursor: pointer;
  transition: color 140ms ease, background-color 140ms ease;
}

.login-preview__password-toggle:hover:not(:disabled) {
  color: var(--el-color-primary);
  background: var(--el-color-primary-light-9);
}

.login-preview__password-toggle:focus-visible,
.login-preview__state-picker:focus-visible {
  outline: 3px solid rgb(18 96 71 / 25%);
  outline-offset: 2px;
}

.login-preview__password-toggle:disabled { cursor: not-allowed; }

.login-preview__password-toggle svg {
  width: 17px;
  height: 17px;
}

.login-preview__error {
  margin: -8px 0 0;
  padding: 10px 12px;
  color: var(--el-color-error-dark-2);
  background: var(--el-color-error-light-9);
  border: 1px solid var(--el-color-error-light-7);
  border-radius: 7px;
  font-size: 13px;
  line-height: 1.5;
}

.login-preview__submit {
  width: 100%;
  min-height: 48px;
  margin-top: 3px;
  border-radius: 8px;
  font-size: 15px;
  font-weight: 650;
  transition: transform 140ms ease, box-shadow 140ms ease;
}

.login-preview__submit:active:not(.is-disabled) {
  transform: translateY(1px) scale(0.995);
}

.login-preview__account-note {
  margin: 19px 0 0;
  color: var(--el-text-color-secondary);
  font-size: 12px;
  line-height: 1.65;
  text-align: center;
}

@media (max-width: 600px) {
  .login-preview { padding: 68px 16px 24px; }
  .login-preview__toolbar { top: 12px; gap: 8px; padding: 6px 8px 6px 11px; }
  .login-preview__toolbar-note { display: none; }
  .login-preview__layout {
    width: min(100%, 480px);
    min-height: 0;
    grid-template-columns: minmax(0, 1fr);
    border-radius: 23px;
  }
  .login-preview__story { min-height: 0; padding: 24px 28px 20px; }
  .login-preview__story::before { right: -48px; bottom: -112px; width: 240px; height: 240px; }
  .login-preview__story-mark-well { width: 48px; height: 48px; border-radius: 15px; }
  .login-preview__story-mark-well img { width: 33px; height: 33px; }
  .login-preview__story-copy { margin: 22px 0 0; padding: 0; }
  .login-preview__story-copy h1 { font-size: 28px; }
  .login-preview__story-copy p { margin-top: 10px; font-size: 13px; }
  .login-preview__story-note { margin-top: 20px; padding-top: 13px; font-size: 11px; }
  .login-preview__panel { padding: 30px 28px 28px; border-top: 1px solid var(--el-border-color-extra-light); border-left: 0; }
  .login-preview__body { width: min(100%, 380px); }
  .login-preview__intro h2 { font-size: 24px; }
}

@media (max-width: 360px) {
  .login-preview { padding-inline: 12px; }
  .login-preview__toolbar-label { display: none; }
  .login-preview__story { padding-inline: 20px; }
  .login-preview__panel { padding-inline: 20px; }
}

@media (prefers-reduced-motion: reduce) {
  .login-preview__field :deep(.el-input__wrapper),
  .login-preview__password-toggle,
  .login-preview__submit {
    transition-duration: 0.01ms !important;
  }
}
</style>
