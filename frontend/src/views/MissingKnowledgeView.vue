<!-- 文件作用：组织缺失知识记录浏览、筛选和处理页面。 -->
<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { ElButton, ElInput, ElOption, ElSelect } from 'element-plus'
import { Filter, Search } from '@element-plus/icons-vue'

import {
  getMissingKnowledge,
  updateMissingKnowledge,
  type MissingKnowledgeItem,
  type MissingKnowledgeQuery,
  type MissingKnowledgeStatus,
  type MissingKnowledgeSort,
} from '@/api/missingKnowledge'
import { getErrorMessage } from '@/api/http'

interface Draft {
  status: MissingKnowledgeStatus
  note: string
}

// 当前页知识缺口记录列表。
const items = ref<MissingKnowledgeItem[]>([])
// 按记录 ID 保存的未提交状态和备注草稿。
const drafts = ref<Record<number, Draft>>({})
// 搜索框中的问题或缺口关键词。
const keywordInput = ref('')
// 知识缺口记录状态筛选值。
const statusFilter = ref<MissingKnowledgeStatus | ''>('')
// 知识缺口记录排序方式。
const sortFilter = ref<MissingKnowledgeSort>('count_desc')
// 知识缺口列表当前页码。
const page = ref(1)
// 知识缺口列表每页数量。
const size = ref(20)
// 符合筛选条件的记录总数。
const total = ref(0)
// 知识缺口列表总页数。
const pages = ref(0)
// 知识缺口列表请求是否正在执行。
const loading = ref(false)
// 当前正在保存的记录 ID。
const savingId = ref<number>()
// 记录列表加载失败信息。
const pageError = ref('')
// 记录更新操作失败信息。
const actionError = ref('')
// 请求版本号，用于忽略过期列表响应。
let requestId = 0
// 缺口主题代码到中文名称的映射表。
const topicLabels: Record<string, string> = {
  wage_payment: '工资支付与欠薪',
  termination: '劳动关系解除与经济补偿',
  labor_contract: '劳动合同与劳动关系',
  social_insurance: '社会保险与住房公积金',
  working_time: '工作时间与休息休假',
  work_injury: '工伤认定与待遇',
  other: '其他劳动权益问题',
}

/** 按当前筛选、排序和分页条件加载知识缺口记录。 */
async function loadItems() {
  // 本次列表请求的版本号。
  const currentRequest = ++requestId
  // 本次列表查询使用的筛选、排序和分页参数。
  const query: MissingKnowledgeQuery = {
    page: page.value,
    size: size.value,
    status: statusFilter.value || undefined,
    keyword: keywordInput.value.trim() || undefined,
    sort: sortFilter.value,
  }
  loading.value = true
  pageError.value = ''
  try {
    // 本次后端请求返回的数据。
    const result = await getMissingKnowledge(query)
    if (currentRequest !== requestId) return
    items.value = result.items
    total.value = result.total
    pages.value = result.pages
    drafts.value = Object.fromEntries(
      result.items.map((item) => [item.id, { status: item.status, note: item.note ?? '' }]),
    )
  } catch (error) {
    if (currentRequest === requestId) pageError.value = getErrorMessage(error)
  } finally {
    if (currentRequest === requestId) loading.value = false
  }
}

/** 应用关键词和状态筛选并从第一页重新查询。 */
function applyFilters() {
  page.value = 1
  void loadItems()
}

/** 更新记录排序方式并重新加载第一页。 */
// 参数 nextSort：用户选择的排序方式。
function changeSort(nextSort: MissingKnowledgeSort) {
  sortFilter.value = nextSort
  page.value = 1
  void loadItems()
}

/** 切换知识缺口列表页码。 */
// 参数 nextPage：用户选择的目标页码。
function changePage(nextPage: number) {
  page.value = nextPage
  void loadItems()
}

/** 更新每页记录数量并回到第一页。 */
// 参数 nextSize：用户选择的每页数量。
function changeSize(nextSize: number | string) {
  size.value = Number(nextSize)
  page.value = 1
  void loadItems()
}

/** 保存指定知识缺口记录的状态及备注。 */
// 参数 item：当前待保存的 MissingKnowledgeItem，提供记录 ID 及原始状态；编辑草稿按该 ID 取出后提交状态和备注。
async function save(item: MissingKnowledgeItem) {
  // 指定记录当前编辑中的状态和备注。
  const draft = drafts.value[item.id]
  if (!draft) return
  savingId.value = item.id
  actionError.value = ''
  try {
    // 更新接口返回的最新知识缺口记录。
    const updated = await updateMissingKnowledge(item.id, {
      status: draft.status,
      note: draft.note.trim() || null,
    })
    if (statusFilter.value && updated.status !== statusFilter.value) {
      await loadItems()
    } else {
      items.value = items.value.map((current) => current.id === updated.id ? updated : current)
      drafts.value[updated.id] = { status: updated.status, note: updated.note ?? '' }
    }
  } catch (error) {
    actionError.value = `更新失败：${getErrorMessage(error)}`
  } finally {
    savingId.value = undefined
  }
}

/** 将日期字符串格式化为界面日期文本。 */
// 参数 value：后端返回的时间字符串；转换为本地日期时间供列表显示，解析失败时保留原值便于定位异常数据。
function formatDate(value: string) {
  // 待格式化的日期时间对象。
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  // 将个位数字补零到两位的格式化函数。
  const pad = (part: number) => String(part).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`
}

/** 将主题代码映射为中文标签。 */
// 参数 topicKey：知识缺口记录中的主题代码。
function topicLabel(topicKey: string) {
  return topicLabels[topicKey] ?? topicKey
}

onMounted(() => void loadItems())
</script>

<template>
  <section class="missing-knowledge-page" aria-labelledby="missing-knowledge-title">
    <div class="page-intro documents-page-intro">
      <div>
        <h1 id="missing-knowledge-title">知识缺口</h1>
        <p>查看因缺少可靠依据而拒答的高频问题，并维护处理状态与备注。</p>
      </div>
      <div class="missing-knowledge-total" aria-label="知识缺口主题数量">
        <strong>{{ total }}</strong>
        <span>个主题</span>
      </div>
    </div>

    <p v-if="pageError" class="missing-knowledge-error" role="alert">{{ pageError }}</p>
    <p v-if="actionError" class="missing-knowledge-error" role="alert">{{ actionError }}</p>

    <section class="missing-knowledge-panel" aria-label="知识缺口列表">
      <form class="missing-knowledge-filters" @submit.prevent="applyFilters">
        <label class="missing-filter-control missing-filter-search">
          <span class="sr-only">关键词</span>
          <ElInput
            v-model="keywordInput"
            class="missing-filter-input"
            type="search"
            aria-label="搜索问题或缺失方向"
            placeholder="搜索问题或缺失方向"
          >
            <template #prefix><Search aria-hidden="true" /></template>
          </ElInput>
        </label>
        <label class="missing-filter-control">
          <span class="sr-only">状态</span>
          <ElSelect v-model="statusFilter" class="missing-filter-select" aria-label="按状态筛选" placeholder="全部状态" clearable>
            <ElOption label="待补充" value="PENDING" />
            <ElOption label="已补充" value="RESOLVED" />
            <ElOption label="忽略" value="IGNORED" />
          </ElSelect>
        </label>
        <label class="missing-filter-control">
          <span class="sr-only">排序方式</span>
          <ElSelect
            v-model="sortFilter"
            class="missing-filter-select"
            aria-label="选择排序方式"
            placeholder="排序方式"
            @change="changeSort"
          >
            <ElOption label="拒答次数从高到低" value="count_desc" />
            <ElOption label="最近出现从新到旧" value="last_seen_desc" />
          </ElSelect>
        </label>
        <ElButton type="primary" native-type="submit" class="missing-knowledge-primary missing-filter-submit">
          <Filter aria-hidden="true" />
          <span>筛选</span>
        </ElButton>
      </form>

      <div class="missing-knowledge-table-wrap" :aria-busy="loading">
        <table class="missing-knowledge-table">
          <thead>
            <tr>
              <th scope="col">主题</th>
              <th scope="col">典型问题</th>
              <th scope="col">次数</th>
              <th scope="col">缺失资料方向</th>
              <th scope="col">首次 / 最近出现</th>
              <th scope="col">状态与备注</th>
              <th scope="col">操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="item in items" :key="item.id">
              <td>{{ topicLabel(item.topic_key) }}</td>
              <td class="question-cell">{{ item.sample_question }}</td>
              <td class="missing-knowledge-row-count">{{ item.count }} 次</td>
              <td>{{ item.missing_area }}</td>
              <td class="date-cell">
                <span>{{ formatDate(item.first_seen_at) }} <b>/</b></span>
                <span>{{ formatDate(item.last_seen_at) }}</span>
              </td>
              <td class="edit-cell">
                <ElSelect
                  v-model="drafts[item.id].status"
                  class="missing-status-select"
                  :aria-label="`更新 ${topicLabel(item.topic_key)} 的状态`"
                >
                  <ElOption label="待补充" value="PENDING" />
                  <ElOption label="已补充" value="RESOLVED" />
                  <ElOption label="忽略" value="IGNORED" />
                </ElSelect>
                <ElInput
                  v-model="drafts[item.id].note"
                  class="missing-note-input"
                  type="textarea"
                  :aria-label="`更新 ${topicLabel(item.topic_key)} 的备注`"
                  :rows="1"
                  placeholder="添加处理备注"
                />
              </td>
              <td>
                <ElButton
                  type="primary"
                  class="missing-knowledge-primary"
                  :loading="savingId === item.id"
                  :disabled="savingId === item.id"
                  @click="save(item)"
                >{{ savingId === item.id ? '保存中…' : '保存' }}</ElButton>
              </td>
            </tr>
            <tr v-if="!loading && items.length === 0">
              <td colspan="7" class="empty-copy">暂无符合条件的知识缺口</td>
            </tr>
          </tbody>
        </table>
      </div>

      <div class="missing-knowledge-pagination">
        <span>共 {{ total }} 条，第 {{ page }} / {{ pages || 1 }} 页</span>
        <label>
          每页
          <ElSelect
            :model-value="size"
            class="missing-page-size"
            aria-label="每页条数"
            @update:model-value="changeSize"
          >
            <ElOption :value="10" label="10" />
            <ElOption :value="20" label="20" />
            <ElOption :value="50" label="50" />
          </ElSelect>
        </label>
        <ElButton class="missing-page-previous" native-type="button" :disabled="page <= 1 || loading" @click="changePage(page - 1)">上一页</ElButton>
        <ElButton class="missing-page-next" native-type="button" :disabled="page >= pages || loading" @click="changePage(page + 1)">下一页</ElButton>
      </div>
    </section>
  </section>
</template>
