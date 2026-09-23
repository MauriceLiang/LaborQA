<!-- 文件作用：组织文档上传、列表浏览、导入状态和文本块查看页面。 -->
<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { ElAlert, ElButton, ElMessage, ElMessageBox } from 'element-plus'
import { Refresh } from '@element-plus/icons-vue'

import ChunkDetail from '@/components/documents/ChunkDetail.vue'
import ChunkList from '@/components/documents/ChunkList.vue'
import DocumentList from '@/components/documents/DocumentList.vue'
import DocumentUpload from '@/components/documents/DocumentUpload.vue'
import {
  deleteDocument,
  getDocument,
  getDocumentChunks,
  getBuiltinDocumentStatus,
  getDocuments,
  importBuiltinDocuments,
  reimportDocument,
  type ChunkItem,
  type DocumentItem,
  type DocumentUploadAccepted,
} from '@/api/documents'
import { ApiError, getErrorMessage } from '@/api/http'

// 文档处理状态轮询的间隔，单位为毫秒。
const POLL_INTERVAL_MS = 2000

// 当前页文档记录列表。
const documents = ref<DocumentItem[]>([])
// 符合筛选条件的文档总数。
const totalDocuments = ref(0)
// 文档列表当前页码。
const documentPage = ref(1)
// 文档列表每页显示数量。
const documentPageSize = ref(10)
// 文档名称关键词筛选值。
const keyword = ref('')
// 文档处理状态筛选值；空字符串表示不过滤状态。
const status = ref<DocumentItem['status'] | ''>('')
// 当前选中文档的唯一标识。
const selectedDocumentId = ref<number>()
// 文档列表请求是否正在执行。
const pageLoading = ref(false)
// 文档列表或状态刷新失败时展示的错误信息。
const pageError = ref('')
// 文档操作或文本分块请求失败时展示的错误信息。
const actionError = ref('')
// 当前正在删除的文档标识，用于防止重复提交。
const deletingDocumentId = ref<number>()
// 内置资料导入及其后续处理是否仍在进行。
const builtinImportActive = ref(false)

// 当前页文本分块记录列表。
const chunks = ref<ChunkItem[]>([])
// 当前文档的文本分块总数。
const totalChunks = ref(0)
// 文本分块列表当前页码。
const chunkPage = ref(1)
// 文本分块列表每页显示数量。
const chunkPageSize = ref(10)
// 文本分块请求是否正在执行。
const chunksLoading = ref(false)
// 当前选中的文本分块。
const selectedChunk = ref<ChunkItem>()
// 文本分块详情面板是否显示。
const chunkDetailVisible = ref(false)

// 文档处理状态轮询定时器句柄。
let pollingTimer: ReturnType<typeof setInterval> | undefined
// 是否已有一轮文档状态轮询尚未结束。
let pollingInProgress = false
// 页面组件是否仍挂载，用于阻止卸载后的状态更新。
let isMounted = false
// 文档列表请求版本号，用于丢弃过期响应。
let documentRequestId = 0
// 分块请求版本号，用于丢弃过期响应。
let chunkRequestId = 0
// 需要持续查询处理状态的文档 ID 集合。
const trackedProcessingIds = new Set<number>()
// 本次内置资料导入所关联的文档 ID 集合。
const builtinTrackedIds = new Set<number>()

// 根据选中文档 ID 从当前列表解析出的文档记录。
const selectedDocument = computed(() =>
  documents.value.find((document) => document.id === selectedDocumentId.value),
)

/** 停止文档处理状态轮询并释放定时器。 */
function stopPolling() {
  if (pollingTimer !== undefined) {
    clearInterval(pollingTimer)
    pollingTimer = undefined
  }
}

/** 根据挂载状态和待处理文档集合启动或停止轮询。 */
function syncPolling() {
  stopPolling()
  if (!isMounted || trackedProcessingIds.size === 0) return
  pollingTimer = setInterval(() => void pollProcessingDocuments(), POLL_INTERVAL_MS)
}

/** 确认内置资料处理已结束，展示汇总结果并刷新文档列表。 */
async function finishBuiltinImportIfReady() {
  if (!isMounted || !builtinImportActive.value || builtinTrackedIds.size > 0) return

  try {
    // 内置资料导入状态查询返回的总体进度和文件明细。
    const status = await getBuiltinDocumentStatus()
    if (status.processing > 0) {
      for (const item of status.items) {
        if (item.state === 'PROCESSING' && item.document_id !== null) {
          builtinTrackedIds.add(item.document_id)
          trackedProcessingIds.add(item.document_id)
        }
      }
      syncPolling()
      return
    }

    builtinImportActive.value = false
    if (status.all_imported) {
      ElMessage.success('系统资料已全部导入完成')
    } else if (status.failed > 0) {
      ElMessage.warning(
        `系统资料处理完成，但有 ${status.failed} 份导入失败，可再次点击“导入系统资料”重试`,
      )
    } else if (status.outdated > 0 || status.missing > 0) {
      ElMessage.warning(
        `仍有 ${status.outdated + status.missing} 份系统资料未完成导入或更新`,
      )
    } else if (status.total === 0) {
      ElMessage.warning('未找到系统资料文件')
    }
    builtinTrackedIds.clear()
    await loadDocuments()
  } catch (error) {
    builtinImportActive.value = false
    builtinTrackedIds.clear()
    actionError.value = `读取系统资料状态失败：${getErrorMessage(error)}`
  }
}

/** 清空当前文档及分块的选择状态，并使未完成的分块请求失效。 */
function clearSelection() {
  chunkRequestId += 1
  selectedDocumentId.value = undefined
  chunks.value = []
  totalChunks.value = 0
  chunksLoading.value = false
  selectedChunk.value = undefined
  chunkDetailVisible.value = false
}

/** 将最新文档信息插入或更新到当前列表中。 */
// 参数 document：当前需要处理的文档记录。
function upsertDocument(document: DocumentItem) {
  // 目标文档在当前列表中的下标。
  const index = documents.value.findIndex((item) => item.id === document.id)
  if (index >= 0) {
    documents.value[index] = document
    return
  }
  documents.value.unshift(document)
  if (documents.value.length > documentPageSize.value) documents.value.pop()
}

/** 首次加载后自动选中可用文档，并加载其文本分块。 */
async function selectInitialDocument() {
  if (selectedDocumentId.value !== undefined || documents.value.length === 0) return
  // 当前正在处理或展示的文档记录。
  const document = documents.value.find((item) => item.status === 'SUCCESS') ?? documents.value[0]
  selectedDocumentId.value = document.id
  chunkPage.value = 1
  if (document.status === 'SUCCESS') await loadChunks()
}

/** 按当前筛选和分页条件读取文档列表，并同步处理状态跟踪。 */
async function loadDocuments() {
  // 本次请求的版本号，用于判断响应是否过期。
  const requestId = ++documentRequestId
  pageLoading.value = true
  pageError.value = ''
  try {
    // 本次后端请求返回的数据。
    const result = await getDocuments({
      page: documentPage.value,
      size: documentPageSize.value,
      keyword: keyword.value || undefined,
      status: status.value || undefined,
    })
    if (!isMounted || requestId !== documentRequestId) return
    for (const document of documents.value) {
      if (document.status === 'PROCESSING') trackedProcessingIds.add(document.id)
    }
    for (const document of result.items) {
      if (document.status === 'PROCESSING') trackedProcessingIds.add(document.id)
      else trackedProcessingIds.delete(document.id)
    }
    documents.value = result.items
    totalDocuments.value = result.total
    if (!documents.value.some((document) => document.id === selectedDocumentId.value)) {
      clearSelection()
    }
    syncPolling()
    await selectInitialDocument()
  } catch (error) {
    if (requestId === documentRequestId) pageError.value = getErrorMessage(error)
  } finally {
    if (requestId === documentRequestId) pageLoading.value = false
  }
}

/** 读取当前选中文档的指定页文本分块。 */
async function loadChunks() {
  // 本次请求的版本号，用于判断响应是否过期。
  const requestId = ++chunkRequestId
  // 当前正在处理或展示的文档记录。
  const document = selectedDocument.value
  if (!document || document.status !== 'SUCCESS') {
    chunks.value = []
    totalChunks.value = 0
    chunksLoading.value = false
    return
  }

  // 发起分块请求时捕获的页码。
  const requestedPage = chunkPage.value
  // 发起分块请求时捕获的每页数量。
  const requestedSize = chunkPageSize.value
  chunksLoading.value = true
  actionError.value = ''
  try {
    // 本次后端请求返回的数据。
    const result = await getDocumentChunks(document.id, requestedPage, requestedSize)
    if (
      !isMounted ||
      requestId !== chunkRequestId ||
      selectedDocumentId.value !== document.id
    ) return
    chunks.value = result.items
    totalChunks.value = result.total
    if (selectedChunk.value && !chunks.value.some((chunk) => chunk.id === selectedChunk.value?.id)) {
      selectedChunk.value = undefined
      chunkDetailVisible.value = false
    }
  } catch (error) {
    if (requestId === chunkRequestId) {
      actionError.value = `加载分块失败：${getErrorMessage(error)}`
    }
  } finally {
    if (requestId === chunkRequestId) chunksLoading.value = false
  }
}

/** 刷新所有处理中资料的状态，并在任务结束后更新关联界面。 */
async function pollProcessingDocuments() {
  if (pollingInProgress) return
  for (const document of documents.value) {
    if (document.status === 'PROCESSING') trackedProcessingIds.add(document.id)
  }
  if (trackedProcessingIds.size === 0) {
    stopPolling()
    return
  }

  pollingInProgress = true
  pageError.value = ''
  // 本轮轮询是否发现文档离开处理中状态。
  let reachedTerminalStatus = false
  try {
    // 并发读取得到的文档状态结果列表。
    const results = await Promise.all(
      [...trackedProcessingIds].map(async (id) => {
        try {
          return await getDocument(id)
        } catch (error) {
          if (error instanceof ApiError && error.code === 40401) {
            trackedProcessingIds.delete(id)
            builtinTrackedIds.delete(id)
            return undefined
          }
          throw error
        }
      }),
    )
    if (!isMounted) return
    for (const updated of results) {
      if (!updated) continue
      // 目标文档在当前列表中的下标。
      const index = documents.value.findIndex((document) => document.id === updated.id)
      if (updated.status === 'PROCESSING') trackedProcessingIds.add(updated.id)
      else {
        trackedProcessingIds.delete(updated.id)
        builtinTrackedIds.delete(updated.id)
        reachedTerminalStatus = true
      }
      if (index < 0) {
        if (selectedDocumentId.value === updated.id) upsertDocument(updated)
        continue
      }
      // 本轮轮询前列表中保存的文档记录。
      const previous = documents.value[index]
      documents.value[index] = updated
      if (previous.status === 'PROCESSING' && updated.status !== 'PROCESSING') {
        if (updated.status === 'SUCCESS' && selectedDocumentId.value === updated.id && isMounted) {
          await loadChunks()
        }
      }
    }
  } catch (error) {
    pageError.value = `刷新导入状态失败：${getErrorMessage(error)}`
  } finally {
    pollingInProgress = false
    syncPolling()
  }

  if (reachedTerminalStatus && isMounted) await loadDocuments()
  if (builtinImportActive.value && builtinTrackedIds.size === 0) {
    await finishBuiltinImportIfReady()
  }
}

/** 触发内置资料导入并跟踪后台处理任务。 */
async function onImportBuiltin() {
  if (builtinImportActive.value) return

  builtinImportActive.value = true
  actionError.value = ''
  try {
    // 本次后端请求返回的数据。
    const result = await importBuiltinDocuments()
    if (!isMounted) return

    builtinTrackedIds.clear()
    for (const id of result.tracked_document_ids) {
      builtinTrackedIds.add(id)
      trackedProcessingIds.add(id)
    }

    if (result.all_imported) {
      builtinImportActive.value = false
      ElMessage.info('系统自带资料已全部导入')
      await loadDocuments()
      return
    }

    await loadDocuments()
    syncPolling()
    if (builtinTrackedIds.size === 0) await finishBuiltinImportIfReady()
  } catch (error) {
    builtinImportActive.value = false
    builtinTrackedIds.clear()
    actionError.value = `导入系统资料失败：${getErrorMessage(error)}`
  }
}

/** 切换当前选中文档；再次点击同一文档时取消选择。 */
// 参数 document：当前需要处理的文档记录。
async function selectDocument(document: DocumentItem) {
  if (selectedDocumentId.value === document.id) {
    clearSelection()
    return
  }

  selectedDocumentId.value = document.id
  chunkPage.value = 1
  chunks.value = []
  totalChunks.value = 0
  selectedChunk.value = undefined
  chunkDetailVisible.value = false
  if (document.status === 'SUCCESS') await loadChunks()
}

/** 接收上传完成事件，选中新资料并开始跟踪解析状态。 */
// 参数 uploaded：上传组件返回的文件受理信息。
async function onUpload(uploaded: DocumentUploadAccepted) {
  keyword.value = ''
  status.value = ''
  documentPage.value = 1
  selectedDocumentId.value = uploaded.document_id
  chunkPage.value = 1
  chunkRequestId += 1
  chunks.value = []
  totalChunks.value = 0
  chunksLoading.value = false
  selectedChunk.value = undefined
  chunkDetailVisible.value = false
  actionError.value = ''
  trackedProcessingIds.add(uploaded.document_id)
  syncPolling()
  try {
    const [document] = await Promise.all([getDocument(uploaded.document_id), loadDocuments()])
    if (!isMounted) return
    if (document.status === 'PROCESSING') trackedProcessingIds.add(document.id)
    else trackedProcessingIds.delete(document.id)
    upsertDocument(document)
    selectedDocumentId.value = document.id
    syncPolling()
    if (document.status === 'SUCCESS') await loadChunks()
  } catch (error) {
    actionError.value = `上传已受理，但无法读取处理状态：${getErrorMessage(error)}`
  }
}

/** 重新提交指定文档的导入任务并刷新其状态。 */
// 参数 document：当前需要处理的文档记录。
async function onReimport(document: DocumentItem) {
  actionError.value = ''
  try {
    await reimportDocument(document.id)
    trackedProcessingIds.add(document.id)
    selectedDocumentId.value = document.id
    chunkPage.value = 1
    chunkRequestId += 1
    chunks.value = []
    totalChunks.value = 0
    chunksLoading.value = false
    selectedChunk.value = undefined
    chunkDetailVisible.value = false
    if (status.value === 'FAILED') {
      status.value = ''
      keyword.value = document.file_name
      documentPage.value = 1
    }
    syncPolling()
    const [updated] = await Promise.all([getDocument(document.id), loadDocuments()])
    if (!isMounted) return
    if (updated.status === 'PROCESSING') trackedProcessingIds.add(updated.id)
    else trackedProcessingIds.delete(updated.id)
    upsertDocument(updated)
    selectedDocumentId.value = updated.id
    syncPolling()
    if (updated.status === 'SUCCESS') await loadChunks()
  } catch (error) {
    actionError.value = `重新导入失败：${getErrorMessage(error)}`
  }
}

/** 确认并删除指定文档，同时维护分页、选择和处理状态。 */
// 参数 document：当前需要处理的文档记录。
async function onDelete(document: DocumentItem) {
  if (deletingDocumentId.value !== undefined) return
  try {
    await ElMessageBox.confirm(
      `将同时删除该资料的文本分块、向量索引和上传文件，删除后不可恢复。`,
      `确定删除“${document.file_name}”吗？`,
      {
        type: 'warning',
        confirmButtonText: '删除',
        cancelButtonText: '取消',
        closeOnClickModal: false,
      },
    )
  } catch {
    return
  }

  deletingDocumentId.value = document.id
  actionError.value = ''
  trackedProcessingIds.delete(document.id)
  syncPolling()
  try {
    await deleteDocument(document.id)
    if (selectedDocumentId.value === document.id) clearSelection()
    await loadDocuments()
  } catch (error) {
    if (document.status === 'PROCESSING') {
      trackedProcessingIds.add(document.id)
      syncPolling()
    }
    actionError.value = `删除资料失败：${getErrorMessage(error)}`
  } finally {
    deletingDocumentId.value = undefined
  }

  if (documents.value.length === 0 && documentPage.value > 1) {
    documentPage.value -= 1
    await loadDocuments()
  }
}

/** 应用文档关键词及状态筛选条件。 */
// 参数 query：筛选表单提交的查询条件。
async function onFilter(query: { keyword: string; status: DocumentItem['status'] | '' }) {
  keyword.value = query.keyword
  status.value = query.status
  documentPage.value = 1
  clearSelection()
  await loadDocuments()
}

/** 切换文档列表页码。 */
// 参数 page：用户选择的目标页码。
async function onDocumentPage(page: number) {
  documentPage.value = page
  clearSelection()
  await loadDocuments()
}

/** 修改文档列表每页数量并回到第一页。 */
// 参数 size：列表每页显示的记录数量。
async function onDocumentSize(size: number) {
  documentPageSize.value = size
  documentPage.value = 1
  clearSelection()
  await loadDocuments()
}

/** 切换文本分块列表页码。 */
// 参数 page：用户选择的目标页码。
async function onChunkPage(page: number) {
  chunkPage.value = page
  await loadChunks()
}

/** 修改文本分块每页数量并回到第一页。 */
// 参数 size：列表每页显示的记录数量。
async function onChunkSize(size: number) {
  chunkPageSize.value = size
  chunkPage.value = 1
  await loadChunks()
}

/** 选中指定文本分块并打开详情面板。 */
// 参数 chunk：当前文本分块记录。
function openChunk(chunk: ChunkItem) {
  selectedChunk.value = chunk
  chunkDetailVisible.value = true
}

onMounted(() => {
  isMounted = true
  void loadDocuments()
})
onUnmounted(() => {
  isMounted = false
  builtinImportActive.value = false
  builtinTrackedIds.clear()
  documentRequestId += 1
  chunkRequestId += 1
  stopPolling()
})
</script>

<template>
  <div class="documents-page">
    <section class="page-intro documents-page-intro">
      <div>
        <h1>资料管理</h1>
        <p>导入法规和政策资料，查看解析状态及文本分块。</p>
      </div>
      <div class="documents-page-actions">
        <ElButton
          class="document-builtin-import-button"
          type="primary"
          :loading="builtinImportActive"
          @click="onImportBuiltin"
        >
          导入系统资料
        </ElButton>
        <ElButton class="document-refresh-button" :loading="pageLoading" @click="loadDocuments">
          <Refresh aria-hidden="true" />
          刷新列表
        </ElButton>
      </div>
    </section>

    <ElAlert v-if="pageError" :title="pageError" type="error" show-icon :closable="false" />
    <ElAlert v-if="actionError" :title="actionError" type="error" show-icon :closable="true" @close="actionError = ''" />

    <DocumentUpload @uploaded="onUpload" />

    <DocumentList
      :documents="documents"
      :loading="pageLoading"
      :page="documentPage"
      :size="documentPageSize"
      :total="totalDocuments"
      :selected-id="selectedDocumentId"
      :deleting-id="deletingDocumentId"
      :keyword="keyword"
      :status="status"
      @filter="onFilter"
      @page="onDocumentPage"
      @size="onDocumentSize"
      @select="selectDocument"
      @reimport="onReimport"
      @delete="onDelete"
    />

    <section v-if="selectedDocument" class="selected-document-panel">
      <div class="selected-document-heading">
        <div>
          <h2>{{ selectedDocument.file_name }}</h2>
          <div class="selected-document-meta">
            <span class="document-status" :class="`document-status-${selectedDocument.status.toLowerCase()}`">
              <span class="document-status-dot" aria-hidden="true" />
              {{ selectedDocument.file_type.toUpperCase() }}
            </span>
            <span class="selected-document-meta-divider">·</span>
            <span class="selected-document-status-label">
              {{ selectedDocument.status === 'SUCCESS' ? '已完成' : selectedDocument.status === 'FAILED' ? '处理失败' : '处理中' }}
            </span>
          </div>
        </div>
      </div>

      <p v-if="selectedDocument.status === 'PROCESSING'" class="muted-copy">
        正在解析资料并建立索引，页面每 2 秒自动检查处理状态。
      </p>
      <ElAlert
        v-else-if="selectedDocument.status === 'FAILED'"
        :title="selectedDocument.error_message || '资料处理失败，可重新导入。'"
        type="error"
        show-icon
        :closable="false"
      />
      <ChunkList
        v-else
        :chunks="chunks"
        :loading="chunksLoading"
        :page="chunkPage"
        :size="chunkPageSize"
        :total="totalChunks"
        :selected-id="selectedChunk?.id"
        @select="openChunk"
        @page="onChunkPage"
        @size="onChunkSize"
      />
    </section>

    <ChunkDetail v-model="chunkDetailVisible" :chunk="selectedChunk" />
  </div>
</template>
