<!-- 文件作用：展示文档列表、导入状态和文档管理操作。 -->
<script setup lang="ts">
import { ref, watch } from 'vue'
import { ElButton, ElInput, ElOption, ElPagination, ElSelect, ElTag } from 'element-plus'
import { ArrowRight, Delete, Filter, Search } from '@element-plus/icons-vue'

import type { DocumentItem } from '@/api/documents'

// 父组件传入的文档列表、筛选、分页及操作状态属性。
const props = defineProps<{
  /** 当前页的文档数据。 */
  documents: DocumentItem[]
  /** 文档列表是否正在加载。 */
  loading: boolean
  /** 当前页码。 */
  page: number
  /** 每页文档数量。 */
  size: number
  /** 匹配筛选条件的文档总数。 */
  total: number
  /** 当前选中文档标识。 */
  selectedId?: number
  /** 正在删除的文档标识。 */
  deletingId?: number
  /** 已提交的关键词过滤条件。 */
  keyword: string
  /** 已提交的文档状态过滤条件；空字符串表示不筛选。 */
  status: DocumentItem['status'] | ''
  /** 当前账号是否允许导入、重试或删除共享资料。 */
  canManage?: boolean
}>()

// 向页面发送筛选、分页、选择和文档操作事件。
const emit = defineEmits<{
  filter: [query: { keyword: string; status: DocumentItem['status'] | '' }]
  page: [page: number]
  size: [size: number]
  select: [document: DocumentItem]
  reimport: [document: DocumentItem]
  delete: [document: DocumentItem]
}>()

// 当前编辑但尚未提交的关键词筛选值。
const draftKeyword = ref(props.keyword)
// 当前编辑但尚未提交的状态筛选值。
const draftStatus = ref(props.status)
// 各文档导入状态对应的中文展示标签。
const statusLabels: Record<DocumentItem['status'], string> = {
  PROCESSING: '处理中',
  SUCCESS: '已完成',
  FAILED: '失败',
}
// 父组件的已提交筛选值变化时，同步本地草稿。
watch(() => props.keyword, (value) => { draftKeyword.value = value })
watch(() => props.status, (value) => { draftStatus.value = value })

/** 将本地筛选草稿提交给父组件执行查询。 */
function applyFilters() {
  emit('filter', { keyword: draftKeyword.value.trim(), status: draftStatus.value })
}

/** 将后端时间字符串格式化为本地日期和分钟。
 * @param value ISO 日期时间字符串。
 * @returns 格式化后的日期；无效时间原样返回。
 */
function formatDate(value: string) {
  // date 用于提取年月日和时分字段。
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  // pad 将个位数字补成两位，保持日期格式统一。
  const pad = (part: number) => String(part).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`
}

/** 获取文档状态对应的中文标签。
 * @param status 后端文档状态。
 */
function statusLabel(status: DocumentItem['status']) {
  return statusLabels[status]
}

/** 生成与文档状态对应的 CSS 类名。
 * @param status 后端文档状态。
 */
function statusClass(status: DocumentItem['status']) {
  return `document-status-${status.toLowerCase()}`
}

/** 校验并向父组件发送选中的文档行。
 * @param row 表格组件传出的行对象。
 */
function selectRow(row: unknown) {
  emit('select', row as DocumentItem)
}

/** 通知父组件重新导入指定文档。
 * @param document 表格行中的文档对象。
 */
function requestReimport(document: unknown) {
  emit('reimport', document as DocumentItem)
}

/** 通知父组件删除指定文档。
 * @param document 表格行中的文档对象。
 */
function requestDelete(document: unknown) {
  emit('delete', document as DocumentItem)
}

/** 在表格行获得 Enter 或空格键时触发文档选择。
 * @param event 当前键盘事件。
 * @param document 对应的文档数据。
 */
function selectWithKeyboard(event: KeyboardEvent, document: DocumentItem) {
  if (event.key === 'Enter' || event.key === ' ') {
    event.preventDefault()
    selectRow(document)
  }
}
</script>

<template>
  <section class="document-list" aria-labelledby="documents-title">
    <div class="section-heading">
      <div>
        <h2 id="documents-title">资料列表</h2>
        <p>选择已完成的资料，可查看其文本分块。</p>
      </div>
      <div class="document-filters">
        <ElInput
          v-model="draftKeyword"
          clearable
          placeholder="按文件名搜索"
          aria-label="按文件名搜索"
          @keyup.enter="applyFilters"
          @clear="applyFilters"
        >
          <template #prefix><Search aria-hidden="true" /></template>
        </ElInput>
        <ElSelect v-model="draftStatus" clearable placeholder="全部状态" aria-label="按处理状态筛选">
          <ElOption label="处理中" value="PROCESSING" />
          <ElOption label="已完成" value="SUCCESS" />
          <ElOption label="失败" value="FAILED" />
        </ElSelect>
        <ElButton class="document-filter-button" @click="applyFilters">
          <Filter aria-hidden="true" />
          筛选
        </ElButton>
      </div>
    </div>

    <div v-loading="loading" class="document-table-wrap">
      <table class="document-data-table">
        <thead>
          <tr>
            <th scope="col">文件名</th>
            <th scope="col">归属</th>
            <th scope="col">格式</th>
            <th scope="col">状态</th>
            <th scope="col">Chunk 数</th>
            <th scope="col">创建时间</th>
            <th scope="col">处理说明</th>
            <th v-if="canManage" scope="col">操作</th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="document in documents"
            :key="document.id"
            class="document-data-row"
            :class="{ 'selected-document-row': document.id === selectedId }"
            :aria-selected="document.id === selectedId"
            tabindex="0"
            @click="selectRow(document)"
            @keydown="selectWithKeyboard($event, document)"
          >
            <td class="document-name-cell" :title="document.file_name">
              <span>{{ document.file_name }}</span>
            </td>
            <td class="document-source-cell">
              <ElTag
                size="small"
                :type="document.source_type === 'BUILTIN' ? 'success' : 'info'"
              >
                {{ document.source_type === 'BUILTIN' ? '系统资料' : '用户上传' }}
              </ElTag>
            </td>
            <td class="document-format-cell">{{ document.file_type.toUpperCase() }}</td>
            <td>
              <span class="document-status" :class="statusClass(document.status)">
                <span class="document-status-dot" aria-hidden="true" />
                {{ statusLabel(document.status) }}
              </span>
            </td>
            <td>{{ document.chunk_count ?? '—' }}</td>
            <td class="document-created-cell">{{ formatDate(document.created_at) }}</td>
            <td class="document-description-cell">
              <span v-if="document.status === 'FAILED'" class="failure-message">
                {{ document.error_message || '处理失败' }}
              </span>
              <span v-else-if="document.status === 'PROCESSING'" class="muted-copy">正在解析并建立索引</span>
              <span v-else class="muted-copy">—</span>
            </td>
            <td v-if="canManage">
              <div class="document-operation-actions">
                <ElButton
                  v-if="document.status === 'FAILED'"
                  class="document-reimport-button"
                  :disabled="deletingId === document.id"
                  @click.stop="requestReimport(document)"
                >
                  重新导入
                  <ArrowRight aria-hidden="true" />
                </ElButton>
                <ElButton
                  class="document-delete-button"
                  :disabled="deletingId === document.id"
                  :aria-busy="deletingId === document.id"
                  @click.stop="requestDelete(document)"
                >
                  <Delete aria-hidden="true" />
                  {{ deletingId === document.id ? '删除中…' : '删除' }}
                </ElButton>
              </div>
            </td>
          </tr>
          <tr v-if="!loading && documents.length === 0" class="document-empty-row">
            <td :colspan="canManage ? 8 : 7">暂无资料</td>
          </tr>
        </tbody>
      </table>
    </div>

    <ElPagination
      v-if="total > 0"
      class="document-pagination"
      background
      layout="total, sizes, prev, pager, next"
      :current-page="page"
      :page-size="size"
      :page-sizes="[10, 20, 50]"
      :total="total"
      @current-change="(nextPage: number) => emit('page', nextPage)"
      @size-change="(nextSize: number) => emit('size', nextSize)"
    />
  </section>
</template>
