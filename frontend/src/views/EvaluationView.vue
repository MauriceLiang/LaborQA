<!-- 文件作用：组织评估用例维护、评估任务执行和结果展示页面。 -->
<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import {
  ElButton,
  ElDialog,
  ElInput,
  ElMessage,
  ElMessageBox,
  ElOption,
  ElSelect,
  ElSwitch,
} from 'element-plus'
import { ArrowRight, Search } from '@element-plus/icons-vue'

import {
  createEvaluationCase,
  createEvaluationRun,
  deleteEvaluationCase,
  deleteEvaluationRun,
  getEvaluationCases,
  getEvaluationRun,
  getEvaluationRuns,
  updateEvaluationCase,
  type EvaluationAnswerStyle,
  type EvaluationCase,
  type EvaluationCaseInput,
  type EvaluationCaseOrigin,
  type EvaluationCaseScope,
  type EvaluationCaseStatus,
  type EvaluationExpectedType,
  type EvaluationJobStatus,
  type EvaluationRunDetail,
  type EvaluationRunSummary,
} from '@/api/evaluations'
import { getErrorMessage } from '@/api/http'
import MarkdownContent from '@/components/chat/MarkdownContent.vue'
import { usePreferencesStore } from '@/stores/preferences'
import { aiModeLabel } from '@/types/preferences'

// 提供当前模型及回答偏好设置的状态仓库。
const preferencesStore = usePreferencesStore()
// 当前页评估用例列表。
const cases = ref<EvaluationCase[]>([])
// 评估用例列表当前页码。
const casePage = ref(1)
// 评估用例列表每页数量。
const caseSize = ref(20)
// 符合筛选条件的评估用例总数。
const caseTotal = ref(0)
// 评估用例列表总页数。
const casePages = ref(0)
// 筛选表单中正在编辑的主题关键词。
const topicInput = ref('')
// 已应用的主题关键词筛选条件。
const topicFilter = ref('')
// 已应用的预期回答类型筛选条件。
const expectedTypeFilter = ref<EvaluationExpectedType | ''>('')
// 已应用的多轮对话筛选条件。
const multiTurnFilter = ref<'' | 'true' | 'false'>('')
// 已应用的用例来源筛选条件。
const caseOriginFilter = ref<EvaluationCaseOrigin | ''>('')
// 已应用的用例状态筛选条件。
const caseStatusFilter = ref<EvaluationCaseStatus | ''>('')
// 当前评测选中的用例 ID 集合。
const selectedCaseIds = ref(new Set<number>())
// 用例列表请求是否正在执行。
const loadingCases = ref(false)
// 用例列表加载错误信息。
const casesError = ref('')
// 用例请求版本号，用于忽略过期响应。
let caseRequestId = 0

type CaseForm = EvaluationCaseInput

// 创建字段为空且采用默认类型的用例表单。
const emptyCaseForm = (): CaseForm => ({
  topic: '',
  expected_type: 'ANSWER',
  turns: [''],
  expected_points: [''],
  expected_sources: [],
  should_show_compliance: false,
})

// 用例新建或编辑对话框是否显示。
const caseDialogVisible = ref(false)
// 当前正在编辑的用例；无值表示新建。
const editingCase = ref<EvaluationCase>()
// 用例编辑表单中的字段值。
const caseForm = ref<CaseForm>(emptyCaseForm())
// 用例表单校验或保存失败信息。
const caseFormError = ref('')
// 用例保存请求是否正在执行。
const savingCase = ref(false)
// 当前正在归档的用例 ID。
const deletingCaseId = ref<number>()
// 评测用例的选择范围。
const caseScope = ref<EvaluationCaseScope>('BUILTIN_BASELINE')

// 评测运行列表。
const runs = ref<EvaluationRunSummary[]>([])
// 评测运行列表当前页码。
const runPage = ref(1)
// 评测运行列表每页数量。
const runSize = 10
// 评测运行总数。
const runTotal = ref(0)
// 评测运行列表总页数。
const runPages = ref(0)
// 评测运行列表请求是否正在执行。
const loadingRuns = ref(false)
// 当前正在删除的运行 ID。
const deletingRunId = ref<number>()
// 评测运行列表加载错误信息。
const runsError = ref('')
// 新评测任务名称输入值。
const runName = ref('')
// 新评测任务采用的回答风格。
const answerStyle = ref<EvaluationAnswerStyle>('plain')
// 评测任务创建请求是否正在执行。
const creatingRun = ref(false)
// 评测任务创建失败信息。
const createError = ref('')
// 当前展开的评测运行 ID。
const selectedRunId = ref<number>()
// 当前选中评测运行的详细结果。
const runDetail = ref<EvaluationRunDetail>()
// 评测运行详情请求是否正在执行。
const loadingDetail = ref(false)
// 评测运行详情加载失败信息。
const detailError = ref('')
// 各运行详情请求的版本号映射。
const detailRequestVersions = new Map<number, number>()
// 当前选择的运行详情请求版本。
let selectedRunVersion = 0
// 评测运行状态轮询定时器。
let pollTimer: ReturnType<typeof setInterval> | undefined

// 当前页所有活动用例是否均已选中。
const selectedPageFully = computed(
  () => {
    // 当前页状态为活动的评估用例。
    const activeCases = cases.value.filter((item) => item.status === 'ACTIVE')
    return activeCases.length > 0 && activeCases.every((item) => selectedCaseIds.value.has(item.id))
  },
)
// 当前选中运行在列表中的摘要记录。
const selectedRunSummary = computed(() => runs.value.find((run) => run.id === selectedRunId.value))
// 全量活动用例选择入口的显示文案。
const allActiveCaseLabel = computed(() => {
  // 当前筛选是否覆盖全部活动用例。
  const isUnfilteredActiveList = !topicFilter.value
    && !expectedTypeFilter.value
    && !multiTurnFilter.value
    && !caseOriginFilter.value
    && !caseStatusFilter.value
  return isUnfilteredActiveList ? `全部活动用例（${caseTotal.value} 条）` : '全部活动用例'
})
// 当前展示的评测运行名称。
const displayedRunName = computed(
  () => runDetail.value?.name ?? selectedRunSummary.value?.name ?? `评测批次 #${selectedRunId.value ?? ''}`,
)
// 当前运行详情或摘要的状态值。
const displayedStatus = computed(
  () => runDetail.value?.status ?? selectedRunSummary.value?.status,
)
// 当前评测结果是否包含不可用指标。
const hasUnavailableMetrics = computed(() => {
  // 当前评测运行的汇总指标。
  const metrics = runDetail.value?.metrics
  return metrics !== null
    && metrics !== undefined
    && Object.values(metrics).some((value) => value === null)
})
// 判断评测运行状态是否已结束的函数。
const terminalStatus = (status: EvaluationJobStatus | undefined) =>
  status === 'COMPLETED' || status === 'FAILED'

/** 按当前筛选和分页条件加载评估用例。 */
async function loadCases() {
  // 当前详情请求的版本号。
  const requestId = ++caseRequestId
  loadingCases.value = true
  casesError.value = ''
  try {
    // 本次后端请求返回的数据。
    const result = await getEvaluationCases({
      page: casePage.value,
      size: caseSize.value,
      topic: topicFilter.value || undefined,
      expected_type: expectedTypeFilter.value || undefined,
      is_multi_turn: multiTurnFilter.value === '' ? undefined : multiTurnFilter.value === 'true',
      origin: caseOriginFilter.value || undefined,
      status: caseStatusFilter.value || undefined,
      include_archived: caseStatusFilter.value === 'ARCHIVED',
    })
    if (requestId !== caseRequestId) return
    cases.value = result.items
    caseTotal.value = result.total
    casePages.value = result.pages
  } catch (error) {
    if (requestId === caseRequestId) casesError.value = getErrorMessage(error)
  } finally {
    if (requestId === caseRequestId) loadingCases.value = false
  }
}

/** 应用评估用例筛选项并重新加载第一页。 */
function applyCaseFilters() {
  topicFilter.value = topicInput.value.trim()
  casePage.value = 1
  void loadCases()
}

/** 初始化并打开新建评估用例表单。 */
function openCreateCase() {
  editingCase.value = undefined
  caseForm.value = emptyCaseForm()
  caseFormError.value = ''
  caseDialogVisible.value = true
}

/** 将选中用例填入表单并打开编辑窗口。 */
// 参数 item：当前选中的 EvaluationCase，包含来源、状态及用例正文；编辑和归档前会检查来源/状态以保护内置或已停用用例。
function openEditCase(item: EvaluationCase) {
  if (item.status !== 'ACTIVE') return
  editingCase.value = item
  caseForm.value = {
    topic: item.topic,
    expected_type: item.expected_type,
    turns: [...item.turns],
    expected_points: [...item.expected_points],
    expected_sources: item.expected_sources.map((source) => ({ ...source })),
    should_show_compliance: item.should_show_compliance,
  }
  caseFormError.value = ''
  caseDialogVisible.value = true
}

/** 在多轮用例中追加一个对话轮次。 */
function addCaseTurn() {
  if (caseForm.value.turns.length < 10) caseForm.value.turns.push('')
}

/** 移除指定位置的对话轮次。 */
// 参数 index：需要移除的对话轮次在表单中的下标。
function removeCaseTurn(index: number) {
  if (caseForm.value.turns.length <= 1) return
  caseForm.value.turns.splice(index, 1)
}

/** 为当前用例追加一个预期答案要点。 */
function addExpectedPoint() {
  if (caseForm.value.expected_points.length < 20) caseForm.value.expected_points.push('')
}

/** 移除指定位置的预期答案要点。 */
// 参数 index：需要移除的预期要点在表单中的下标。
function removeExpectedPoint(index: number) {
  caseForm.value.expected_points.splice(index, 1)
}

/** 为当前用例追加一个预期引用来源。 */
function addExpectedSource() {
  if (caseForm.value.expected_sources.length < 20) {
    caseForm.value.expected_sources.push({ file_name: '', chunk_no: null })
  }
}

/** 移除指定位置的预期引用来源。 */
// 参数 index：需要移除的预期来源在表单中的下标。
function removeExpectedSource(index: number) {
  caseForm.value.expected_sources.splice(index, 1)
}

/** 校验评估用例表单并返回首个错误提示。 */
function validateCaseForm(): string | undefined {
  caseForm.value.topic = caseForm.value.topic.trim()
  if (!caseForm.value.topic) return '请填写用例主题'
  // 去除首尾空白后的对话轮次文本。
  const turns = caseForm.value.turns.map((turn) => turn.trim())
  if (turns.some((turn) => !turn)) return '每轮问题都不能为空'
  caseForm.value.turns = turns
  // 去空并过滤空值后的预期答案要点。
  const points = caseForm.value.expected_points.map((point) => point.trim()).filter(Boolean)
  if (caseForm.value.expected_type === 'ANSWER' && points.length === 0) {
    return '应回答用例至少需要一个预期要点'
  }
  caseForm.value.expected_points = points
  // 规范化后的预期引用来源字段。
  const sources = caseForm.value.expected_sources.map((source) => ({
    file_name: source.file_name.trim(),
    chunk_no: source.chunk_no === null || source.chunk_no === undefined || Number.isNaN(Number(source.chunk_no))
      ? null
      : Number(source.chunk_no),
  }))
  if (sources.some((source) => !source.file_name || (source.chunk_no !== null && source.chunk_no < 1))) {
    return '引用来源需要填写文件名，分块号必须为正整数'
  }
  caseForm.value.expected_sources = sources
  return undefined
}

/** 创建或更新当前评估用例并刷新列表。 */
async function saveCase() {
  // 用例表单校验发现的错误提示。
  const validationError = validateCaseForm()
  if (validationError) {
    caseFormError.value = validationError
    return
  }
  savingCase.value = true
  caseFormError.value = ''
  try {
    if (editingCase.value) {
      await updateEvaluationCase(editingCase.value.id, {
        ...caseForm.value,
        version: editingCase.value.version,
      })
      ElMessage.success('评测用例已更新')
    } else {
      await createEvaluationCase(caseForm.value)
      ElMessage.success('评测用例已创建')
    }
    caseDialogVisible.value = false
    await loadCases()
  } catch (error) {
    caseFormError.value = getErrorMessage(error)
  } finally {
    savingCase.value = false
  }
}

/** 确认后归档指定评估用例。 */
// 参数 item：准备归档的 EvaluationCase；函数只允许用户创建且状态为 ACTIVE 的用例进入归档请求。
async function archiveCase(item: EvaluationCase) {
  if (item.origin === 'BUILTIN' || item.status !== 'ACTIVE' || deletingCaseId.value !== undefined) return
  try {
    await ElMessageBox.confirm(
      '删除后，该用例将从新的评测选择列表中移除；已有评测批次和结果不会被删除。',
      `确定删除评测用例“${item.topic}”吗？`,
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
  deletingCaseId.value = item.id
  casesError.value = ''
  try {
    await deleteEvaluationCase(item.id)
    // 根据选择变化生成的用例 ID 集合。
    const next = new Set(selectedCaseIds.value)
    next.delete(item.id)
    selectedCaseIds.value = next
    ElMessage.success('评测用例已删除')
    if (cases.value.length === 1 && casePage.value > 1) casePage.value -= 1
    await loadCases()
  } catch (error) {
    casesError.value = `删除评测用例失败：${getErrorMessage(error)}`
  } finally {
    deletingCaseId.value = undefined
  }
}

/** 切换评估用例列表页码。 */
// 参数 nextPage：用户选择的目标页码。
function changeCasePage(nextPage: number) {
  casePage.value = nextPage
  void loadCases()
}

/** 修改用例列表每页数量并重置页码。 */
// 参数 nextSize：用户选择的每页数量。
function changeCaseSize(nextSize: number) {
  caseSize.value = nextSize
  casePage.value = 1
  void loadCases()
}

/** 将指定用例加入或移出本次评测选择集。 */
// 参数 checked：当前用例是否处于选中状态。
// 参数 id：目标业务记录的唯一标识。
function toggleCase(id: number, checked: boolean) {
  // 根据选择变化生成的用例 ID 集合。
  const next = new Set(selectedCaseIds.value)
  if (checked) next.add(id)
  else next.delete(id)
  selectedCaseIds.value = next
}

/** 全选或取消选择当前页活动用例。 */
function toggleCurrentPage() {
  // 根据选择变化生成的用例 ID 集合。
  const next = new Set(selectedCaseIds.value)
  // 当前页状态为活动的评估用例。
  const activeCases = cases.value.filter((item) => item.status === 'ACTIVE')
  if (selectedPageFully.value) activeCases.forEach((item) => next.delete(item.id))
  else activeCases.forEach((item) => next.add(item.id))
  selectedCaseIds.value = next
}

/** 按当前分页条件加载评测运行记录。 */
async function loadRuns() {
  loadingRuns.value = true
  runsError.value = ''
  try {
    // 本次后端请求返回的数据。
    const result = await getEvaluationRuns({ page: runPage.value, size: runSize })
    runs.value = result.items
    runTotal.value = result.total
    runPages.value = result.pages
  } catch (error) {
    runsError.value = getErrorMessage(error)
  } finally {
    loadingRuns.value = false
  }
}

/** 将运行详情中的最新状态同步到列表摘要。 */
// 参数 detail：当前读取到的资源详情数据。
function syncRunSummary(detail: EvaluationRunDetail) {
  // 运行记录在当前列表中的位置。
  const index = runs.value.findIndex((run) => run.id === detail.id)
  if (index === -1) return

  runs.value[index] = {
    ...runs.value[index],
    name: detail.name,
    status: detail.status,
    progress_current: detail.progress_current,
    progress_total: detail.progress_total,
    error_message: detail.error_message,
  }
}

/** 停止评测运行状态轮询。 */
function stopPolling() {
  if (pollTimer !== undefined) {
    clearInterval(pollTimer)
    pollTimer = undefined
  }
}

/** 开始轮询指定评测运行的最新状态。 */
// 参数 id：目标业务记录的唯一标识。
function startPolling(id: number) {
  if (pollTimer !== undefined) return
  pollTimer = setInterval(() => {
    if (!detailRequestVersions.has(id) && selectedRunId.value === id) void loadRunDetail(id)
  }, 2000)
}

/** 加载指定运行详情，并按请求版本丢弃过期响应。 */
// 参数 id：目标业务记录的唯一标识。
// 参数 showLoading：是否显示详情区域的加载状态。
async function loadRunDetail(id: number, showLoading = false) {
  // 当前运行详情请求版本。
  const version = selectedRunVersion
  if (detailRequestVersions.get(id) === version) return
  detailRequestVersions.set(id, version)
  if (showLoading) loadingDetail.value = true
  detailError.value = ''
  try {
    // 本次后端请求返回的数据。
    const result = await getEvaluationRun(id)
    if (version !== selectedRunVersion || selectedRunId.value !== id) return
    runDetail.value = result
    syncRunSummary(result)
    if (terminalStatus(result.status)) stopPolling()
    else startPolling(id)
  } catch (error) {
    if (version === selectedRunVersion && selectedRunId.value === id) detailError.value = getErrorMessage(error)
  } finally {
    if (detailRequestVersions.get(id) === version) detailRequestVersions.delete(id)
    if (version === selectedRunVersion && selectedRunId.value === id && showLoading) {
      loadingDetail.value = false
    }
  }
}

/** 选中或取消展开指定评测运行。 */
// 参数 run：评测运行记录。
function toggleRun(run: EvaluationRunSummary) {
  stopPolling()
  selectedRunVersion += 1
  if (selectedRunId.value === run.id) {
    selectedRunId.value = undefined
    runDetail.value = undefined
    loadingDetail.value = false
    detailError.value = ''
    return
  }
  selectedRunId.value = run.id
  runDetail.value = undefined
  detailError.value = ''
  if (!terminalStatus(run.status)) startPolling(run.id)
  void loadRunDetail(run.id, true)
}

/** 确认后删除指定评测运行及其结果。 */
// 参数 run：评测运行记录。
async function deleteRun(run: EvaluationRunSummary) {
  if (!terminalStatus(run.status) || deletingRunId.value !== undefined) return
  try {
    await ElMessageBox.confirm(
      '将同时删除该批次的逐题结果、指标和用例关联，删除后不可恢复。',
      `确定删除评测批次“${run.name}”吗？`,
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

  deletingRunId.value = run.id
  runsError.value = ''
  try {
    await deleteEvaluationRun(run.id)
    if (selectedRunId.value === run.id) {
      stopPolling()
      selectedRunVersion += 1
      selectedRunId.value = undefined
      runDetail.value = undefined
      loadingDetail.value = false
      detailError.value = ''
    }
    if (runs.value.length === 1 && runPage.value > 1) runPage.value -= 1
    await loadRuns()
  } catch (error) {
    runsError.value = `删除评测批次失败：${getErrorMessage(error)}`
  } finally {
    deletingRunId.value = undefined
  }
}

/** 按当前选中用例和答案风格创建评测任务。 */
async function createRun() {
  // 去除首尾空白后的评测任务名称。
  const name = runName.value.trim()
  if (!name) {
    createError.value = '请填写评测批次名称'
    return
  }
  if (caseScope.value === 'SELECTED' && selectedCaseIds.value.size === 0) {
    createError.value = '选择“已选用例”时，至少勾选一条用例'
    return
  }

  creatingRun.value = true
  createError.value = ''
  try {
    // 新建评测任务接口返回的任务信息。
    const job = await createEvaluationRun({
      name,
      case_ids: caseScope.value === 'SELECTED'
        ? [...selectedCaseIds.value].sort((a, b) => a - b)
        : null,
      answer_style: answerStyle.value,
      case_scope: caseScope.value,
      ai_mode: preferencesStore.aiMode,
    })
    stopPolling()
    selectedRunVersion += 1
    selectedRunId.value = job.run_id
    runDetail.value = undefined
    detailError.value = ''
    if (!terminalStatus(job.status)) startPolling(job.run_id)
    void loadRunDetail(job.run_id, true)
    await loadRuns()
  } catch (error) {
    createError.value = getErrorMessage(error)
  } finally {
    creatingRun.value = false
  }
}

/** 切换评测运行列表页码。 */
// 参数 nextPage：用户选择的目标页码。
function changeRunPage(nextPage: number) {
  runPage.value = nextPage
  void loadRuns()
}

/** 将评测任务状态转换为中文展示文本。 */
// 参数 status：评测运行的当前状态枚举。
function formatStatus(status: EvaluationJobStatus | undefined) {
  if (status === 'PENDING') return '排队中'
  if (status === 'RUNNING') return '运行中'
  if (status === 'COMPLETED') return '已完成'
  if (status === 'FAILED') return '失败'
  return '加载中'
}

/** 将比例数值格式化为百分比文本。 */
// 参数 value：后端返回的 0 到 1 比率或空值；有效数字转为百分比并保留一位小数，空值显示为“不适用”。
function formatRate(value: number | null | undefined) {
  return value === null || value === undefined ? '不适用' : `${(value * 100).toFixed(1)}%`
}

/** 将可空布尔值格式化为中文文本。 */
// 参数 value：评测指标可能缺失的布尔结果；true/false 显示为“是/否”，null 保留“不适用”以区别于失败。
function formatFlag(value: boolean | null) {
  return value === null ? '不适用' : value ? '是' : '否'
}

/** 将回答模式转换为中文展示文本。 */
// 参数 value：后端 answer_mode 枚举值；函数将 RAG、部分回答、工具回答和拒答路径映射为面向用户的中文标签。
function formatAnswerMode(value: string | null | undefined) {
  if (value === 'RAG_ANSWER') return '完整回答'
  if (value === 'PARTIAL_ANSWER') return '部分回答'
  if (value === 'TOOL_ONLY') return '工具回答'
  if (value === 'REFUSAL') return '拒答'
  return '未记录'
}

/** 将证据判定结果转换为中文展示文本。 */
// 参数 value：后端 evidence_decision 枚举值；根据 SUFFICIENT、PARTIAL 或 INSUFFICIENT 显示对应中文证据结论。
function formatEvidenceDecision(value: string | null | undefined) {
  if (value === 'SUFFICIENT') return '证据充分'
  if (value === 'PARTIAL') return '证据部分充分'
  if (value === 'INSUFFICIENT') return '证据不足'
  return '未记录'
}

/** 格式化评估用例编号。 */
// 参数 id：目标业务记录的唯一标识。
function formatCaseId(id: number) {
  return `#${String(id).padStart(3, '0')}`
}

/** 将创建时间格式化为本地日期时间。 */
// 参数 value：服务端返回的 ISO 时间字符串；转为本地时间显示，若日期无效则原样返回以免丢失原始信息。
function formatCreatedAt(value: string) {
  // 待格式化的日期时间对象。
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  // 将个位数字补零到两位的格式化函数。
  const pad = (part: number) => String(part).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`
}

/** 根据已完成数计算任务进度百分比。 */
// 参数 current：已经完成的工作项数量。
// 参数 total：本次任务的工作项总数量。
function progressPercent(current: number, total: number) {
  if (total <= 0) return 0
  return Math.min(100, Math.round((current / total) * 100))
}

/** 将评测任务状态映射为界面样式类别。 */
// 参数 status：业务记录当前的状态枚举。
function statusClass(status: EvaluationJobStatus | undefined) {
  if (status === 'COMPLETED') return 'evaluation-status-completed'
  if (status === 'FAILED') return 'evaluation-status-failed'
  if (status === 'RUNNING') return 'evaluation-status-running'
  return 'evaluation-status-pending'
}

onMounted(() => {
  void loadCases()
  void loadRuns()
})

onBeforeUnmount(() => {
  stopPolling()
  caseRequestId += 1
  selectedRunVersion += 1
})
</script>

<template>
  <section class="evaluation-page" aria-labelledby="evaluation-title">
    <header class="page-intro documents-page-intro">
      <div>
        <h1 id="evaluation-title">问答测评</h1>
        <p>筛选评测用例，创建后台评测批次，并查看运行结果。</p>
      </div>
      <div class="evaluation-count" aria-label="已选择的评测用例数量">
        <span>已选</span>
        <strong>{{ selectedCaseIds.size }}</strong>
        <span>条用例</span>
      </div>
    </header>

    <section class="evaluation-panel" aria-labelledby="evaluation-cases-title">
      <div class="evaluation-section-heading">
        <div>
          <h2 id="evaluation-cases-title">评测用例</h2>
          <p>内置基线和自定义用例均可编辑；自定义用例可删除</p>
        </div>
        <ElButton type="primary" class="evaluation-primary" @click="openCreateCase">新建用例</ElButton>
      </div>
      <p v-if="casesError" class="evaluation-error" role="alert">{{ casesError }}</p>
      <form class="evaluation-filters" @submit.prevent="applyCaseFilters">
        <label class="evaluation-filter-control evaluation-filter-search">
          <span class="sr-only">搜索主题或问题</span>
          <ElInput
            v-model="topicInput"
            class="evaluation-filter-input"
            type="search"
            placeholder="搜索主题或问题关键词"
            aria-label="搜索主题或问题"
          >
            <template #prefix><Search aria-hidden="true" /></template>
          </ElInput>
        </label>
        <label class="evaluation-filter-control">
          <span class="sr-only">预期类型</span>
          <ElSelect v-model="expectedTypeFilter" class="evaluation-filter-select" aria-label="预期类型" placeholder="预期类型">
            <ElOption label="应回答" value="ANSWER" />
            <ElOption label="应拒答" value="REJECT" />
          </ElSelect>
        </label>
        <label class="evaluation-filter-control">
          <span class="sr-only">对话轮数</span>
          <ElSelect v-model="multiTurnFilter" class="evaluation-filter-select" aria-label="对话轮数" placeholder="对话轮数">
            <ElOption label="单轮" value="false" />
            <ElOption label="多轮" value="true" />
          </ElSelect>
        </label>
        <label class="evaluation-filter-control">
          <span class="sr-only">用例来源</span>
          <ElSelect v-model="caseOriginFilter" class="evaluation-filter-select" aria-label="用例来源" placeholder="用例来源">
            <ElOption label="内置基线" value="BUILTIN" />
            <ElOption label="自定义" value="CUSTOM" />
          </ElSelect>
        </label>
        <label class="evaluation-filter-control">
          <span class="sr-only">用例状态</span>
          <ElSelect v-model="caseStatusFilter" class="evaluation-filter-select" aria-label="用例状态" placeholder="活动用例">
            <ElOption label="活动用例" value="ACTIVE" />
            <ElOption label="已归档" value="ARCHIVED" />
          </ElSelect>
        </label>
        <ElButton type="primary" native-type="submit" class="evaluation-primary">筛选</ElButton>
      </form>

      <div class="evaluation-table-wrap" :aria-busy="loadingCases">
        <table class="evaluation-table evaluation-case-table">
          <thead>
            <tr>
              <th scope="col" class="evaluation-select-cell">
                <input aria-label="选择本页全部用例" type="checkbox" :disabled="!cases.some((item) => item.status === 'ACTIVE')" :checked="selectedPageFully" @change="toggleCurrentPage" />
              </th>
              <th scope="col">ID</th>
              <th scope="col">主题</th>
              <th scope="col">问题</th>
              <th scope="col">问题轮次</th>
              <th scope="col">预期</th>
              <th scope="col">多轮</th>
              <th scope="col">合规提示</th>
              <th scope="col">来源</th>
              <th scope="col">操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="item in cases" :key="item.id">
              <td class="evaluation-select-cell">
                <input :aria-label="`选择用例 ${item.id}`" type="checkbox" :disabled="item.status !== 'ACTIVE'" :checked="selectedCaseIds.has(item.id)" @change="toggleCase(item.id, ($event.target as HTMLInputElement).checked)" />
              </td>
              <td class="evaluation-case-id">{{ formatCaseId(item.id) }}</td>
              <td class="evaluation-case-topic">{{ item.topic }}</td>
              <td class="evaluation-case-question">{{ item.turns.join(' / ') }}</td>
              <td class="evaluation-case-rounds">{{ item.turns.length }}</td>
              <td>{{ item.expected_type === 'ANSWER' ? '应回答' : '应拒答' }}</td>
              <td>{{ item.turns.length > 1 ? '是' : '否' }}</td>
              <td>{{ item.should_show_compliance ? '需要' : '不需要' }}</td>
              <td>{{ item.origin === 'BUILTIN' ? '内置基线' : '自定义' }}</td>
              <td class="evaluation-case-actions">
                <template v-if="item.status === 'ACTIVE'">
                  <ElButton text size="small" @click.stop="openEditCase(item)">编辑</ElButton>
                  <ElButton v-if="item.origin === 'CUSTOM'" text type="danger" size="small" :loading="deletingCaseId === item.id" @click.stop="archiveCase(item)">删除</ElButton>
                </template>
                <span v-else class="evaluation-muted">已归档</span>
              </td>
            </tr>
            <tr v-if="!loadingCases && cases.length === 0">
              <td colspan="10" class="evaluation-empty">暂无符合条件的用例</td>
            </tr>
          </tbody>
        </table>
      </div>
      <div class="evaluation-pagination">
        <span>共 {{ caseTotal }} 条，第 {{ casePage }} / {{ casePages || 1 }} 页</span>
        <label>
          每页
          <ElSelect
            :model-value="caseSize"
            class="evaluation-page-size"
            aria-label="每页条数"
            @update:model-value="changeCaseSize"
          >
            <ElOption :value="10" label="10" />
            <ElOption :value="20" label="20" />
            <ElOption :value="50" label="50" />
          </ElSelect>
        </label>
        <ElButton native-type="button" :disabled="casePage <= 1 || loadingCases" @click="changeCasePage(casePage - 1)">上一页</ElButton>
        <ElButton native-type="button" :disabled="casePage >= casePages || loadingCases" @click="changeCasePage(casePage + 1)">下一页</ElButton>
      </div>
    </section>

    <ElDialog
      v-model="caseDialogVisible"
      class="evaluation-case-dialog"
      modal-class="evaluation-case-overlay"
      :title="editingCase ? '编辑评测用例' : '新建评测用例'"
      width="min(720px, calc(100vw - 32px))"
      destroy-on-close
    >
      <form class="evaluation-case-form" @submit.prevent="saveCase">
        <label class="evaluation-case-field">
          <span>主题</span>
          <ElInput v-model="caseForm.topic" maxlength="50" show-word-limit placeholder="例如：劳动合同签订期限" />
        </label>
        <label class="evaluation-case-field">
          <span>预期类型</span>
          <ElSelect v-model="caseForm.expected_type" placeholder="选择预期类型">
            <ElOption label="应回答" value="ANSWER" />
            <ElOption label="应拒答" value="REJECT" />
          </ElSelect>
        </label>

        <div class="evaluation-form-array evaluation-form-section">
          <div class="evaluation-form-array-heading">
            <span>问题轮次</span>
            <ElButton text type="primary" :disabled="caseForm.turns.length >= 10" @click="addCaseTurn">新增轮次</ElButton>
          </div>
          <div v-for="(turn, index) in caseForm.turns" :key="`turn-${index}`" class="evaluation-form-array-row">
            <ElInput v-model="caseForm.turns[index]" type="textarea" :rows="2" maxlength="2000" show-word-limit :placeholder="`第 ${index + 1} 轮问题`" />
            <ElButton text type="danger" :disabled="caseForm.turns.length <= 1" @click="removeCaseTurn(index)">删除</ElButton>
          </div>
        </div>

        <div class="evaluation-form-array evaluation-form-section">
          <div class="evaluation-form-array-heading">
            <span>预期要点</span>
            <ElButton text type="primary" :disabled="caseForm.expected_points.length >= 20" @click="addExpectedPoint">新增要点</ElButton>
          </div>
          <div v-for="(point, index) in caseForm.expected_points" :key="`point-${index}`" class="evaluation-form-array-row">
            <ElInput v-model="caseForm.expected_points[index]" maxlength="500" placeholder="回答应包含的关键点" />
            <ElButton text type="danger" @click="removeExpectedPoint(index)">删除</ElButton>
          </div>
        </div>

        <div class="evaluation-form-array evaluation-form-section">
          <div class="evaluation-form-array-heading">
            <span>预期引用来源（可选）</span>
            <ElButton text type="primary" :disabled="caseForm.expected_sources.length >= 20" @click="addExpectedSource">新增来源</ElButton>
          </div>
          <div v-for="(source, index) in caseForm.expected_sources" :key="`source-${index}`" class="evaluation-form-array-row evaluation-source-row">
            <ElInput v-model="source.file_name" placeholder="文件名" />
            <ElInput v-model="source.chunk_no" type="number" min="1" placeholder="分块号（可选）" />
            <ElButton text type="danger" @click="removeExpectedSource(index)">删除</ElButton>
          </div>
        </div>

        <label class="evaluation-switch-row">
          <span>需要合规提示</span>
          <ElSwitch v-model="caseForm.should_show_compliance" />
        </label>
        <p v-if="caseFormError" class="evaluation-error" role="alert">{{ caseFormError }}</p>
        <div class="evaluation-dialog-actions">
          <ElButton native-type="button" @click="caseDialogVisible = false">取消</ElButton>
          <ElButton type="primary" native-type="submit" :loading="savingCase">保存</ElButton>
        </div>
      </form>
    </ElDialog>

    <section class="evaluation-panel evaluation-create-panel" aria-labelledby="evaluation-create-title">
      <div class="evaluation-create-layout">
        <div class="evaluation-section-heading">
          <div>
            <h2 id="evaluation-create-title">创建评测批次</h2>
            <p>选择执行范围后创建后台评测批次；历史批次保留执行时的用例版本</p>
            <p class="evaluation-mode-hint">当前 AI 模式：{{ aiModeLabel(preferencesStore.aiMode) }}（由顶部全局选择器控制）</p>
          </div>
        </div>
        <form class="evaluation-create-form" @submit.prevent="createRun">
          <label>
            <span>批次名称</span>
            <ElInput
              v-model="runName"
              class="evaluation-create-input"
              maxlength="100"
              placeholder="例如：正式评测-20260915"
              aria-label="批次名称"
            />
          </label>
          <label>
            <span>回答风格</span>
            <ElSelect v-model="answerStyle" class="evaluation-create-select" aria-label="回答风格">
              <ElOption label="通俗版" value="plain" />
              <ElOption label="严谨版" value="legal" />
            </ElSelect>
          </label>
          <label>
            <span>执行范围</span>
            <ElSelect v-model="caseScope" class="evaluation-create-select" aria-label="执行范围">
              <ElOption label="官方基线（60 条）" value="BUILTIN_BASELINE" />
              <ElOption :label="allActiveCaseLabel" value="ALL_ACTIVE" />
              <ElOption :label="`已选用例（${selectedCaseIds.size} 条）`" value="SELECTED" />
            </ElSelect>
          </label>
          <ElButton class="evaluation-primary" type="primary" native-type="submit" :loading="creatingRun" :disabled="creatingRun">
            {{ creatingRun ? '创建中…' : '创建批次' }}
          </ElButton>
        </form>
      </div>
      <p v-if="createError" class="evaluation-error" role="alert">{{ createError }}</p>
    </section>

    <section class="evaluation-panel" aria-labelledby="evaluation-runs-title">
      <div class="evaluation-section-heading">
        <div>
          <h2 id="evaluation-runs-title">评测批次</h2>
          <p>点击批次展开详情，再次点击收起；展开的运行中任务每 2 秒更新一次。</p>
        </div>
      </div>
      <p v-if="runsError" class="evaluation-error" role="alert">{{ runsError }}</p>
      <p v-if="detailError" class="evaluation-error" role="alert">{{ detailError }}</p>
      <div class="evaluation-table-wrap" :aria-busy="loadingRuns">
        <table class="evaluation-table evaluation-run-table">
          <thead>
            <tr>
              <th scope="col" class="evaluation-select-cell"><span class="sr-only">展开状态</span></th>
              <th scope="col">批次</th>
              <th scope="col">状态</th>
              <th scope="col">进度</th>
              <th scope="col">创建时间</th>
              <th scope="col">操作</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="run in runs"
              :key="run.id"
              :class="{ 'evaluation-run-selected': run.id === selectedRunId }"
              @click="toggleRun(run)"
            >
              <td class="evaluation-select-cell">
                <ElButton
                  class="evaluation-run-toggle"
                  text
                  circle
                  :aria-label="`${run.id === selectedRunId ? '收起' : '展开'}评测批次 ${run.name}`"
                  :aria-expanded="run.id === selectedRunId"
                  aria-controls="evaluation-run-detail"
                  @click.stop="toggleRun(run)"
                >
                  <ArrowRight aria-hidden="true" />
                </ElButton>
              </td>
              <td>
                <button
                  class="evaluation-link"
                  type="button"
                  :aria-expanded="run.id === selectedRunId"
                  aria-controls="evaluation-run-detail"
                  @click.stop="toggleRun(run)"
                >
                  {{ run.name }}
                </button>
              </td>
              <td>
                <span class="evaluation-status-inline" :class="statusClass(run.status)">
                  <i aria-hidden="true" />{{ formatStatus(run.status) }}
                </span>
              </td>
              <td>{{ run.progress_current }} / {{ run.progress_total }}</td>
              <td>{{ formatCreatedAt(run.created_at) }}</td>
              <td>
                <ElButton
                  class="evaluation-run-delete"
                  type="danger"
                  text
                  :loading="deletingRunId === run.id"
                  :disabled="!terminalStatus(run.status) || deletingRunId !== undefined"
                  :title="terminalStatus(run.status) ? '删除评测批次' : '排队中或运行中的批次不能删除'"
                  :aria-label="`删除评测批次 ${run.name}`"
                  @click.stop="deleteRun(run)"
                >
                  删除
                </ElButton>
              </td>
            </tr>
            <tr v-if="!loadingRuns && runs.length === 0">
              <td colspan="6" class="evaluation-empty">暂无评测批次</td>
            </tr>
          </tbody>
        </table>
      </div>
      <div class="evaluation-pagination">
        <span>共 {{ runTotal }} 条，第 {{ runPage }} / {{ runPages || 1 }} 页</span>
        <ElButton native-type="button" :disabled="runPage <= 1 || loadingRuns" @click="changeRunPage(runPage - 1)">上一页</ElButton>
        <ElButton native-type="button" :disabled="runPage >= runPages || loadingRuns" @click="changeRunPage(runPage + 1)">下一页</ElButton>
      </div>
    </section>

    <section
      v-if="selectedRunId !== undefined"
      id="evaluation-run-detail"
      class="evaluation-panel evaluation-detail-panel"
      aria-labelledby="evaluation-detail-title"
    >
      <div class="evaluation-section-heading evaluation-detail-heading">
        <div>
          <h2 id="evaluation-detail-title">批次运行结果</h2>
          <p class="evaluation-detail-run-name">{{ displayedRunName }}</p>
          <p
            v-if="runDetail?.config.baseline_version !== undefined && runDetail.config.baseline_version !== null"
            class="evaluation-detail-run-name"
          >
            官方基线 v{{ runDetail.config.baseline_version }}
          </p>
          <p class="evaluation-detail-run-name">AI 模式：{{ aiModeLabel(runDetail?.config.ai_mode) }}</p>
        </div>
        <span class="evaluation-status" :class="statusClass(displayedStatus)">{{ formatStatus(displayedStatus) }}</span>
      </div>
      <p v-if="runDetail?.error_message" class="evaluation-error" role="alert">{{ runDetail.error_message }}</p>
      <p v-if="loadingDetail && !runDetail" class="evaluation-muted">正在读取评测详情…</p>
      <template v-if="runDetail">
        <div class="evaluation-progress" aria-label="评测进度">
          <div class="evaluation-progress-status">
            <span>状态</span>
            <span class="evaluation-status-inline" :class="statusClass(runDetail.status)">
              <i aria-hidden="true" />{{ formatStatus(runDetail.status) }}
            </span>
          </div>
          <div class="evaluation-progress-track">
            <span>进度</span>
            <strong>{{ runDetail.progress_current }} / {{ runDetail.progress_total }} 题</strong>
            <progress :value="runDetail.progress_current" :max="Math.max(runDetail.progress_total, 1)" />
          </div>
          <span class="evaluation-progress-percent">{{ progressPercent(runDetail.progress_current, runDetail.progress_total) }}%</span>
        </div>
        <div v-if="runDetail.metrics" class="evaluation-metrics" aria-label="评测指标">
          <div><span>回答正确率</span><strong>{{ formatRate(runDetail.metrics.accuracy) }}</strong></div>
          <div><span title="应拒答用例中正确拒答的比例">应拒答命中率</span><strong>{{ formatRate(runDetail.metrics.reject_rate) }}</strong></div>
          <div><span title="全部已完成用例中实际触发拒答的比例">实际拒答占比</span><strong>{{ formatRate(runDetail.metrics.refusal_rate) }}</strong></div>
          <div><span>最终引用命中率</span><strong>{{ formatRate(runDetail.metrics.citation_hit_rate) }}</strong></div>
          <div><span>检索来源命中率</span><strong>{{ formatRate(runDetail.metrics.retrieval_hit_rate) }}</strong></div>
          <div><span>上下文保留率</span><strong>{{ formatRate(runDetail.metrics.context_retention_rate) }}</strong></div>
          <div><span>多轮通过率</span><strong>{{ formatRate(runDetail.metrics.multi_turn_pass_rate) }}</strong></div>
          <div><span>合规提示命中率</span><strong>{{ formatRate(runDetail.metrics.compliance_hit_rate) }}</strong></div>
          <div><span>执行成功率</span><strong>{{ formatRate(runDetail.metrics.execution_success_rate) }}</strong></div>
        </div>
        <div v-else-if="terminalStatus(runDetail.status)" class="evaluation-muted">此批次没有可计算的指标。</div>
        <p v-if="hasUnavailableMetrics" class="evaluation-metric-note">
          “不适用”表示本批次没有包含该指标所需的用例；同时包含对应类型的用例才会生成完整指标。
        </p>

        <div class="evaluation-results">
          <h3>逐题结果（{{ runDetail.results.length }}）</h3>
          <div v-if="runDetail.results.length === 0" class="evaluation-muted">任务完成后会显示逐题回答、引用与判定。</div>
          <article v-for="result in runDetail.results" :key="result.case_id" class="evaluation-result">
            <div class="evaluation-result-heading">
              <span class="evaluation-result-chevron" aria-hidden="true">›</span>
              <strong>用例 #{{ result.case_id }}</strong>
              <span>·</span>
              <span :class="result.status === 'FAILED' ? 'evaluation-failed' : 'evaluation-completed'">
                {{ result.status === 'FAILED' ? '单题失败' : '已评估' }}
              </span>
              <span v-if="result.latency_ms !== null">· {{ result.latency_ms }} ms</span>
            </div>
            <p v-if="result.error_message" class="evaluation-error">{{ result.error_message }}</p>
            <MarkdownContent
              v-if="result.answer"
              class="evaluation-answer"
              :content="result.answer"
            />
            <div class="evaluation-result-flags">
              <span v-if="result.answer_mode">回答模式：<b>{{ formatAnswerMode(result.answer_mode) }}</b></span>
              <span v-if="result.evidence_decision">证据判断：<b>{{ formatEvidenceDecision(result.evidence_decision) }}</b></span>
              <span>正确：<b>{{ formatFlag(result.correct) }}</b></span>
              <span>拒答：<b>{{ formatFlag(result.refused) }}</b></span>
              <span>最终引用命中：<b>{{ formatFlag(result.source_hit) }}</b></span>
              <span>检索命中：<b>{{ formatFlag(result.retrieval_hit) }}</b></span>
              <span v-if="result.context_retained !== null">上下文保留：<b>{{ formatFlag(result.context_retained) }}</b></span>
              <span>多轮通过：<b>{{ formatFlag(result.multi_turn_correct) }}</b></span>
              <span>合规提示：<b>{{ formatFlag(result.compliance_hit) }}</b></span>
            </div>
            <details v-if="(result.turn_diagnostics ?? []).length" class="evaluation-turn-diagnostics">
              <summary>检索与证据诊断（{{ result.turn_diagnostics?.length }} 轮）</summary>
              <div v-for="turn in result.turn_diagnostics ?? []" :key="turn.turn_no" class="evaluation-turn-diagnostic">
                <div class="evaluation-turn-heading">
                  <strong>Turn {{ turn.turn_no }}</strong>
                  <span>{{ turn.retrieval_ms }} ms</span>
                  <span>回答模式：{{ formatAnswerMode(turn.answer_mode) }}</span>
                  <span>证据：{{ formatEvidenceDecision(turn.evidence_decision) }}</span>
                </div>
                <p><b>原始问题：</b>{{ turn.question }}</p>
                <p><b>独立检索问题：</b>{{ turn.rewritten_question }}</p>
                <p v-if="turn.evidence_reason"><b>证据理由：</b>{{ turn.evidence_reason }}</p>
                <div class="evaluation-turn-points">
                  <span><b>Evidence supported：</b>{{ (turn.supported_points ?? []).join('；') || '无' }}</span>
                  <span><b>Evidence missing：</b>{{ (turn.missing_points ?? []).join('；') || '无' }}</span>
                </div>
                <details v-if="turn.retrieved_sources.length" class="evaluation-citations">
                  <summary>Retrieved Sources（{{ turn.retrieved_sources.length }}）</summary>
                  <ul>
                    <li v-for="source in turn.retrieved_sources" :key="`${turn.turn_no}-retrieved-${source.chunk_id}`">
                      {{ source.file_name }} · 第 {{ source.chunk_no }} 段 · {{ source.evidence_origin === 'NEIGHBOR' ? '邻接扩展' : '直接召回' }}
                      <span>{{ source.content }}</span>
                    </li>
                  </ul>
                </details>
              </div>
            </details>
            <details
              v-if="result.status === 'COMPLETED' && result.correct !== null"
              class="evaluation-judge-diagnostics"
            >
              <summary>评测诊断</summary>
              <div class="evaluation-judge-diagnostic-body">
                <p class="evaluation-judge-conclusion">
                  评测结论：<b :class="result.correct ? 'evaluation-completed' : 'evaluation-failed'">
                    {{ result.correct ? '通过' : '未通过' }}
                  </b>
                </p>
                <template v-if="!result.correct">
                  <div class="evaluation-diagnostic-group">
                    <strong>已覆盖要点</strong>
                    <ul v-if="(result.matched_points ?? []).length">
                      <li v-for="point in result.matched_points ?? []" :key="`matched-${point}`">✓ {{ point }}</li>
                    </ul>
                    <p v-else class="evaluation-diagnostic-empty">无</p>
                  </div>
                  <div class="evaluation-diagnostic-group">
                    <strong>缺失要点</strong>
                    <ul v-if="(result.missing_points ?? []).length">
                      <li v-for="point in result.missing_points ?? []" :key="`missing-${point}`">✗ {{ point }}</li>
                    </ul>
                    <p v-else class="evaluation-diagnostic-empty">无</p>
                  </div>
                  <div class="evaluation-diagnostic-group">
                    <strong>冲突内容</strong>
                    <ul v-if="(result.contradictions ?? []).length">
                      <li v-for="point in result.contradictions ?? []" :key="`contradiction-${point}`">! {{ point }}</li>
                    </ul>
                    <p v-else class="evaluation-diagnostic-empty">无</p>
                  </div>
                  <p class="evaluation-judge-reason">评测原因：{{ result.judge_reason || '未记录' }}</p>
                </template>
              </div>
            </details>
            <details v-if="result.citations.length" class="evaluation-citations">
              <summary>最终引用来源（{{ result.citations.length }}）</summary>
              <ul>
                <li v-for="citation in result.citations" :key="citation.chunk_id">
                  {{ citation.file_name }} · 第 {{ citation.chunk_no }} 段
                  <span>{{ citation.content }}</span>
                </li>
              </ul>
            </details>
          </article>
        </div>
      </template>
    </section>
  </section>
</template>

<style scoped>
.evaluation-page {
  display: grid;
  width: min(100%, 1100px);
  margin: 0 auto;
  gap: 12px;
  color: #263548;
}

.evaluation-count {
  display: flex;
  align-items: baseline;
  gap: 7px;
  padding-bottom: 5px;
  color: #526079;
  font-size: 18px;
  white-space: nowrap;
}

.evaluation-count strong {
  color: #155a42;
  font-size: 34px;
  line-height: 1;
}

.evaluation-panel {
  min-width: 0;
  padding: 14px 14px 12px;
  background: #fff;
  border: 1px solid #e1e9e4;
  border-radius: 9px;
}

.evaluation-section-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 20px;
  margin-bottom: 11px;
}

.evaluation-section-heading h2 {
  margin: 0;
  color: #20352d;
  font-size: 18px;
  font-weight: 700;
  line-height: 1.3;
}

.evaluation-section-heading p,
.evaluation-detail-run-name {
  margin: 5px 0 0;
  color: #758195;
  font-size: 12px;
  line-height: 1.45;
}

.evaluation-filters {
  display: grid;
  grid-template-columns: minmax(210px, 1.5fr) repeat(4, minmax(120px, 1fr)) 90px;
  gap: 8px;
}

.evaluation-filter-control {
  display: flex;
  min-width: 0;
  min-height: 38px;
  align-items: center;
  gap: 8px;
  padding: 0 11px;
  color: #667286;
  background: #fff;
  border: 1px solid #d8e0da;
  border-radius: 7px;
  font-size: 12px;
}

.evaluation-filter-control svg {
  width: 17px;
  height: 17px;
  flex: 0 0 auto;
  color: #34455d;
}

.evaluation-filter-control input,
.evaluation-filter-control select {
  width: 100%;
  min-width: 0;
  min-height: 36px;
  padding: 0;
  color: #354257;
  background: transparent;
  border: 0;
  outline: 0;
  font: inherit;
}

.evaluation-filter-control input::placeholder {
  color: #9aa5b4;
}

.evaluation-filter-input,
.evaluation-filter-select {
  width: 100%;
  min-width: 0;
}

.evaluation-filter-input :deep(.el-input__wrapper),
.evaluation-filter-select :deep(.el-select__wrapper) {
  min-height: 36px;
  padding: 0;
  background: transparent;
  border: 0;
  box-shadow: none !important;
  font: inherit;
}

.evaluation-filter-input :deep(.el-input__inner),
.evaluation-filter-select :deep(.el-select__placeholder),
.evaluation-filter-select :deep(.el-select__selected-item) {
  color: #354257;
  font: inherit;
}

.evaluation-filter-input :deep(.el-input__inner::placeholder) {
  color: #9aa5b4;
}

/* 筛选栏输入框和下拉框使用自定义边框反馈；
   覆盖 Element Plus 全局内阴影焦点描边，避免悬停或聚焦时出现双层描边。 */
.evaluation-filter-input:hover :deep(.el-input__wrapper),
.evaluation-filter-select:hover :deep(.el-select__wrapper),
.evaluation-filter-input :deep(.el-input__wrapper:hover),
.evaluation-filter-select :deep(.el-select__wrapper:hover),
.evaluation-filter-input :deep(.el-input__wrapper.is-focus),
.evaluation-filter-select :deep(.el-select__wrapper.is-focused),
.evaluation-filter-input.is-focus :deep(.el-input__wrapper),
.evaluation-filter-select.is-focused :deep(.el-select__wrapper) {
  border-color: transparent;
  box-shadow: none !important;
}

.evaluation-filter-input :deep(.el-input__prefix-inner) {
  color: #34455d;
}

.evaluation-primary {
  min-height: 38px;
  padding: 0 16px;
  color: #fff;
  background: #126047;
  border: 1px solid #126047;
  border-radius: 7px;
  cursor: pointer;
  font-size: 12px;
  font-weight: 650;
  transition: background 0.15s ease, border-color 0.15s ease;
}

.evaluation-primary.el-button {
  height: 38px;
  box-shadow: none;
}

/* 固定分区标题右侧操作按钮的宽度，使其与筛选提交按钮保持一致；
   在窄屏换行时也维持标题区操作列的对齐关系。 */
.evaluation-section-heading .evaluation-primary {
  width: 90px;
  min-width: 90px;
}

.evaluation-primary:hover:not(:disabled) {
  background: #0f523c;
  border-color: #0f523c;
}

.evaluation-primary:disabled {
  cursor: wait;
  opacity: 0.65;
}

.evaluation-table-wrap {
  margin-top: 10px;
  overflow-x: auto;
}

.evaluation-table {
  width: 100%;
  min-width: 900px;
  border-spacing: 0;
  text-align: left;
}

.evaluation-table th,
.evaluation-table td {
  padding: 9px 10px;
  border-bottom: 1px solid #edf0f1;
  vertical-align: middle;
}

.evaluation-table th {
  color: #68758a;
  background: #f4f6f5;
  font-size: 12px;
  font-weight: 650;
  line-height: 1.35;
  white-space: nowrap;
}

.evaluation-table th:first-child {
  border-radius: 6px 0 0 6px;
}

.evaluation-table th:last-child {
  border-radius: 0 6px 6px 0;
}

.evaluation-table td {
  color: #354257;
  font-size: 12px;
  line-height: 1.45;
}

.evaluation-case-table th:nth-child(1),
.evaluation-case-table td:nth-child(1) { width: 42px; }
.evaluation-case-table th:nth-child(2),
.evaluation-case-table td:nth-child(2) { width: 58px; }
.evaluation-case-table th:nth-child(3),
.evaluation-case-table td:nth-child(3) { width: 150px; }
.evaluation-case-table th:nth-child(4),
.evaluation-case-table td:nth-child(4) { width: 27%; }
.evaluation-case-table th:nth-child(5),
.evaluation-case-table td:nth-child(5) { width: 72px; }
.evaluation-case-table th:nth-child(6),
.evaluation-case-table td:nth-child(6) { width: 76px; }
.evaluation-case-table th:nth-child(7),
.evaluation-case-table td:nth-child(7) { width: 62px; }
.evaluation-case-table th:nth-child(8),
.evaluation-case-table td:nth-child(8) { width: 82px; }
.evaluation-case-table th:nth-child(9),
.evaluation-case-table td:nth-child(9) { width: 82px; }
.evaluation-case-table th:nth-child(10),
.evaluation-case-table td:nth-child(10) { width: 125px; }

.evaluation-select-cell {
  text-align: center;
}

.evaluation-table input[type='checkbox'],
.evaluation-table input[type='radio'] {
  width: 17px;
  height: 17px;
  margin: 0;
  accent-color: #146348;
  cursor: pointer;
}

.evaluation-case-id,
.evaluation-case-rounds {
  color: #536077;
  white-space: nowrap;
}

.evaluation-case-topic {
  font-weight: 600;
}

.evaluation-case-question {
  color: #354257;
  overflow-wrap: anywhere;
}

.evaluation-case-actions {
  white-space: nowrap;
}

.evaluation-case-actions .el-button {
  padding: 4px 5px;
}

.evaluation-empty {
  padding: 24px !important;
  color: #8a95a5 !important;
  text-align: center;
}

.evaluation-pagination {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  flex-wrap: wrap;
  gap: 9px;
  margin-top: 10px;
  color: #758195;
  font-size: 12px;
}

.evaluation-pagination label {
  display: inline-flex;
  align-items: center;
  gap: 7px;
}

.evaluation-pagination select {
  min-height: 30px;
  padding: 0 8px;
  color: #354257;
  background: #fff;
  border: 1px solid #d8e0da;
  border-radius: 6px;
  font: inherit;
}

.evaluation-pagination button {
  min-height: 30px;
  padding: 0 11px;
  color: #536077;
  background: #fff;
  border: 1px solid #d8e0da;
  border-radius: 6px;
  cursor: pointer;
  font: inherit;
}

.evaluation-pagination button:hover:not(:disabled) {
  color: #185b44;
  border-color: #a9c8b9;
}

.evaluation-pagination button:disabled {
  color: #b4bbc5;
  cursor: default;
}

.evaluation-page-size {
  width: 68px;
}

.evaluation-page-size :deep(.el-select__wrapper) {
  min-height: 30px;
  padding: 0 8px;
  color: #354257;
  border: 1px solid #d8e0da;
  border-radius: 6px;
  box-shadow: none;
  font: inherit;
}

.evaluation-create-layout {
  display: grid;
  grid-template-columns: 180px minmax(0, 1fr);
  align-items: center;
  gap: 14px;
}

.evaluation-create-layout .evaluation-section-heading {
  margin: 0;
}

.evaluation-create-form {
  display: grid;
  grid-template-columns: minmax(170px, 1fr) minmax(140px, 170px) minmax(180px, 210px) 126px;
  align-items: center;
  gap: 10px;
}

.evaluation-create-form label {
  display: flex;
  min-width: 0;
  align-items: center;
  gap: 8px;
  color: #758195;
  font-size: 12px;
  white-space: nowrap;
}

.evaluation-create-form input,
.evaluation-create-form select {
  width: 100%;
  min-width: 0;
  min-height: 38px;
  padding: 0 10px;
  color: #354257;
  background: #fff;
  border: 1px solid #d8e0da;
  border-radius: 7px;
  outline: 0;
  font: inherit;
}

.evaluation-create-form input::placeholder {
  color: #a0a9b5;
}

.evaluation-create-input,
.evaluation-create-select {
  flex: 1 1 auto;
  min-width: 0;
}

.evaluation-create-input :deep(.el-input__wrapper),
.evaluation-create-select :deep(.el-select__wrapper) {
  min-height: 38px;
  padding: 0 10px;
  color: #354257;
  border: 1px solid #d8e0da;
  border-radius: 7px;
  box-shadow: none;
  font: inherit;
}

.evaluation-create-input :deep(.el-input__inner),
.evaluation-create-select :deep(.el-select__placeholder),
.evaluation-create-select :deep(.el-select__selected-item) {
  color: #354257;
  font: inherit;
}

.evaluation-case-form {
  display: grid;
  gap: 18px;
}

.evaluation-case-form > label,
.evaluation-switch-row {
  display: grid;
  gap: 6px;
  color: #3d5c4d;
  font-size: 13px;
}

.evaluation-case-form > label > span,
.evaluation-switch-row > span {
  color: #315b4b;
  font-size: 14px;
  font-weight: 700;
}

.evaluation-case-form .el-select,
.evaluation-case-form .el-input {
  width: 100%;
}

:deep(.evaluation-case-dialog.el-dialog) {
  width: min(780px, calc(100vw - 32px)) !important;
  max-width: calc(100vw - 32px);
  max-height: calc(100vh - 48px);
  margin-top: 24px;
  overflow: hidden;
  border: 1px solid #dbe9e1;
  border-radius: 14px;
  background: #fff;
  box-shadow: 0 24px 64px rgb(31 68 54 / 18%), 0 3px 12px rgb(31 68 54 / 8%);
}

:global(.evaluation-case-overlay) {
  background: rgb(31 68 54 / 42%);
}

:deep(.evaluation-case-dialog .el-dialog__header) {
  margin-right: 0;
  padding: 22px 28px 15px;
  background: #fbfdfc;
  border-bottom: 1px solid #edf3ef;
}

:deep(.evaluation-case-dialog .el-dialog__title) {
  color: #155a42;
  font-family: "Songti SC", "STSong", "Noto Serif CJK SC", serif;
  font-size: 25px;
  font-weight: 700;
  letter-spacing: -0.03em;
}

:deep(.evaluation-case-dialog .el-dialog__headerbtn) {
  top: 18px;
  right: 20px;
  width: 34px;
  height: 34px;
  border-radius: 8px;
}

:deep(.evaluation-case-dialog .el-dialog__headerbtn:hover) {
  background: #edf7f1;
}

:deep(.evaluation-case-dialog .el-dialog__close) {
  color: #72857b;
}

:deep(.evaluation-case-dialog .el-dialog__headerbtn:hover .el-dialog__close) {
  color: #185b44;
}

:deep(.evaluation-case-dialog .el-dialog__body) {
  max-height: calc(100vh - 155px);
  padding: 22px 28px 26px;
  overflow-y: auto;
  scrollbar-color: #b8d3c4 transparent;
  scrollbar-width: thin;
}

.evaluation-case-form :deep(.el-input__wrapper),
.evaluation-case-form :deep(.el-select__wrapper) {
  min-height: 44px;
  padding: 2px 13px;
  background: #fbfdfc;
  border-radius: 9px;
  box-shadow: 0 0 0 1px #d8e5de inset;
  transition: box-shadow 160ms ease, background-color 160ms ease;
}

.evaluation-case-form :deep(.el-input__wrapper:hover),
.evaluation-case-form :deep(.el-select__wrapper:hover) {
  background: #fff;
  box-shadow: 0 0 0 1px #a9c8b9 inset;
}

.evaluation-case-form :deep(.el-input__wrapper.is-focus),
.evaluation-case-form :deep(.el-select__wrapper.is-focused) {
  background: #fff;
  box-shadow: 0 0 0 2px #b9d9c8 inset !important;
}

.evaluation-case-form :deep(.el-input__inner),
.evaluation-case-form :deep(.el-select__selected-item),
.evaluation-case-form :deep(.el-select__placeholder) {
  color: #354257;
  font-size: 14px;
}

.evaluation-case-form :deep(.el-input__inner::placeholder),
.evaluation-case-form :deep(.el-textarea__inner::placeholder) {
  color: #9aa9a1;
}

.evaluation-case-form :deep(.el-textarea__inner) {
  min-height: 86px;
  padding: 12px 13px;
  color: #354257;
  background: #fbfdfc;
  border: 0;
  border-radius: 9px;
  box-shadow: 0 0 0 1px #d8e5de inset;
  font-size: 14px;
  line-height: 1.6;
  resize: vertical;
  transition: box-shadow 160ms ease, background-color 160ms ease;
}

.evaluation-case-form :deep(.el-textarea__inner:hover) {
  background: #fff;
  box-shadow: 0 0 0 1px #a9c8b9 inset;
}

.evaluation-case-form :deep(.el-textarea__inner:focus) {
  background: #fff;
  box-shadow: 0 0 0 2px #b9d9c8 inset;
}

.evaluation-case-form :deep(.el-input__count),
.evaluation-case-form :deep(.el-input__count-inner) {
  color: #93a39a;
  background: transparent;
}

.evaluation-form-array {
  display: grid;
  gap: 8px;
}

.evaluation-form-section {
  gap: 10px;
  padding: 14px 16px 16px;
  background: #f8fbf9;
  border: 1px solid #e2eee7;
  border-radius: 10px;
}

.evaluation-form-array-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding-bottom: 7px;
  color: #28654e;
  border-bottom: 1px solid #e5efe9;
  font-size: 14px;
  font-weight: 700;
}

.evaluation-form-array-heading .el-button {
  height: 30px;
  padding: 0 9px;
  color: #1c6b50;
  border-radius: 7px;
  font-size: 13px;
  font-weight: 650;
}

.evaluation-form-array-heading .el-button:hover:not(.is-disabled) {
  color: #155a42;
  background: #eaf5ef;
}

.evaluation-form-array-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  align-items: start;
  gap: 8px;
}

.evaluation-source-row {
  grid-template-columns: minmax(0, 1fr) 140px auto;
}

.evaluation-form-array-row > .el-button {
  min-width: 44px;
  margin-top: 8px;
  padding: 4px 7px;
  color: #a8615a;
  border-radius: 7px;
  font-size: 13px;
  font-weight: 650;
}

.evaluation-form-array-row > .el-button:hover:not(.is-disabled) {
  color: #914a44;
  background: #fff0ee;
}

.evaluation-switch-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  min-height: 52px;
  padding: 9px 16px;
  background: #f8fbf9;
  border: 1px solid #e2eee7;
  border-radius: 10px;
}

.evaluation-switch-row :deep(.el-switch) {
  --el-switch-on-color: #126047;
  --el-switch-off-color: #cbd9d1;
}

.evaluation-dialog-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  padding-top: 18px;
  border-top: 1px solid #e8f0eb;
}

.evaluation-dialog-actions .el-button {
  min-width: 88px;
  height: 40px;
  padding: 0 18px;
  border-radius: 8px;
  font-size: 14px;
  font-weight: 650;
}

.evaluation-dialog-actions .el-button:not(.el-button--primary) {
  color: #466056;
  background: #fff;
  border-color: #d6e3dc;
}

.evaluation-dialog-actions .el-button:not(.el-button--primary):hover {
  color: #185b44;
  background: #f4faf6;
  border-color: #9fc4af;
}

.evaluation-dialog-actions .el-button.el-button--primary {
  color: #fff;
  background: #126047;
  border-color: #126047;
  box-shadow: 0 5px 12px rgb(18 96 71 / 16%);
}

.evaluation-dialog-actions .el-button.el-button--primary:hover:not(.is-disabled) {
  background: #0f523c;
  border-color: #0f523c;
}

.evaluation-create-input :deep(.el-input__inner::placeholder) {
  color: #a0a9b5;
}

.evaluation-run-table {
  min-width: 700px;
}

.evaluation-run-table th:nth-child(1),
.evaluation-run-table td:nth-child(1) { width: 42px; }
.evaluation-run-table th:nth-child(2),
.evaluation-run-table td:nth-child(2) { width: 36%; }
.evaluation-run-table th:nth-child(3),
.evaluation-run-table td:nth-child(3) { width: 16%; }
.evaluation-run-table th:nth-child(4),
.evaluation-run-table td:nth-child(4) { width: 16%; }
.evaluation-run-table th:nth-child(5),
.evaluation-run-table td:nth-child(5) { width: 20%; }
.evaluation-run-table th:nth-child(6),
.evaluation-run-table td:nth-child(6) { width: 78px; }

.evaluation-run-table tbody tr {
  cursor: pointer;
}

.evaluation-run-table tbody tr:hover,
.evaluation-run-table tbody tr.evaluation-run-selected {
  background: #f0f7f4;
}

.evaluation-run-toggle.el-button {
  width: 26px;
  height: 26px;
  color: #536077;
}

.evaluation-run-toggle.el-button:hover {
  color: #185b44;
  background: #e4f0eb;
}

.evaluation-run-toggle :deep(svg) {
  transition: transform 160ms ease;
}

.evaluation-run-toggle[aria-expanded='true'] :deep(svg) {
  transform: rotate(90deg);
}

.evaluation-run-delete.el-button {
  padding: 5px 8px;
}

.evaluation-link {
  padding: 0;
  color: #185b44;
  background: none;
  border: 0;
  cursor: pointer;
  font: inherit;
  font-weight: 650;
  text-align: left;
}

.evaluation-status-inline {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  white-space: nowrap;
}

.evaluation-status-inline i {
  display: block;
  width: 9px;
  height: 9px;
  flex: 0 0 auto;
  border-radius: 50%;
  background: currentColor;
}

.evaluation-status-completed,
.evaluation-completed { color: #168052; }
.evaluation-status-running { color: #3175d6; }
.evaluation-status-pending { color: #a47b22; }
.evaluation-status-failed,
.evaluation-failed { color: #c45656; }

.evaluation-detail-panel {
  padding-top: 16px;
}

.evaluation-detail-heading {
  align-items: flex-start;
}

.evaluation-detail-heading .evaluation-status {
  padding-top: 4px;
}

.evaluation-status {
  font-size: 13px;
  font-weight: 650;
  white-space: nowrap;
}

.evaluation-progress {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr) auto;
  align-items: center;
  gap: 12px;
  padding: 10px 0 13px;
  border-bottom: 1px solid #e7ece9;
  color: #758195;
  font-size: 12px;
}

.evaluation-progress-status,
.evaluation-progress-track {
  display: flex;
  align-items: center;
  gap: 9px;
  white-space: nowrap;
}

.evaluation-progress-track {
  min-width: 0;
}

.evaluation-progress-track progress {
  width: min(100%, 520px);
  min-width: 90px;
  height: 9px;
  margin-left: 3px;
  accent-color: #126047;
}

.evaluation-progress-track strong {
  color: #354257;
  font-weight: 500;
}

.evaluation-progress-percent {
  color: #718096;
  white-space: nowrap;
}

.evaluation-metrics {
  display: grid;
  grid-template-columns: repeat(5, minmax(0, 1fr));
  margin-top: 14px;
}

.evaluation-metrics div {
  display: grid;
  gap: 7px;
  padding: 0 14px;
  border-left: 1px solid #dfe6e2;
  text-align: center;
}

.evaluation-metrics div:first-child {
  border-left: 0;
}

.evaluation-metrics span {
  color: #758195;
  font-size: 11px;
}

.evaluation-metrics strong {
  color: #155a42;
  font-size: 19px;
  line-height: 1.1;
}

.evaluation-metric-note {
  margin: 10px 0 0;
  color: #7d899b;
  font-size: 12px;
  line-height: 1.6;
}

.evaluation-results {
  margin-top: 18px;
}

.evaluation-results h3 {
  margin: 0 0 9px;
  color: #20352d;
  font-size: 15px;
}

.evaluation-result {
  display: grid;
  gap: 9px;
  padding: 10px 10px 11px;
  border: 1px solid #e0e8e3;
  border-radius: 7px;
}

.evaluation-result-heading,
.evaluation-result-flags {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 7px 12px;
  color: #667286;
  font-size: 12px;
}

.evaluation-result-heading strong {
  color: #354257;
  font-size: 12px;
}

.evaluation-result-chevron {
  color: #526079;
  font-size: 21px;
  line-height: 0.7;
}

.evaluation-result-flags b {
  color: #168052;
  font-weight: 650;
}

.evaluation-answer {
  margin: 0;
  color: #4e5a6b;
  font-size: 12px;
  line-height: 1.65;
}

.evaluation-judge-diagnostics {
  color: #185b44;
  font-size: 12px;
}

.evaluation-judge-diagnostic-body {
  display: grid;
  gap: 9px;
  margin-top: 8px;
  padding: 9px 10px;
  color: #4e5a6b;
  background: #f7faf8;
  border: 1px solid #e0e8e3;
  border-radius: 6px;
}

.evaluation-judge-conclusion,
.evaluation-judge-reason,
.evaluation-diagnostic-group {
  margin: 0;
}

.evaluation-diagnostic-group strong {
  color: #354257;
  font-weight: 650;
}

.evaluation-diagnostic-group ul {
  display: grid;
  gap: 4px;
  margin: 5px 0 0;
  padding-left: 18px;
}

.evaluation-diagnostic-empty {
  margin: 5px 0 0;
  color: #8a95a5;
}

.evaluation-citations {
  color: #185b44;
  font-size: 12px;
}

.evaluation-citations ul {
  display: grid;
  gap: 7px;
  margin: 8px 0 0;
  padding-left: 18px;
  color: #4e5a6b;
}

.evaluation-citations li span {
  display: block;
  margin-top: 3px;
  color: #758195;
  line-height: 1.55;
}

.evaluation-turn-diagnostics {
  color: #185b44;
  font-size: 12px;
}

.evaluation-turn-diagnostic {
  display: grid;
  gap: 6px;
  margin-top: 8px;
  padding: 9px 10px;
  color: #4e5a6b;
  background: #fbfdfc;
  border: 1px solid #e0e8e3;
  border-radius: 6px;
}

.evaluation-turn-heading,
.evaluation-turn-diagnostic p,
.evaluation-turn-points {
  display: flex;
  flex-wrap: wrap;
  gap: 5px 12px;
  margin: 0;
  line-height: 1.55;
}

.evaluation-turn-heading {
  color: #315b4b;
}

.evaluation-turn-heading span {
  color: #758195;
}

.evaluation-turn-points {
  display: grid;
  gap: 4px;
}

.evaluation-error {
  margin: 9px 0;
  padding: 8px 10px;
  color: #a63c3c;
  background: #fff4f2;
  border: 1px solid #f0d5d1;
  border-radius: 7px;
  font-size: 12px;
  line-height: 1.5;
}

.evaluation-muted {
  margin: 10px 0 0;
  color: #8a95a5;
  font-size: 12px;
}

@media (max-width: 820px) {
  .evaluation-filters {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .evaluation-section-heading .evaluation-primary {
    width: 100px;
    min-width: 100px;
  }

  .evaluation-filters .evaluation-primary {
    grid-column: 1 / -1;
    justify-self: end;
    min-width: 100px;
  }

  .evaluation-create-layout {
    grid-template-columns: 1fr;
    gap: 10px;
  }

  .evaluation-create-form {
    grid-template-columns: minmax(180px, 1fr) minmax(140px, 1fr);
  }

  .evaluation-create-form .evaluation-primary {
    grid-column: 1 / -1;
    justify-self: end;
  }
}

@media (max-width: 620px) {
  :deep(.evaluation-case-dialog.el-dialog) {
    width: calc(100vw - 20px) !important;
    max-width: calc(100vw - 20px);
    margin-top: 10px;
  }

  :deep(.evaluation-case-dialog .el-dialog__header) {
    padding: 18px 18px 12px;
  }

  :deep(.evaluation-case-dialog .el-dialog__body) {
    max-height: calc(100vh - 112px);
    padding: 18px 18px 20px;
  }

  :deep(.evaluation-case-dialog .el-dialog__title) {
    font-size: 21px;
  }

  .evaluation-form-array-row,
  .evaluation-source-row {
    grid-template-columns: 1fr;
  }

  .evaluation-form-array-row > .el-button {
    justify-self: end;
    margin-top: 0;
  }

  .evaluation-page {
    width: 100%;
    gap: 10px;
  }

  .evaluation-count {
    padding-bottom: 0;
    font-size: 15px;
  }

  .evaluation-count strong {
    font-size: 29px;
  }

  .evaluation-panel {
    padding: 12px 10px 10px;
  }

  .evaluation-filters,
  .evaluation-create-form {
    grid-template-columns: 1fr;
  }

  .evaluation-section-heading {
    align-items: stretch;
    flex-direction: column;
  }

  .evaluation-section-heading .evaluation-primary {
    width: 100%;
    min-width: 0;
  }

  .evaluation-filters .evaluation-primary {
    width: 100%;
    grid-column: auto;
  }

  .evaluation-create-form label {
    align-items: stretch;
    flex-direction: column;
    gap: 5px;
    white-space: normal;
  }

  .evaluation-create-form .evaluation-primary {
    width: 100%;
  }

  .evaluation-progress {
    grid-template-columns: 1fr auto;
  }

  .evaluation-progress-track {
    grid-column: 1 / -1;
    grid-row: 2;
  }

  .evaluation-metrics {
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 13px 0;
  }

  .evaluation-metrics div:nth-child(odd) {
    border-left: 0;
  }

  .evaluation-results {
    margin-top: 15px;
  }

  .evaluation-form-array-row,
  .evaluation-source-row {
    grid-template-columns: 1fr;
  }

  .evaluation-dialog-actions {
    justify-content: stretch;
  }

  .evaluation-dialog-actions .el-button {
    flex: 1;
  }
}
</style>
