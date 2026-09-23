<!-- 文件作用：选择并上传劳动法律文档，展示上传和导入反馈。 -->
<script setup lang="ts">
import { computed, ref } from 'vue'
import { ElButton } from 'element-plus'
import { Document, FolderOpened } from '@element-plus/icons-vue'

import { getDocument, uploadDocument, type DocumentItem, type DocumentUploadAccepted } from '@/api/documents'
import { getErrorMessage } from '@/api/http'

// 前端允许选择的文档扩展名白名单。
const acceptedExtensions = ['pdf', 'doc', 'docx', 'md', 'txt']
// 单个文件的前端校验上限，单位为字节。
const maxFileSize = 20 * 1024 * 1024
// 上传受理后轮询文档导入状态的间隔，单位为毫秒。
const importPollIntervalMs = 2000

// 等待按顺序上传和处理的用户所选文件。
const selectedFiles = ref<File[]>([])
// 标记上传队列是否正在执行。
const uploading = ref(false)
// 当前正在处理的文件序号，用于显示进度。
const uploadIndex = ref(0)
// 当前上传队列中的文件总数。
const uploadTotal = ref(0)
// 上传或文档处理失败时展示的错误文本。
const errorMessage = ref('')
// 隐藏的文件选择控件，用于通过按钮打开系统文件选择器。
const fileInput = ref<HTMLInputElement>()
// 将所选文件名合并为界面展示文本。
const selectedFileNames = computed(() => selectedFiles.value.map((file) => file.name).join('、'))
// 文件被后端受理后向父组件通知对应的文档记录。
const emit = defineEmits<{ uploaded: [document: DocumentUploadAccepted] }>()

/** 打开浏览器原生文件选择对话框。 */
function chooseFile() {
  fileInput.value?.click()
}

/** 在发起上传前校验文件是否为空、超限或格式不支持。
 * @param file 用户选择的文件。
 * @returns 校验失败提示；通过时为空字符串。
 */
function validateFile(file: File): string {
  if (file.size === 0) return '文件内容为空，请选择其他文件。'
  if (file.size > maxFileSize) return '文件不能超过 20 MB。'
  // 从文件名末尾提取小写扩展名。
  const extension = file.name.includes('.') ? file.name.split('.').pop()?.toLowerCase() : undefined
  if (!extension || !acceptedExtensions.includes(extension)) return '仅支持 PDF、DOC、DOCX、MD 或 TXT 文件。'
  return ''
}

/** 轮询文档详情，直到后台导入离开处理中状态。
 * @param documentId 已受理文档的主键。
 * @returns 导入成功或失败后的最终文档记录。
 */
async function waitForImport(documentId: number): Promise<DocumentItem> {
  // document 保存最近一次轮询得到的导入状态。
  let document = await getDocument(documentId)
  while (document.status === 'PROCESSING') {
    await new Promise((resolve) => setTimeout(resolve, importPollIntervalMs))
    document = await getDocument(documentId)
  }
  return document
}

/** 读取文件选择结果、校验全部文件并更新待上传列表。
 * @param event 文件输入控件触发的 change 事件。
 */
function onFileChange(event: Event) {
  // input 是触发 change 事件的文件选择控件。
  const input = event.target as HTMLInputElement
  // files 是当前选择器中的全部文件副本。
  const files = Array.from(input.files ?? [])
  input.value = ''
  errorMessage.value = ''
  selectedFiles.value = []

  // file 表示待检查的单个选择文件。
  for (const file of files) {
    // validationMessage 为空表示文件通过全部前端规则。
    const validationMessage = validateFile(file)
    if (validationMessage) {
      errorMessage.value = file.name + '：' + validationMessage
      return
    }
  }

  selectedFiles.value = files
}

/** 按顺序上传已校验文件，并等待每份文档完成后台导入。 */
async function submitUpload() {
  if (selectedFiles.value.length === 0 || uploading.value) return
  // files 固定本次提交的上传队列，即使界面状态随后更新也保持一致。
  const files = selectedFiles.value
  uploading.value = true
  uploadIndex.value = 0
  uploadTotal.value = files.length
  errorMessage.value = ''

  // 记录上传成功但后端解析或索引失败的文件名。
  const failedImports: string[] = []
  try {
    // index 是零起始队列位置，file 是当前正在上传的文件。
    for (const [index, file] of files.entries()) {
      uploadIndex.value = index + 1
      // uploaded 是后端接受上传后返回的文档标识和初始状态。
      const uploaded = await uploadDocument(file)
      emit('uploaded', uploaded)
      // completed 是轮询结束后的最终导入状态。
      const completed = await waitForImport(uploaded.document_id)
      if (completed.status === 'FAILED') {
        failedImports.push(file.name + '：' + (completed.error_message || '资料处理失败'))
      }
    }
    selectedFiles.value = []
    if (failedImports.length > 0) {
      errorMessage.value = '以下文件处理失败：' + failedImports.join('；')
    }
  } catch (error) {
    // error 保存上传或等待导入过程中抛出的异常。
    // failedIndex 是失败文件在队列中的零起始位置。
    const failedIndex = Math.max(uploadIndex.value - 1, 0)
    selectedFiles.value = files.slice(failedIndex)
    errorMessage.value = '第 ' + uploadIndex.value + ' 个文件“' + files[failedIndex].name + '”上传失败：' + getErrorMessage(error)
  } finally {
    uploading.value = false
    uploadIndex.value = 0
    uploadTotal.value = 0
  }
}
</script>

<template>
  <section class="document-upload" aria-labelledby="upload-title">
    <div class="document-upload-copy">
      <h2 id="upload-title">导入资料</h2>
      <p>支持一次选择多个 PDF、DOC、DOCX、MD、TXT 文件，系统按顺序逐个解析、切块并向量化；单个文件不超过 20 MB。</p>
    </div>
    <div class="upload-controls">
      <input
        ref="fileInput"
        class="file-input"
        type="file"
        accept=".pdf,.doc,.docx,.md,.txt"
        multiple
        aria-label="选择要导入的资料（可多选）"
        :disabled="uploading"
        @change="onFileChange"
      />
      <ElButton class="file-picker-button" :disabled="uploading" @click="chooseFile">
        <FolderOpened aria-hidden="true" />
        <span>选择文件（可多选）</span>
      </ElButton>
      <ElButton class="upload-submit-button" type="primary" :disabled="selectedFiles.length === 0 || uploading" :loading="uploading" @click="submitUpload">
        <Document aria-hidden="true" />
        上传资料
      </ElButton>
    </div>
    <p v-if="selectedFiles.length > 0" class="upload-file-name">已选择 {{ selectedFiles.length }} 个文件：{{ selectedFileNames }}</p>
    <p v-if="uploading" class="upload-progress" role="status">正在处理第 {{ uploadIndex }} / {{ uploadTotal }} 个文件…</p>
    <p v-if="errorMessage" class="inline-error" role="alert">{{ errorMessage }}</p>
  </section>
</template>
