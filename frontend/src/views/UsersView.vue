<!-- 文件作用：仅供系统管理员创建普通账号和知识管理员，并管理其角色与启用状态。 -->
<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import {
  ElAlert,
  ElButton,
  ElCard,
  ElDialog,
  ElEmpty,
  ElForm,
  ElFormItem,
  ElInput,
  ElMessage,
  ElOption,
  ElPopconfirm,
  ElSelect,
  ElTable,
  ElTableColumn,
  ElTag,
  type FormInstance,
  type FormRules,
} from 'element-plus'

import {
  createManagedUser,
  listManagedUsers,
  revokeManagedUserSessions,
  updateManagedUser,
  type ManagedUser,
  type UserRole,
} from '@/api/auth'
import { getErrorMessage } from '@/api/http'

type ManagedRole = Exclude<UserRole, 'SYSTEM_ADMIN'>

const users = ref<ManagedUser[]>([])
const createDialogVisible = ref(false)
const createFormRef = ref<FormInstance>()
const createForm = reactive<{
  loginIdentifier: string
  password: string
  role: ManagedRole
}>({
  loginIdentifier: '',
  password: '',
  role: 'USER',
})
const createFormRules: FormRules = {
  loginIdentifier: [
    { required: true, whitespace: true, message: '请输入登录账号', trigger: 'blur' },
    { max: 80, message: '登录账号不能超过 80 个字符', trigger: 'blur' },
  ],
  password: [
    { required: true, message: '请设置初始密码', trigger: 'blur' },
    { min: 6, max: 18, message: '密码长度需为 6–18 位', trigger: 'blur' },
  ],
  role: [{ required: true, message: '请选择账号角色', trigger: 'change' }],
}
const loading = ref(false)
const saving = ref(false)
const actionUserId = ref('')
const pageError = ref('')

onMounted(() => void loadUsers())

async function loadUsers() {
  loading.value = true
  pageError.value = ''
  try {
    users.value = await listManagedUsers()
  } catch (error: unknown) {
    pageError.value = getErrorMessage(error)
  } finally {
    loading.value = false
  }
}

function openCreateDialog() {
  pageError.value = ''
  createDialogVisible.value = true
}

function closeCreateDialog(done: () => void) {
  if (!saving.value) done()
}

function resetCreateForm() {
  createForm.loginIdentifier = ''
  createForm.password = ''
  createForm.role = 'USER'
  createFormRef.value?.clearValidate()
}

async function submitUser() {
  if (saving.value || !createFormRef.value) return
  try {
    await createFormRef.value.validate()
  } catch {
    return
  }

  saving.value = true
  pageError.value = ''
  try {
    await createManagedUser({
      login_identifier: createForm.loginIdentifier.trim(),
      password: createForm.password,
      role: createForm.role,
    })
    createDialogVisible.value = false
    ElMessage.success('账号已创建')
    await loadUsers()
  } catch (error: unknown) {
    pageError.value = getErrorMessage(error)
  } finally {
    saving.value = false
  }
}

async function updateAccount(user: ManagedUser, changes: {
  role: ManagedRole
  status: ManagedUser['status']
}) {
  if (user.role === 'SYSTEM_ADMIN') return
  actionUserId.value = user.id
  pageError.value = ''
  try {
    await updateManagedUser(user.id, changes)
    await loadUsers()
    ElMessage.success('账号权限已更新，已有登录会话已撤销')
  } catch (error: unknown) {
    pageError.value = getErrorMessage(error)
  } finally {
    actionUserId.value = ''
  }
}

function changeRole(user: ManagedUser, selected: unknown) {
  if (user.role === 'SYSTEM_ADMIN') return
  if (selected !== 'USER' && selected !== 'KNOWLEDGE_ADMIN') return
  void updateAccount(user, { role: selected, status: user.status })
}

function toggleStatus(user: ManagedUser) {
  if (user.role === 'SYSTEM_ADMIN') return
  void updateAccount(user, {
    role: user.role,
    status: user.status === 'ACTIVE' ? 'DISABLED' : 'ACTIVE',
  })
}

async function revokeSessions(user: ManagedUser) {
  actionUserId.value = user.id
  pageError.value = ''
  try {
    await revokeManagedUserSessions(user.id)
    ElMessage.success('该账号的登录会话已撤销')
  } catch (error: unknown) {
    pageError.value = getErrorMessage(error)
  } finally {
    actionUserId.value = ''
  }
}

function roleLabel(value: UserRole) {
  return {
    USER: '普通用户',
    KNOWLEDGE_ADMIN: '知识管理员',
    SYSTEM_ADMIN: '系统管理员',
  }[value]
}

function managedUser(value: unknown): ManagedUser {
  return value as ManagedUser
}

function formatCreatedAt(value: string) {
  return new Intl.DateTimeFormat('zh-CN', {
    dateStyle: 'medium',
    timeStyle: 'short',
  }).format(new Date(value))
}
</script>

<template>
  <div class="users-page">
    <section class="page-intro users-page-intro">
      <div>
        <h1>账号管理</h1>
        <p>为不同身份分配相应功能权限；停用账号或调整角色会立即撤销已有登录会话。</p>
      </div>
      <ElButton type="primary" @click="openCreateDialog">创建账号</ElButton>
    </section>

    <ElAlert
      v-if="pageError"
      class="users-page__error"
      :title="pageError"
      type="error"
      show-icon
      closable
      @close="pageError = ''"
    />

    <ElCard class="users-table-card" shadow="never">
      <template #header>
        <div class="users-table-card__heading">
          <div class="users-table-card__title">
            <h2>账号列表</h2>
            <ElTag type="info" effect="plain" round>{{ users.length }} 个账号</ElTag>
          </div>
          <ElButton text :loading="loading" @click="loadUsers">刷新</ElButton>
        </div>
      </template>

      <ElTable
        v-loading="loading"
        :data="users"
        row-key="id"
        class="users-table"
        aria-label="账号列表"
      >
        <ElTableColumn prop="login_identifier" label="登录账号" min-width="180" show-overflow-tooltip />
        <ElTableColumn label="角色" min-width="180">
          <template #default="scope">
            <ElTag v-if="managedUser(scope.row).role === 'SYSTEM_ADMIN'" type="success" effect="light">
              {{ roleLabel(managedUser(scope.row).role) }}
            </ElTag>
            <ElSelect
              v-else
              :model-value="managedUser(scope.row).role"
              :disabled="actionUserId === managedUser(scope.row).id"
              :aria-label="`${managedUser(scope.row).login_identifier} 的角色`"
              @update:model-value="changeRole(managedUser(scope.row), $event)"
            >
              <ElOption label="普通用户" value="USER" />
              <ElOption label="知识管理员" value="KNOWLEDGE_ADMIN" />
            </ElSelect>
          </template>
        </ElTableColumn>
        <ElTableColumn label="状态" width="112">
          <template #default="scope">
            <ElTag :type="managedUser(scope.row).status === 'ACTIVE' ? 'success' : 'info'" effect="light" round>
              {{ managedUser(scope.row).status === 'ACTIVE' ? '启用' : '已停用' }}
            </ElTag>
          </template>
        </ElTableColumn>
        <ElTableColumn label="创建时间" min-width="180">
          <template #default="scope">
            {{ formatCreatedAt(managedUser(scope.row).created_at) }}
          </template>
        </ElTableColumn>
        <ElTableColumn label="操作" width="226" fixed="right">
          <template #default="scope">
            <div v-if="managedUser(scope.row).role !== 'SYSTEM_ADMIN'" class="users-table__actions">
              <ElPopconfirm
                v-if="managedUser(scope.row).status === 'ACTIVE'"
                title="停用后，该账号的所有登录会话将立即失效。"
                @confirm="toggleStatus(managedUser(scope.row))"
              >
                <template #reference>
                  <ElButton text type="danger" :disabled="actionUserId === managedUser(scope.row).id">停用</ElButton>
                </template>
                <template #actions="{ confirm }">
                  <ElButton size="small" type="danger" @click="confirm">确认停用</ElButton>
                </template>
              </ElPopconfirm>
              <ElButton
                v-else
                text
                type="primary"
                :disabled="actionUserId === managedUser(scope.row).id"
                @click="toggleStatus(managedUser(scope.row))"
              >启用</ElButton>

              <ElPopconfirm
                title="撤销后，该账号在所有设备上的登录会话都需要重新登录。"
                @confirm="revokeSessions(managedUser(scope.row))"
              >
                <template #reference>
                  <ElButton
                    text
                    :disabled="actionUserId === managedUser(scope.row).id || managedUser(scope.row).status === 'DISABLED'"
                  >撤销会话</ElButton>
                </template>
                <template #actions="{ confirm }">
                  <ElButton size="small" type="danger" @click="confirm">撤销会话</ElButton>
                </template>
              </ElPopconfirm>
            </div>
            <span v-else class="users-table__protected">系统管理员账号</span>
          </template>
        </ElTableColumn>
        <template #empty>
          <ElEmpty description="暂无账号" :image-size="72" />
        </template>
      </ElTable>
    </ElCard>

    <ElDialog
      v-model="createDialogVisible"
      class="users-create-dialog"
      title="创建账号"
      width="min(520px, calc(100vw - 32px))"
      :close-on-click-modal="false"
      :close-on-press-escape="!saving"
      :before-close="closeCreateDialog"
      destroy-on-close
      @closed="resetCreateForm"
    >
      <p class="users-create-dialog__intro">新建登录账号，并设置初始角色与登录凭证。</p>
      <ElForm
        ref="createFormRef"
        :model="createForm"
        :rules="createFormRules"
        label-position="top"
        class="users-create-form"
        @submit.prevent="submitUser"
      >
        <ElFormItem label="登录账号" prop="loginIdentifier">
          <ElInput
            v-model="createForm.loginIdentifier"
            autocomplete="off"
            maxlength="80"
            placeholder="请输入登录账号"
          />
        </ElFormItem>
        <ElFormItem label="初始密码" prop="password">
          <ElInput
            v-model="createForm.password"
            type="password"
            autocomplete="new-password"
            maxlength="18"
            show-password
            placeholder="设置 6–18 位密码"
          />
        </ElFormItem>
        <ElFormItem label="账号角色" prop="role">
          <ElSelect v-model="createForm.role" class="users-create-form__role">
            <ElOption label="普通用户" value="USER" />
            <ElOption label="知识管理员" value="KNOWLEDGE_ADMIN" />
          </ElSelect>
        </ElFormItem>
        <div class="users-create-dialog__footer">
          <ElButton :disabled="saving" @click="createDialogVisible = false">取消</ElButton>
          <ElButton type="primary" native-type="submit" :loading="saving">创建账号</ElButton>
        </div>
      </ElForm>
    </ElDialog>
  </div>
</template>

<style scoped>
.users-page {
  display: grid;
  width: min(100%, 1100px);
  margin: 0 auto;
  gap: 18px;
}

.users-page-intro {
  align-items: flex-end;
  padding: 2px 2px 4px;
}

.users-page-intro h1 {
  margin: 0;
  color: #155a42;
  font-family: "Songti SC", "STSong", "Noto Serif CJK SC", serif;
  font-size: clamp(30px, 2.8vw, 38px);
  font-weight: 700;
  letter-spacing: -0.06em;
  line-height: 1.16;
}

.users-page-intro p {
  margin: 8px 0 0;
  color: #7b8798;
  font-size: 14px;
  line-height: 1.5;
}

.users-page-intro > :deep(.el-button) {
  min-height: 40px;
  padding-inline: 18px;
  border-radius: 9px;
}

.users-page__error {
  border-radius: 10px;
}

.users-table-card {
  overflow: hidden;
  border-color: var(--el-border-color-lighter);
  border-radius: 14px;
}

.users-table-card :deep(.el-card__header) {
  padding: 17px 20px;
  border-bottom-color: var(--el-border-color-lighter);
}

.users-table-card :deep(.el-card__body) {
  padding: 0 0 2px;
}

.users-table-card__heading,
.users-table-card__title {
  display: flex;
  align-items: center;
}

.users-table-card__heading {
  justify-content: space-between;
  gap: 16px;
}

.users-table-card__title { gap: 10px; }
.users-table-card__title h2 { margin: 0; color: #263548; font-size: 16px; font-weight: 650; }

.users-table {
  width: 100%;
  color: #344257;
}

.users-table :deep(.el-table__header th) {
  height: 48px;
  color: #728096;
  background: #f8faf9;
  font-size: 12px;
  font-weight: 600;
}

.users-table :deep(.el-table__cell) { padding-block: 11px; }
.users-table :deep(.el-table__inner-wrapper::before) { display: none; }
.users-table :deep(.el-select) { width: 100%; max-width: 176px; }

.users-table__actions {
  display: flex;
  align-items: center;
  gap: 4px;
}

.users-table__protected {
  color: #6d7d72;
  font-size: 12px;
}

.users-create-dialog__intro {
  margin: -4px 0 22px;
  color: var(--el-text-color-secondary);
  font-size: 13px;
  line-height: 1.6;
}

.users-create-form :deep(.el-form-item) { margin-bottom: 19px; }
.users-create-form :deep(.el-form-item__label) { padding-bottom: 7px; color: #3d4b5e; font-weight: 550; }
.users-create-form__role { width: 100%; }

.users-create-dialog__footer {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
  padding-top: 7px;
}

.users-create-dialog__footer :deep(.el-button) {
  min-width: 92px;
  min-height: 38px;
  border-radius: 8px;
}

@media (max-width: 640px) {
  .users-page { gap: 14px; }
  .users-page-intro { align-items: flex-start; }
  .users-page-intro p { max-width: 46ch; font-size: 13px; }
  .users-page-intro > :deep(.el-button) { flex: 0 0 auto; padding-inline: 13px; }
  .users-table-card :deep(.el-card__header) { padding: 15px 14px; }
  .users-table-card__title h2 { font-size: 15px; }
  .users-table :deep(.el-table__cell) { padding-block: 9px; }
}
</style>
