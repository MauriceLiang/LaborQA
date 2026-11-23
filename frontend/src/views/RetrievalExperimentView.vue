<!-- 文件作用：组织检索策略配置、实验执行和结果比较页面。 -->
<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { ElButton, ElDialog, ElInput, ElInputNumber, ElMessage, ElMessageBox, ElOption, ElSelect, ElSwitch } from 'element-plus'
import { WarningFilled } from '@element-plus/icons-vue'

import {
  createExperiment,
  copyExperiment,
  createRetrievalStrategy,
  deleteExperiment,
  deleteRetrievalStrategy,
  archiveRetrievalExperiment,
  archiveRetrievalStrategy,
  exportRetrievalExperiment,
  getExperiment,
  getExperiments,
  listRetrievalStrategies,
  listRetrievalStrategyVersions,
  previewRetrieval,
  restoreRetrievalExperiment,
  restoreRetrievalStrategy,
  restoreRetrievalStrategyVersion,
  setRetrievalStrategyActive,
  updateRetrievalStrategy,
  type ExperimentConfig,
  type ExperimentConfigResult,
  type ExperimentDetail,
  type ExperimentCaseScope,
  type ExperimentStatus,
  type ExperimentSummary,
  type RetrievalPreview,
  type RetrievalStrategy,
  type RetrievalStrategyPayload,
  type RetrievalStrategyVersion,
} from '@/api/experiments'
import { getErrorMessage } from '@/api/http'
import { getEvaluationCases, type EvaluationCase } from '@/api/evaluations'
import MarkdownContent from '@/components/chat/MarkdownContent.vue'
import { usePreferencesStore } from '@/stores/preferences'
import { aiModeLabel } from '@/types/preferences'

// 提供当前模型及回答偏好设置的状态仓库。
const preferencesStore = usePreferencesStore()
// 轻量检索策略共享的默认参数。
const lightweightRagDefaults = {
  candidate_k: 20,
  lightweight_rerank_enabled: true,
  neighbor_expand_top_n: 3,
  neighbor_radius: 1,
  evidence_limit: 10,
}

// 用于对照实验的内置检索参数组合。
const experimentGroups: Array<{ label: string; config: ExperimentConfig }> = [
  { label: 'A', config: { ...lightweightRagDefaults, chunk_size: 600, chunk_overlap: 100, top_k: 5, rerank_enabled: false, rerank_top_n: 5, score_threshold: 0.35 } },
  { label: 'B', config: { ...lightweightRagDefaults, chunk_size: 400, chunk_overlap: 100, top_k: 5, rerank_enabled: false, rerank_top_n: 5, score_threshold: 0.35 } },
  { label: 'C', config: { ...lightweightRagDefaults, chunk_size: 800, chunk_overlap: 100, top_k: 5, rerank_enabled: false, rerank_top_n: 5, score_threshold: 0.35 } },
  { label: 'D', config: { ...lightweightRagDefaults, chunk_size: 600, chunk_overlap: 100, top_k: 3, rerank_enabled: false, rerank_top_n: 3, score_threshold: 0.35 } },
  { label: 'E', config: { ...lightweightRagDefaults, chunk_size: 600, chunk_overlap: 100, top_k: 8, rerank_enabled: false, rerank_top_n: 8, score_threshold: 0.35 } },
  { label: 'F', config: { ...lightweightRagDefaults, chunk_size: 600, chunk_overlap: 100, top_k: 5, rerank_enabled: true, rerank_top_n: 5, score_threshold: 0.35 } },
]

/** 复制检索实验配置，避免编辑时修改共享默认值。 */
// 参数 config：检索实验参数配置。
function cloneConfig(config: ExperimentConfig): ExperimentConfig {
  return { ...config }
}

// 当前页检索实验记录。
const experiments = ref<ExperimentSummary[]>([])
// 检索实验列表当前页码。
const page = ref(1)
// 检索实验列表每页数量。
const pageSize = 10
// 符合条件的实验总数。
const total = ref(0)
// 实验列表总页数。
const pages = ref(0)
// 实验列表请求是否正在执行。
const loadingList = ref(false)
// 实验列表加载失败信息。
const listError = ref('')
// 新建实验名称输入值。
const experimentName = ref('')
// 新建实验使用的回答风格。
const answerStyle = ref<'' | 'plain' | 'legal'>('')
// 实验用例选择范围。
const caseScope = ref<ExperimentCaseScope>('BUILTIN_BASELINE')
// 当前可选的评测用例列表。
const evaluationCases = ref<EvaluationCase[]>([])
// 用户自建评测用例总数。
const evaluationCaseTotal = ref(0)
// 内置基准评测用例总数。
const builtinCaseTotal = ref(0)
// 当前实验选中的评测用例 ID。
const selectedCaseIds = ref<number[]>([])
// 评测用例列表是否正在加载。
const caseLoading = ref(false)
// 评测用例加载失败信息。
const caseError = ref('')
// 实验创建请求是否正在执行。
const creating = ref(false)
// 实验创建失败信息。
const createError = ref('')
// 当前选中的实验 ID。
const selectedId = ref<number>()
// 当前选中实验的详情数据。
const detail = ref<ExperimentDetail>()
// 实验详情请求是否正在执行。
const loadingDetail = ref(false)
// 实验详情加载失败信息。
const detailError = ref('')
// 当前展开的实验配置序号。
const expandedConfigIndex = ref<number>()
// 正在读取详情或需要去重的实验 ID 集合。
const detailRequests = new Set<number>()
// 当前可用的检索策略列表。
const strategies = ref<RetrievalStrategy[]>([])
// 检索策略列表是否正在加载。
const strategyLoading = ref(false)
// 检索策略加载失败信息。
const strategyError = ref('')
// 检索预览当前选中的策略 ID。
const previewStrategyId = ref<number>()
// 本次实验选中的策略 ID 列表。
const experimentStrategyIds = ref<number[]>([])
// 检索策略编辑对话框是否显示。
const strategyEditorVisible = ref(false)
// 当前编辑策略 ID；无值表示新建。
const strategyEditorId = ref<number>()
// 当前编辑流程是否从复制策略开始。
const strategyEditorCopy = ref(false)
// 策略保存请求是否正在执行。
const strategySaving = ref(false)
// 策略编辑或保存错误信息。
const strategyEditorError = ref('')
// 策略历史版本对话框是否显示。
const strategyVersionVisible = ref(false)
// 当前查看历史版本的策略。
const strategyVersionStrategy = ref<RetrievalStrategy>()
// 当前策略的历史版本列表。
const strategyVersions = ref<RetrievalStrategyVersion[]>([])
// 策略历史版本是否正在加载。
const strategyVersionLoading = ref(false)
// 历史版本加载失败信息。
const strategyVersionError = ref('')
// 当前正在恢复的策略版本号。
const restoringStrategyVersion = ref<number>()
// 检索策略编辑表单中的名称、说明和参数。
const strategyDraft = reactive<RetrievalStrategyPayload>({
  name: '',
  description: '',
  config: cloneConfig(experimentGroups[0].config),
})
// 检索预览使用的问题文本。
const previewQuestion = ref('公司拖欠工资，我应该准备什么材料？')
// 检索预览请求是否正在执行。
const previewLoading = ref(false)
// 检索预览失败信息。
const previewError = ref('')
// 最近一次检索预览返回的结果。
const previewResult = ref<RetrievalPreview>()
// 检索预览结果是否折叠。
const previewResultCollapsed = ref(false)
// 检索预览结果区域的 DOM 引用。
const previewPanel = ref<HTMLElement>()
// 当前正在删除的实验 ID。
const deletingExperimentId = ref<number>()
// 当前正在复制的实验 ID。
const copyingExperimentId = ref<number>()
// 当前正在归档的实验 ID。
const archivingExperimentId = ref<number>()
// 当前正在导出的实验 ID。
const exportingExperimentId = ref<number>()
// 当前实验选择请求版本，用于丢弃过期响应。
let selectionVersion = 0
// 实验状态轮询定时器。
let pollTimer: ReturnType<typeof setInterval> | undefined

// 当前选中实验的运行状态。
const status = computed(() => detail.value?.status ?? experiments.value.find((item) => item.id === selectedId.value)?.status)
// 当前选中实验的展示名称。
const selectedName = computed(() => detail.value?.name ?? experiments.value.find((item) => item.id === selectedId.value)?.name ?? '')
// 判断实验状态是否已结束的函数。
const isTerminal = (value: ExperimentStatus | undefined) => value === 'COMPLETED' || value === 'FAILED'
// 按配置序号索引实验结果的映射。
const configResults = computed(() => new Map((detail.value?.config_results ?? []).map((item) => [item.config_index, item])))
// 当前展开配置对应的用例结果列表。
const selectedConfigResults = computed(() => detail.value?.results.filter((item) => item.config_index === expandedConfigIndex.value) ?? [])
// 已启用且未归档的策略列表。
const availableStrategies = computed(() => strategies.value.filter((item) => item.is_active && !item.archived_at))
// 当前选中的预览策略对象。
const previewStrategy = computed(() => availableStrategies.value.find((item) => item.id === previewStrategyId.value))
// 本次实验选择的有效策略对象列表。
const experimentStrategies = computed(() => availableStrategies.value.filter((item) => experimentStrategyIds.value.includes(item.id)))
// 按用例范围计算的实验用例数量。
const experimentCaseCount = computed(() => {
  if (caseScope.value === 'SELECTED') return selectedCaseIds.value.length
  if (caseScope.value === 'BUILTIN_BASELINE') return builtinCaseTotal.value
  return evaluationCaseTotal.value
})
// 预计执行的策略与用例组合总数。
const estimatedRuns = computed(() => experimentStrategies.value.length * experimentCaseCount.value)
// 策略编辑对话框标题。
const strategyEditorTitle = computed(() => {
  if (strategyEditorId.value !== undefined) return '编辑检索策略'
  return strategyEditorCopy.value ? '编辑策略副本' : '新建检索策略'
})
// 根据当前策略参数生成的风险提示。
const strategyDraftWarning = computed(() => {
  if (strategyDraft.config.score_threshold >= 0.8) return '阈值较高，可能过滤掉有用材料。建议先试跑确认。'
  if (strategyDraft.config.top_k >= 12) return 'Top-k 较大，可能增加响应耗时和上下文长度。'
  if (!strategyDraft.config.lightweight_rerank_enabled && !strategyDraft.config.rerank_enabled) return '未启用轻量或模型重排，将按向量检索分排序。'
  if (strategyDraft.config.rerank_enabled) return '旧 CrossEncoder 重排会增加 CPU、内存和响应耗时，4C4G 环境建议关闭。'
  return ''
})
// 实验详情中用于展示的配置分组。
const detailConfigGroups = computed(() => (detail.value?.config_results ?? []).map((result, index) => {
  // 当前配置关联的策略历史快照。
  const snapshot = detail.value?.strategy_snapshots?.[index]
  return {
    label: snapshot
      ? snapshot.name + ' · v' + snapshot.version
      : experimentGroups[index]?.label ?? '配置 ' + (index + 1),
    config: snapshot?.config ?? result.config,
  }
}))
// 根据实验指标生成的结果摘要文本。
const experimentConclusion = computed(() => {
  // 当前实验详情对象。
  const current = detail.value
  // 当前实验各配置的汇总结果。
  const results = current?.config_results ?? []
  if (!current || current.status !== 'COMPLETED' || results.length === 0) return []
  // 本次实验参与统计的用例数量。
  const caseCount = current.case_count || Math.round(current.progress_total / results.length)
  // 用于拼接实验结论的文本行。
  const lines = ['本次实验共比较 ' + results.length + ' 条策略、' + caseCount + ' 条测试问题。']
  // 引用命中率最高的配置结果。
  const highestCitation = results.reduce((best, item) => (
    item.citation_hit_rate !== null
      && (best === undefined || best.citation_hit_rate === null || item.citation_hit_rate > best.citation_hit_rate)
      ? item
      : best
  ), undefined as ExperimentConfigResult | undefined)
  // 准确率最高的配置结果。
  const highestAccuracy = results.reduce((best, item) => (
    item.accuracy !== null
      && (best === undefined || best.accuracy === null || item.accuracy > best.accuracy)
      ? item
      : best
  ), undefined as ExperimentConfigResult | undefined)
  // 耗时最短的配置结果。
  const fastest = results.reduce((best, item) => (
    item.avg_retrieval_ms !== null
      && (best === undefined || best.avg_retrieval_ms === null || item.avg_retrieval_ms < best.avg_retrieval_ms)
      ? item
      : best
  ), undefined as ExperimentConfigResult | undefined)
  if (highestCitation && highestCitation.citation_hit_rate !== null) {
    // 当前最佳配置对应的展示名称。
    const label = detailConfigGroups.value[highestCitation.config_index]?.label
      ?? '策略 ' + (highestCitation.config_index + 1)
    lines.push('引用命中率最高：' + label + '（' + formatRate(highestCitation.citation_hit_rate) + '）。')
  }
  if (highestAccuracy && highestAccuracy.accuracy !== null) {
    // 当前最佳配置对应的展示名称。
    const label = detailConfigGroups.value[highestAccuracy.config_index]?.label
      ?? '策略 ' + (highestAccuracy.config_index + 1)
    lines.push('回答正确率最高：' + label + '（' + formatRate(highestAccuracy.accuracy) + '）。')
  }
  if (fastest && fastest.avg_retrieval_ms !== null) {
    // 当前最佳配置对应的展示名称。
    const label = detailConfigGroups.value[fastest.config_index]?.label
      ?? '策略 ' + (fastest.config_index + 1)
    lines.push('平均检索耗时最低：' + label + '（' + formatDuration(fastest.avg_retrieval_ms) + '）。')
  }
  if (current.best_config_index !== null) {
    // 当前最佳配置对应的展示名称。
    const label = detailConfigGroups.value[current.best_config_index]?.label
      ?? '策略 ' + (current.best_config_index + 1)
    lines.push('当前综合排序下表现最高：' + label + '。')
  }
  lines.push('以上结果仅适用于本次测试集和当前知识库。')
  return lines
})

/** 停止检索实验状态轮询。 */
function stopPolling() {
  if (pollTimer !== undefined) {
    clearInterval(pollTimer)
    pollTimer = undefined
  }
}

/** 开始轮询指定实验的最新状态。 */
// 参数 id：目标业务记录的唯一标识。
function startPolling(id: number) {
  if (pollTimer !== undefined) return
  pollTimer = setInterval(() => {
    if (selectedId.value === id && !detailRequests.has(id)) void loadDetail(id)
  }, 2000)
}

/** 加载实验详情，并按请求版本忽略过期结果。 */
// 参数 id：目标业务记录的唯一标识。
// 参数 showLoading：是否显示实验详情加载状态。
async function loadDetail(id: number, showLoading = false) {
  if (detailRequests.has(id)) return
  // 当前实验详情请求版本。
  const version = selectionVersion
  detailRequests.add(id)
  if (showLoading) loadingDetail.value = true
  detailError.value = ''
  try {
    // 当前配置对应的实验结果。
    const result = await getExperiment(id)
    if (version !== selectionVersion || selectedId.value !== id) return
    detail.value = result
    // 实验列表中的摘要记录。
    const summary = experiments.value.find((item) => item.id === id)
    if (summary) {
      summary.status = result.status
      summary.progress_current = result.progress_current
      summary.progress_total = result.progress_total
      summary.error_message = result.error_message
    }
    if (isTerminal(result.status)) stopPolling()
    else startPolling(id)
  } catch (error) {
    if (version === selectionVersion && selectedId.value === id) detailError.value = getErrorMessage(error)
  } finally {
    detailRequests.delete(id)
    if (version === selectionVersion && selectedId.value === id && showLoading) loadingDetail.value = false
  }
}

/** 选择实验并加载其详情。 */
// 参数 item：实验列表中的 ExperimentSummary，含实验 ID、运行状态和归档时间；本函数据此切换详情并停止旧实验的轮询。
function selectExperiment(item: ExperimentSummary) {
  stopPolling()
  selectionVersion += 1
  selectedId.value = item.id
  detail.value = undefined
  detailError.value = ''
  expandedConfigIndex.value = undefined
  if (!isTerminal(item.status)) startPolling(item.id)
  void loadDetail(item.id, true)
}

/** 按当前分页条件加载检索实验列表。 */
async function loadExperiments() {
  loadingList.value = true
  listError.value = ''
  try {
    // 当前配置对应的实验结果。
    const result = await getExperiments({ page: page.value, size: pageSize, include_archived: true })
    experiments.value = result.items
    total.value = result.total
    pages.value = result.pages
    if (selectedId.value === undefined && result.items.length > 0) {
      // 当前未归档的实验记录。
      const activeItems = result.items.filter((item) => !item.archived_at)
      // 当前列表中优先选中的最近活动实验。
      const latest = activeItems.find((item) => !isTerminal(item.status)) ?? activeItems[0]
      if (latest) selectExperiment(latest)
    }
  } catch (error) {
    listError.value = getErrorMessage(error)
  } finally {
    loadingList.value = false
  }
}

/** 加载实验可选的评测用例及数量。 */
async function loadEvaluationCases() {
  caseLoading.value = true
  caseError.value = ''
  try {
    const [allResult, builtinResult] = await Promise.all([
      getEvaluationCases({
        page: 1,
        size: 100,
        status: 'ACTIVE',
        include_archived: false,
      }),
      getEvaluationCases({
        page: 1,
        size: 100,
        status: 'ACTIVE',
        origin: 'BUILTIN',
        include_archived: false,
      }),
    ])
    evaluationCases.value = allResult.items
    evaluationCaseTotal.value = allResult.total
    builtinCaseTotal.value = builtinResult.total
  } catch (error) {
    caseError.value = getErrorMessage(error)
  } finally {
    caseLoading.value = false
  }
}

/** 切换实验使用的用例范围并同步选择状态。 */
// 参数 value：用户选择的实验用例范围；切回“全部用例”时清除之前选择的 case ID，避免旧筛选残留。
function updateCaseScope(value: ExperimentCaseScope) {
  caseScope.value = value
  if (value !== 'SELECTED') selectedCaseIds.value = []
}

/** 加载可用于预览或实验的检索策略。 */
async function loadStrategies() {
  strategyLoading.value = true
  strategyError.value = ''
  try {
    strategies.value = await listRetrievalStrategies({ include_archived: true })
  } catch (error) {
    strategies.value = []
    strategyError.value = getErrorMessage(error)
  } finally {
    if (previewStrategyId.value === undefined && availableStrategies.value.length > 0) {
      previewStrategyId.value = availableStrategies.value[0].id
    }
    strategyLoading.value = false
  }
}

// 当前执行启用、归档等操作的策略 ID。
const strategyActionId = ref<number>()

/** 用最新策略对象更新本地策略列表。 */
// 参数 strategy：后端返回的 RetrievalStrategy，包含策略 ID、内置/归档标记、激活状态、版本和参数；后续操作会据这些字段决定是否允许编辑或使用。
function replaceStrategy(strategy: RetrievalStrategy) {
  // 策略或实验在当前列表中的位置。
  const index = strategies.value.findIndex((item) => item.id === strategy.id)
  if (index >= 0) strategies.value.splice(index, 1, strategy)
  else strategies.value.push(strategy)
}

/** 切换指定检索策略的启用状态。 */
// 参数 strategy：待切换的 RetrievalStrategy；内置或已归档策略不允许切换，ID 和当前 is_active 值用于提交相反的启用状态。
async function toggleStrategyActive(strategy: RetrievalStrategy) {
  if (strategy.is_builtin || strategy.archived_at) return
  strategyActionId.value = strategy.id
  try {
    // 策略状态更新接口返回的策略记录。
    const updated = await setRetrievalStrategyActive(strategy.id, !strategy.is_active)
    replaceStrategy(updated)
    if (!updated.is_active) {
      experimentStrategyIds.value = experimentStrategyIds.value.filter((id) => id !== updated.id)
      if (previewStrategyId.value === updated.id) previewStrategyId.value = undefined
    }
    ElMessage.success(updated.is_active ? '策略已启用' : '策略已停用')
  } catch (error) {
    strategyError.value = getErrorMessage(error)
  } finally {
    strategyActionId.value = undefined
  }
}

/** 确认并归档指定检索策略。 */
// 参数 strategy：待归档的 RetrievalStrategy；内置或已归档策略直接跳过，名称用于确认提示，ID 用于归档请求及清理实验/预览选择。
async function archiveStrategy(strategy: RetrievalStrategy) {
  if (strategy.is_builtin || strategy.archived_at) return
  try {
    await ElMessageBox.confirm(
      `归档“${strategy.name}”后，它不会出现在可运行的策略选择中，历史实验结果不受影响。`,
      '归档检索策略',
      { type: 'warning', confirmButtonText: '归档', cancelButtonText: '取消' },
    )
    strategyActionId.value = strategy.id
    // 归档操作返回的实验或策略记录。
    const archived = await archiveRetrievalStrategy(strategy.id)
    replaceStrategy(archived)
    experimentStrategyIds.value = experimentStrategyIds.value.filter((id) => id !== archived.id)
    if (previewStrategyId.value === archived.id) previewStrategyId.value = undefined
    ElMessage.success('策略已归档')
  } catch (error) {
    if (error !== 'cancel' && error !== 'close') strategyError.value = getErrorMessage(error)
  } finally {
    strategyActionId.value = undefined
  }
}

/** 恢复已归档的检索策略。 */
// 参数 strategy：待恢复的 RetrievalStrategy；仅处理非内置且已有归档时间的记录，使用其 ID 请求恢复并更新本地列表。
async function restoreStrategy(strategy: RetrievalStrategy) {
  if (strategy.is_builtin || !strategy.archived_at) return
  strategyActionId.value = strategy.id
  try {
    // 恢复操作返回的实验或策略记录。
    const restored = await restoreRetrievalStrategy(strategy.id)
    replaceStrategy(restored)
    ElMessage.success('策略已恢复')
  } catch (error) {
    strategyError.value = getErrorMessage(error)
  } finally {
    strategyActionId.value = undefined
  }
}

/** 打开新建或编辑检索策略的表单。 */
// 参数 strategy：可选的编辑来源；非内置策略以其 ID 更新原记录，内置策略则复制为新策略，并预填名称、说明和配置。
function openStrategyEditor(strategy?: RetrievalStrategy) {
  strategyEditorId.value = strategy?.is_builtin ? undefined : strategy?.id
  strategyEditorCopy.value = !!strategy?.is_builtin
  strategyEditorError.value = ''
  strategyDraft.name = strategy ? `${strategy.name}${strategy.is_builtin ? ' · 副本' : ''}` : ''
  strategyDraft.description = strategy?.description ?? ''
  strategyDraft.config = cloneConfig(strategy?.config ?? experimentGroups[0].config)
  strategyEditorVisible.value = true
}

/** 将策略编辑表单重置为默认配置。 */
function resetStrategyDraft() {
  strategyDraft.config = cloneConfig(experimentGroups[0].config)
}

/** 校验并保存当前检索策略草稿。 */
async function saveStrategy() {
  // 去除首尾空白后的名称文本。
  const name = strategyDraft.name.trim()
  if (!name) {
    strategyEditorError.value = '请填写策略名称'
    return
  }
  if (strategyDraft.config.chunk_overlap >= strategyDraft.config.chunk_size) {
    strategyEditorError.value = 'Overlap 必须小于 Chunk Size'
    return
  }
  if (strategyDraft.config.top_k > strategyDraft.config.candidate_k) {
    strategyEditorError.value = 'Final Top-k 不能大于 Candidate Top-k'
    return
  }
  if (strategyDraft.config.evidence_limit < strategyDraft.config.top_k) {
    strategyEditorError.value = 'Evidence 上限不能小于 Final Top-k'
    return
  }
  if (strategyDraft.config.neighbor_expand_top_n > strategyDraft.config.top_k) {
    strategyEditorError.value = '邻接扩展 Top-N 不能大于 Final Top-k'
    return
  }
  if (strategyDraft.config.rerank_enabled && strategyDraft.config.rerank_top_n > strategyDraft.config.candidate_k) {
    strategyEditorError.value = 'Rerank Top-N 不能大于 Candidate Top-k'
    return
  }
  strategySaving.value = true
  strategyEditorError.value = ''
  try {
    // 创建或更新策略接口返回的策略记录。
    const saved = strategyEditorId.value === undefined
      ? await createRetrievalStrategy({
          name,
          description: strategyDraft.description.trim(),
          config: cloneConfig(strategyDraft.config),
        })
      : await updateRetrievalStrategy(strategyEditorId.value, {
          name,
          description: strategyDraft.description.trim(),
          config: cloneConfig(strategyDraft.config),
        })
    // 已保存策略在本地列表中的位置。
    const existingIndex = strategies.value.findIndex((item) => item.id === saved.id)
    if (existingIndex >= 0) strategies.value.splice(existingIndex, 1, saved)
    else strategies.value.push(saved)
    if (previewStrategyId.value === undefined && saved.is_active && !saved.archived_at) {
      previewStrategyId.value = saved.id
    }
    strategyEditorVisible.value = false
    ElMessage.success(strategyEditorCopy.value ? '策略副本已创建' : strategyEditorId.value === undefined ? '策略已创建' : '策略已更新')
  } catch (error) {
    strategyEditorError.value = getErrorMessage(error)
  } finally {
    strategySaving.value = false
  }
}

/** 将选中策略载入编辑表单。 */
// 参数 strategy：当前选中的 RetrievalStrategy；交给表单初始化逻辑，普通策略进入编辑，内置策略按副本方式打开。
function editStrategy(strategy: RetrievalStrategy) {
  openStrategyEditor(strategy)
}

/** 加载并显示指定策略的历史版本。 */
// 参数 strategy：需要查看历史版本的 RetrievalStrategy；其 ID 用于请求版本列表，记录本身用于标识当前版本弹窗对应的策略。
async function openStrategyVersions(strategy: RetrievalStrategy) {
  strategyVersionStrategy.value = strategy
  strategyVersions.value = []
  strategyVersionError.value = ''
  strategyVersionVisible.value = true
  strategyVersionLoading.value = true
  try {
    strategyVersions.value = await listRetrievalStrategyVersions(strategy.id)
  } catch (error) {
    strategyVersionError.value = getErrorMessage(error)
  } finally {
    strategyVersionLoading.value = false
  }
}

/** 恢复指定检索策略历史版本。 */
// 参数 version：策略历史版本记录。
async function restoreStrategyVersion(version: RetrievalStrategyVersion) {
  // 当前选中或正在编辑的检索策略。
  const strategy = strategyVersionStrategy.value
  if (!strategy || strategy.is_builtin) return
  try {
    await ElMessageBox.confirm(
      `恢复版本 ${version.version} 会生成一个新的当前版本，是否继续？`,
      '恢复策略版本',
      { type: 'warning', confirmButtonText: '恢复', cancelButtonText: '取消' },
    )
    restoringStrategyVersion.value = version.version
    // 恢复操作返回的实验或策略记录。
    const restored = await restoreRetrievalStrategyVersion(strategy.id, version.version)
    replaceStrategy(restored)
    strategyVersionStrategy.value = restored
    strategyVersions.value = await listRetrievalStrategyVersions(restored.id)
    ElMessage.success(`已恢复版本 ${version.version}`)
  } catch (error) {
    if (error !== 'cancel' && error !== 'close') strategyVersionError.value = getErrorMessage(error)
  } finally {
    restoringStrategyVersion.value = undefined
  }
}

/** 确认并删除指定检索策略。 */
// 参数 strategy：待删除的 RetrievalStrategy；内置策略不可删除，名称用于确认提示，ID 用于请求删除并清除相关选择状态。
async function removeStrategy(strategy: RetrievalStrategy) {
  if (strategy.is_builtin) return
  try {
    await ElMessageBox.confirm(
      `删除“${strategy.name}”后，已完成的实验结果不会受影响。`,
      '删除检索策略',
      { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' },
    )
    await deleteRetrievalStrategy(strategy.id)
    strategies.value = strategies.value.filter((item) => item.id !== strategy.id)
    experimentStrategyIds.value = experimentStrategyIds.value.filter((id) => id !== strategy.id)
    if (previewStrategyId.value === strategy.id) previewStrategyId.value = undefined
    ElMessage.success('策略已删除')
  } catch (error) {
    if (error !== 'cancel' && error !== 'close') strategyError.value = getErrorMessage(error)
  }
}

/** 选择用于检索预览的策略。 */
// 参数 strategy：用户选择的 RetrievalStrategy；仅接受已启用且未归档的策略，选中后清除旧预览结果并滚动至预览区域。
function selectPreviewStrategy(strategy: RetrievalStrategy) {
  if (!strategy.is_active || strategy.archived_at) return
  previewStrategyId.value = strategy.id
  clearPreviewResult()
  void nextTick(() => {
    // 预览结果面板对应的页面元素。
    const element = previewPanel.value
    if (element && typeof element.scrollIntoView === 'function') {
      element.scrollIntoView({ behavior: 'smooth', block: 'center' })
    }
  })
}

/** 根据策略 ID 更新预览策略选择。 */
// 参数 strategyId：检索策略的唯一标识。
function updatePreviewStrategy(strategyId: number | undefined) {
  // 当前选中或正在编辑的检索策略。
  const strategy = availableStrategies.value.find((item) => item.id === strategyId)
  if (!strategy) return
  previewStrategyId.value = strategy.id
  clearPreviewResult()
}

/** 清空上一次检索预览结果和错误状态。 */
function clearPreviewResult() {
  previewResult.value = undefined
  previewResultCollapsed.value = false
  previewError.value = ''
}

/** 折叠或展开检索预览结果面板。 */
function togglePreviewResult() {
  previewResultCollapsed.value = !previewResultCollapsed.value
}

/** 使用当前问题和策略执行一次检索预览。 */
async function runPreview() {
  // 当前选中或正在编辑的检索策略。
  const strategy = previewStrategy.value
  if (!strategy) {
    previewError.value = '请先选择一个启用中的检索策略'
    return
  }
  // 去除首尾空白后的预览问题。
  const question = previewQuestion.value.trim()
  if (!question) {
    previewError.value = '请输入要试跑的问题'
    return
  }
  // 本次预览使用的策略 ID。
  const strategyId = strategy.id
  previewLoading.value = true
  previewError.value = ''
  previewResult.value = undefined
  previewResultCollapsed.value = false
  try {
    // 当前配置对应的实验结果。
    const result = await previewRetrieval({
      question,
      answer_style: answerStyle.value || 'plain',
      strategy_id: strategyId,
      ai_mode: preferencesStore.aiMode,
    })
    if (previewStrategyId.value === strategyId) previewResult.value = result
  } catch (error) {
    previewError.value = getErrorMessage(error)
  } finally {
    previewLoading.value = false
  }
}

/** 按当前策略与用例选择创建检索实验。 */
async function submitExperiment() {
  // 去除首尾空白后的名称文本。
  const name = experimentName.value.trim()
  if (!name) {
    createError.value = '请填写实验名称'
    return
  }
  if (experimentStrategies.value.length < 2) {
    createError.value = '检索策略对比实验至少需要选择两条策略'
    return
  }
  if (caseScope.value === 'SELECTED' && selectedCaseIds.value.length === 0) {
    createError.value = '自定义问题集至少选择一个评测用例'
    return
  }

  creating.value = true
  createError.value = ''
  try {
    // 创建或复制实验接口返回的任务受理信息。
    const job = await createExperiment({
      name,
      strategy_ids: experimentStrategies.value.map((strategy) => strategy.id),
      case_ids: caseScope.value === 'SELECTED' ? selectedCaseIds.value : null,
      case_scope: caseScope.value,
      answer_style: answerStyle.value || 'plain',
      ai_mode: preferencesStore.aiMode,
    })
    stopPolling()
    selectionVersion += 1
    selectedId.value = job.experiment_id
    detail.value = undefined
    expandedConfigIndex.value = undefined
    if (!isTerminal(job.status)) startPolling(job.experiment_id)
    void loadDetail(job.experiment_id, true)
    await loadExperiments()
  } catch (error) {
    createError.value = getErrorMessage(error)
  } finally {
    creating.value = false
  }
}

/** 确认并删除指定检索实验。 */
// 参数 item：待删除的 ExperimentSummary；其状态用于阻止删除仍在排队或运行中的任务，ID 用于提交删除请求。
async function removeExperiment(item: ExperimentSummary) {
  if (item.status === 'PENDING' || item.status === 'RUNNING') {
    ElMessage.warning('运行中的实验完成后才能删除')
    return
  }
  try {
    await ElMessageBox.confirm(
      `删除“${item.name}”后，实验记录和隔离索引都会被清理。`,
      '删除实验记录',
      { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' },
    )
    deletingExperimentId.value = item.id
    await deleteExperiment(item.id)
    if (selectedId.value === item.id) {
      stopPolling()
      selectedId.value = undefined
      detail.value = undefined
    }
    await loadExperiments()
    ElMessage.success('实验记录已删除')
  } catch (error) {
    if (error !== 'cancel' && error !== 'close') listError.value = getErrorMessage(error)
  } finally {
    deletingExperimentId.value = undefined
  }
}

/** 归档指定检索实验并更新当前列表。 */
// 参数 item：待归档的 ExperimentSummary；仅允许已完成或失败且尚未归档的实验提交归档。
async function archiveExperimentRecord(item: ExperimentSummary) {
  if (item.status === 'PENDING' || item.status === 'RUNNING' || item.archived_at) {
    ElMessage.warning('只有已完成或失败且未归档的实验才能归档')
    return
  }
  try {
    await ElMessageBox.confirm(
      `归档“${item.name}”后，它会从默认实验列表中隐藏，历史结果仍可恢复查看。`,
      '归档实验记录',
      { type: 'warning', confirmButtonText: '归档', cancelButtonText: '取消' },
    )
    archivingExperimentId.value = item.id
    // 归档操作返回的实验或策略记录。
    const archived = await archiveRetrievalExperiment(item.id)
    // 策略或实验在当前列表中的位置。
    const index = experiments.value.findIndex((current) => current.id === archived.id)
    if (index >= 0) experiments.value.splice(index, 1, archived)
    if (selectedId.value === item.id) await loadDetail(item.id)
    ElMessage.success('实验记录已归档')
  } catch (error) {
    if (error !== 'cancel' && error !== 'close') listError.value = getErrorMessage(error)
  } finally {
    archivingExperimentId.value = undefined
  }
}

/** 恢复已归档的检索实验。 */
// 参数 item：已归档的 ExperimentSummary；通过其 ID 发起恢复，并用 ID 更新当前行的归档状态。
async function restoreExperimentRecord(item: ExperimentSummary) {
  if (!item.archived_at) return
  archivingExperimentId.value = item.id
  try {
    // 恢复操作返回的实验或策略记录。
    const restored = await restoreRetrievalExperiment(item.id)
    // 策略或实验在当前列表中的位置。
    const index = experiments.value.findIndex((current) => current.id === restored.id)
    if (index >= 0) experiments.value.splice(index, 1, restored)
    ElMessage.success('实验记录已恢复')
  } catch (error) {
    listError.value = getErrorMessage(error)
  } finally {
    archivingExperimentId.value = undefined
  }
}

/** 导出指定实验的结果文件。 */
// 参数 item：要导出的 ExperimentSummary；其 ID 用于请求 CSV，当前记录 ID 同时驱动按钮加载状态。
async function exportExperimentRecord(item: ExperimentSummary) {
  exportingExperimentId.value = item.id
  try {
    // 实验导出的 CSV 文本内容。
    const csv = await exportRetrievalExperiment(item.id)
    // 待下载 CSV 文本构造出的文件对象。
    const blob = new Blob([`\uFEFF${csv}`], { type: 'text/csv;charset=utf-8' })
    // 本地下载文件的临时对象 URL。
    const url = URL.createObjectURL(blob)
    // 触发浏览器下载的链接元素。
    const link = document.createElement('a')
    link.href = url
    link.download = `${item.name || `检索实验-${item.id}`}.csv`
    link.click()
    URL.revokeObjectURL(url)
    ElMessage.success('结果已导出')
  } catch (error) {
    listError.value = getErrorMessage(error)
  } finally {
    exportingExperimentId.value = undefined
  }
}

/** 复制指定实验配置并创建新实验。 */
// 参数 item：作为复制来源的 ExperimentSummary；复制接口读取其 ID，并根据返回的受理信息跳转或刷新任务列表。
async function duplicateExperiment(item: ExperimentSummary) {
  try {
    copyingExperimentId.value = item.id
    // 创建或复制实验接口返回的任务受理信息。
    const job = await copyExperiment(item.id, `${item.name} · 副本`)
    stopPolling()
    selectionVersion += 1
    selectedId.value = job.experiment_id
    detail.value = undefined
    expandedConfigIndex.value = undefined
    if (!isTerminal(job.status)) startPolling(job.experiment_id)
    void loadDetail(job.experiment_id, true)
    await loadExperiments()
    ElMessage.success('已复制为新实验并开始运行')
  } catch (error) {
    listError.value = getErrorMessage(error)
  } finally {
    copyingExperimentId.value = undefined
  }
}

/** 切换检索实验列表页码。 */
// 参数 nextPage：用户选择的目标页码。
function changePage(nextPage: number) {
  page.value = nextPage
  void loadExperiments()
}

/** 将实验状态转换为中文展示文本。 */
// 参数 value：后端 ExperimentStatus 枚举或未就绪值；转换为列表显示的中文状态，未知/空值统一显示为“未知”。
function formatStatus(value: ExperimentStatus | undefined) {
  if (value === 'PENDING') return '排队中'
  if (value === 'RUNNING') return '运行中'
  if (value === 'COMPLETED') return '已完成'
  if (value === 'FAILED') return '失败'
  return '加载中'
}

/** 将比例值格式化为百分比。 */
// 参数 value：0 到 1 之间的实验指标或空值；有效数值转换为百分比并保留一位小数，空值显示为短横线。
function formatRate(value: number | null | undefined) {
  return value === null || value === undefined ? '—' : (value * 100).toFixed(1) + '%'
}

/** 将实验阶段耗时毫秒数格式化为易读文本。 */
// 参数 value：实验阶段耗时数值或空值；保留一位小数并附毫秒单位，空值显示为短横线。
function formatDuration(value: number | null | undefined) {
  return value === null || value === undefined ? '—' : value.toFixed(1) + ' ms'
}

/** 将预览回答模式转换为中文展示文本。 */
// 参数 value：预览接口返回的 answer_mode；把完整回答、部分回答、工具回答和拒答映射为中文说明。
function formatPreviewMode(value: RetrievalPreview['answer_mode']) {
  if (value === 'DIRECT_RESPONSE') return '直接回复'
  if (value === 'RAG_ANSWER') return '完整回答'
  if (value === 'PARTIAL_ANSWER') return '部分回答'
  if (value === 'TOOL_ONLY') return '工具回答'
  return '建议拒答'
}

/** 将创建时间格式化为本地日期时间。 */
// 参数 value：服务端返回的 ISO 时间字符串；格式化为本地日期时间，无法解析时返回原字符串以保留诊断信息。
function formatCreatedAt(value: string) {
  // 待格式化的日期时间对象。
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  // 将个位数字补零到两位的格式化函数。
  const pad = (part: number) => String(part).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`
}

/** 根据已完成数计算实验进度百分比。 */
// 参数 current：已经完成的工作项数量。
// 参数 total：本次任务的工作项总数量。
function progressPercent(current: number, total: number) {
  if (total <= 0) return 0
  return Math.min(100, Math.round((current / total) * 100))
}

/** 将实验状态映射为界面样式类别。 */
// 参数 value：实验运行状态或空值；映射为对应 CSS 状态类，使排队、运行、成功和失败采用不同视觉提示。
function statusClass(value: ExperimentStatus | undefined) {
  if (value === 'COMPLETED') return 'experiment-status-completed'
  if (value === 'FAILED') return 'experiment-status-failed'
  if (value === 'RUNNING') return 'experiment-status-running'
  return 'experiment-status-pending'
}

/** 按配置序号查找对应的实验结果。 */
// 参数 index：实验结果使用的 config_index；据此从已建索引中读取该配置的汇总指标，未找到时返回 undefined。
function configResult(index: number): ExperimentConfigResult | undefined {
  return configResults.value.get(index)
}

/** 获取指定配置的运行状态。 */
// 参数 index：目标配置的 config_index；用于统计该配置已完成的用例数，并与总用例数比较后生成进度文案。
function configStatus(index: number) {
  // 当前配置已完成的用例结果数量。
  const completedCases = detail.value?.results.filter((item) => item.config_index === index).length ?? 0
  // 当前实验配置是否存在预期用例数量。
  const expectedCases = detail.value && detail.value.config_results.length > 0
    ? detail.value.progress_total / detail.value.config_results.length
    : 0
  if (detail.value?.status === 'COMPLETED') return '已完成'
  if (detail.value?.status === 'FAILED') return completedCases ? '部分完成' : '未开始'
  if (expectedCases > 0 && completedCases >= expectedCases) return '已完成'
  return completedCases ? '运行中' : formatStatus(detail.value?.status)
}

/** 展开指定配置的实验结果详情。 */
// 参数 index：用户点击的实验配置序号；再次点击当前展开项时收起，否则切换到该配置的用例结果。
function chooseConfig(index: number) {
  expandedConfigIndex.value = expandedConfigIndex.value === index ? undefined : index
}

onMounted(() => {
  void loadExperiments()
  void loadStrategies()
  void loadEvaluationCases()
})

onBeforeUnmount(() => {
  stopPolling()
  selectionVersion += 1
})
</script>

<template>
  <section class="experiment-page" aria-labelledby="experiment-title">
    <header class="page-intro documents-page-intro">
      <div>
        <h1 id="experiment-title">检索策略实验</h1>
        <p>先单题验证一条策略，再使用统一问题集创建正式多策略实验。</p>
      </div>
    </header>

    <section class="experiment-panel experiment-workbench" aria-labelledby="strategy-workbench-title">
      <div class="experiment-heading">
        <div>
          <h2 id="strategy-workbench-title">策略库</h2>
          <p>查看参数并选择一条策略进入单题快速验证；正式实验在下方单独选择多条策略。</p>
        </div>
        <div class="experiment-heading-actions">
          <ElButton class="experiment-secondary" type="primary" plain @click="openStrategyEditor()">新建策略</ElButton>
        </div>
      </div>
      <p v-if="strategyError" class="experiment-inline-hint" role="status">策略服务暂不可用：{{ strategyError }}</p>
      <div class="strategy-grid" :aria-busy="strategyLoading">
        <article
          v-for="strategy in strategies"
          :key="strategy.id"
          class="strategy-card"
          role="button"
          tabindex="0"
          :aria-label="'选择策略 ' + strategy.name"
          :aria-pressed="previewStrategyId === strategy.id"
          :aria-disabled="!strategy.is_active || !!strategy.archived_at"
          :class="{
            'strategy-card-disabled': !strategy.is_active || strategy.archived_at,
            'strategy-card-preview-selected': previewStrategyId === strategy.id,
          }"
          @click="selectPreviewStrategy(strategy)"
          @keydown.enter.prevent="selectPreviewStrategy(strategy)"
          @keydown.space.prevent="selectPreviewStrategy(strategy)"
        >
          <div class="strategy-card-select">
            <span :title="`${strategy.name} · v${strategy.version}`">{{ strategy.name }} <em>v{{ strategy.version }}</em><em v-if="strategy.archived_at"> · 已归档</em><em v-else-if="!strategy.is_active"> · 已停用</em></span>
          </div>
          <p>{{ strategy.description }}</p>
          <dl class="strategy-card-meta">
            <div><dt>分块</dt><dd>{{ strategy.config.chunk_size }} / {{ strategy.config.chunk_overlap }}</dd></div>
            <div><dt>候选 / 最终</dt><dd>{{ strategy.config.candidate_k }} / {{ strategy.config.top_k }}</dd></div>
            <div><dt>轻量重排</dt><dd>{{ strategy.config.lightweight_rerank_enabled ? '开' : '关' }}</dd></div>
            <div><dt>模型重排</dt><dd>{{ strategy.config.rerank_enabled ? '开' : '关' }}</dd></div>
            <div><dt>邻接扩展</dt><dd>{{ strategy.config.neighbor_expand_top_n }} × {{ strategy.config.neighbor_radius }}</dd></div>
            <div><dt>证据上限</dt><dd>{{ strategy.config.evidence_limit }}</dd></div>
            <div><dt>阈值</dt><dd>{{ strategy.config.score_threshold.toFixed(2) }}</dd></div>
          </dl>
          <div class="strategy-card-actions" @click.stop @keydown.stop>
            <ElButton text @click="editStrategy(strategy)">编辑</ElButton>
            <ElButton text @click="openStrategyVersions(strategy)">版本</ElButton>
            <ElButton
              v-if="!strategy.is_builtin && !strategy.archived_at"
              text
              :loading="strategyActionId === strategy.id"
              @click="toggleStrategyActive(strategy)"
            >{{ strategy.is_active ? '停用' : '启用' }}</ElButton>
            <ElButton
              v-if="!strategy.is_builtin"
              text
              :loading="strategyActionId === strategy.id"
              @click="strategy.archived_at ? restoreStrategy(strategy) : archiveStrategy(strategy)"
            >{{ strategy.archived_at ? '恢复' : '归档' }}</ElButton>
            <ElButton v-if="!strategy.is_builtin" text type="danger" @click="removeStrategy(strategy)">删除</ElButton>
          </div>
        </article>
        <p v-if="!strategyLoading && strategies.length === 0" class="experiment-empty">暂无策略，请先新建一条策略。</p>
      </div>
    </section>

    <section ref="previewPanel" class="experiment-panel preview-panel" aria-labelledby="preview-title">
      <div class="experiment-heading">
        <div>
          <h2 id="preview-title">单题快速验证</h2>
          <p>快速试跑复用当前生产索引，仅用于确认一条策略的检索链路。</p>
        </div>
      </div>
      <div class="preview-form">
        <label>
          <span>策略</span>
          <ElSelect
            :model-value="previewStrategyId"
            class="experiment-create-select"
            aria-label="单题试跑策略"
            placeholder="选择一条策略"
            @update:model-value="updatePreviewStrategy"
          >
            <ElOption
              v-for="strategy in availableStrategies"
              :key="strategy.id"
              :label="strategy.name + ' · v' + strategy.version"
              :value="strategy.id"
            />
          </ElSelect>
        </label>
        <label class="preview-question-field">
          <span>测试问题</span>
          <ElInput
            v-model="previewQuestion"
            class="preview-question-input"
            type="text"
            maxlength="2000"
            placeholder="例如：公司拖欠工资，我应该准备什么材料？"
          />
        </label>
        <ElButton class="experiment-primary preview-submit" type="primary" :loading="previewLoading" :disabled="!previewStrategy" @click="runPreview">开始试跑</ElButton>
      </div>
      <p class="preview-limitations">快速试跑复用当前生产索引。Candidate Top-k、Final Top-k、轻量重排、邻接扩展、旧模型重排和阈值会生效；Chunk Size / Overlap 不会重新切分。涉及切块粒度比较时，请创建正式实验。</p>
      <p v-if="previewError" class="experiment-error" role="alert">{{ previewError }}</p>
      <div v-if="previewLoading" class="preview-loading">正在执行检索链路…</div>
      <template v-if="previewResult">
        <div class="preview-result-toolbar">
          <div class="preview-result-identity">
            <strong :title="`${previewResult.strategy_name} · v${previewResult.strategy_version}`">{{ previewResult.strategy_name }} · v{{ previewResult.strategy_version }}</strong>
            <span>{{ previewResult.index_mode }}</span>
          </div>
          <div class="preview-result-actions">
            <ElButton
              class="preview-result-action"
              text
              :aria-expanded="!previewResultCollapsed"
              aria-controls="preview-result-content"
              @click="togglePreviewResult"
            >
              {{ previewResultCollapsed ? '展开结果' : '收起结果' }}
            </ElButton>
            <ElButton class="preview-result-action" text @click="clearPreviewResult">清空结果</ElButton>
          </div>
        </div>
        <div id="preview-result-content" v-if="!previewResultCollapsed" class="preview-result-content">
          <p class="experiment-inline-hint">运行限制：{{ previewResult.limitations.join('；') }}</p>
          <div class="preview-answer" :class="{ 'preview-answer-refused': previewResult.refused }">
            <div class="preview-answer-heading"><strong>回答模式：{{ formatPreviewMode(previewResult.answer_mode) }}</strong><span>{{ previewResult.retrieval_ms }} ms</span></div>
            <MarkdownContent class="preview-answer-markdown" :content="previewResult.answer" />
          </div>
          <div class="preview-trace">
            <h3>链路检查</h3>
            <ol>
              <li v-for="stage in previewResult.trace" :key="stage.stage">
                <span class="preview-trace-dot" :class="'preview-trace-' + stage.status" aria-hidden="true" />
                <div><strong>{{ stage.stage }}</strong><span>{{ stage.detail }}</span></div>
              </li>
            </ol>
          </div>
          <details class="preview-sources" :open="previewResult.retrieved_sources.length > 0">
            <summary>召回片段（{{ previewResult.retrieved_sources.length }}）</summary>
            <ul v-if="previewResult.retrieved_sources.length > 0">
              <li v-for="source in previewResult.retrieved_sources" :key="source.chunk_id">
                <strong>{{ source.file_name }} · 第 {{ source.chunk_no }} 段</strong>
                <span>排序 {{ source.rank_no }} · 检索分 {{ source.retrieval_score.toFixed(3) }} · 轻量分 {{ source.lightweight_score.toFixed(3) }} · {{ source.evidence_origin === 'NEIGHBOR' ? '邻接扩展' : '主召回' }}</span>
                <p>{{ source.content }}</p>
              </li>
            </ul>
            <p v-else class="experiment-muted">没有召回片段，建议降低阈值或检查资料覆盖范围。</p>
          </details>
        </div>
      </template>
    </section>

    <section class="experiment-panel experiment-create-panel" aria-labelledby="experiment-create-title">
      <div class="experiment-create-layout">
        <div class="experiment-heading">
          <div>
            <h2 id="experiment-create-title">创建正式对比实验</h2>
            <p>至少选择两条策略，使用同一评测问题集比较完整检索链路。</p>
          </div>
        </div>
        <form class="experiment-create-form" @submit.prevent="submitExperiment">
          <label class="experiment-name-field">
            <span>实验名称</span>
            <ElInput
              v-model="experimentName"
              class="experiment-create-input"
              maxlength="100"
              required
              placeholder="例如：检索参数对比-20260915"
              aria-label="实验名称"
            />
          </label>
          <label class="experiment-strategy-field">
            <span>对比策略</span>
            <ElSelect
              v-model="experimentStrategyIds"
              class="experiment-create-select"
              multiple
              filterable
              collapse-tags
              placeholder="至少选择两条策略"
              aria-label="对比策略"
            >
              <ElOption
                v-for="strategy in availableStrategies"
                :key="strategy.id"
                :label="strategy.name + ' · v' + strategy.version"
                :value="strategy.id"
              />
            </ElSelect>
          </label>
          <label class="experiment-case-scope-field">
            <span>评测问题集</span>
            <ElSelect
              :model-value="caseScope"
              class="experiment-create-select"
              aria-label="评测问题集"
              @update:model-value="updateCaseScope"
            >
              <ElOption label="官方基线问题集" value="BUILTIN_BASELINE" />
              <ElOption label="全部活动用例" value="ALL_ACTIVE" />
              <ElOption label="自定义选择" value="SELECTED" />
            </ElSelect>
          </label>
          <label class="experiment-answer-style-field">
            <span>回答风格</span>
            <ElSelect v-model="answerStyle" class="experiment-create-select" placeholder="使用默认值" aria-label="回答风格">
              <ElOption label="使用默认值" value="" />
              <ElOption label="通俗版" value="plain" />
              <ElOption label="严谨版" value="legal" />
            </ElSelect>
          </label>
          <label v-if="caseScope === 'SELECTED'" class="experiment-case-select-label">
            <span>选择评测用例</span>
            <ElSelect
              v-model="selectedCaseIds"
              class="experiment-create-select"
              multiple
              filterable
              collapse-tags
              :loading="caseLoading"
              placeholder="请选择一个或多个用例"
              aria-label="选择评测用例"
            >
              <ElOption
                v-for="item in evaluationCases"
                :key="item.id"
                :label="'#' + item.id + ' ' + item.topic"
                :value="item.id"
              />
            </ElSelect>
          </label>
          <div class="experiment-create-actions">
            <p class="experiment-estimate" role="status">
              本次实验预计执行：{{ experimentStrategies.length }} 个策略 × {{ experimentCaseCount }} 条问题 = {{ estimatedRuns }} 题次
            </p>
            <div class="experiment-create-action-end">
              <span v-if="experimentStrategies.length < 2" class="experiment-create-hint" role="status">
                <WarningFilled aria-hidden="true" />
                至少选择两条策略
              </span>
              <ElButton class="experiment-primary" type="primary" native-type="submit" :loading="creating" :disabled="creating">
                {{ creating ? '创建中…' : '创建实验' }}
              </ElButton>
            </div>
          </div>
        </form>
      </div>
      <p v-if="caseError" class="experiment-error" role="alert">评测用例加载失败：{{ caseError }}</p>
      <p v-if="createError" class="experiment-error" role="alert">{{ createError }}</p>
    </section>

    <section class="experiment-panel" aria-labelledby="experiment-list-title">
      <div class="experiment-heading">
        <div>
          <h2 id="experiment-list-title">实验记录</h2>
          <p>选择实验查看进度；运行中的任务每 2 秒更新一次。归档记录可恢复或导出。</p>
        </div>
      </div>
      <p v-if="listError" class="experiment-error" role="alert">{{ listError }}</p>
      <div class="experiment-table-wrap" :aria-busy="loadingList">
        <table class="experiment-table experiment-list-table">
          <thead>
            <tr>
              <th scope="col" class="experiment-select-cell"><span class="sr-only">选择</span></th>
              <th scope="col">实验名称</th>
              <th scope="col">状态</th>
              <th scope="col">进度</th>
              <th scope="col">创建时间</th>
              <th scope="col" class="experiment-actions-header">操作</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="item in experiments"
              :key="item.id"
              :class="{
                'experiment-row-selected': item.id === selectedId,
                'experiment-row-archived': item.archived_at,
              }"
              @click="selectExperiment(item)"
            >
              <td class="experiment-select-cell">
                <input
                  :aria-label="`选择实验 ${item.name}`"
                  type="radio"
                  name="retrieval-experiment"
                  :checked="item.id === selectedId"
                  @click.stop
                  @change="selectExperiment(item)"
                />
              </td>
              <td>
                <button class="experiment-link" type="button" @click.stop="selectExperiment(item)">{{ item.name }}</button>
                <span v-if="item.archived_at" class="experiment-archived-label">已归档</span>
              </td>
              <td>
                <span class="experiment-status-inline" :class="statusClass(item.status)">
                  <i aria-hidden="true" />{{ formatStatus(item.status) }}
                </span>
              </td>
              <td>{{ item.progress_current }} / {{ item.progress_total }}</td>
              <td>{{ formatCreatedAt(item.created_at) }}</td>
              <td class="experiment-actions-cell">
                <div class="experiment-action-group">
                  <ElButton
                    class="experiment-table-action"
                    text
                    :loading="exportingExperimentId === item.id"
                    :disabled="item.status === 'PENDING' || item.status === 'RUNNING'"
                    @click.stop="exportExperimentRecord(item)"
                  >导出</ElButton>
                  <ElButton
                    class="experiment-table-action"
                    text
                    :loading="archivingExperimentId === item.id"
                    :disabled="item.status === 'PENDING' || item.status === 'RUNNING'"
                    @click.stop="item.archived_at ? restoreExperimentRecord(item) : archiveExperimentRecord(item)"
                  >{{ item.archived_at ? '恢复' : '归档' }}</ElButton>
                  <ElButton
                    class="experiment-table-action"
                    text
                    :loading="copyingExperimentId === item.id"
                    @click.stop="duplicateExperiment(item)"
                  >复制</ElButton>
                  <ElButton
                    class="experiment-table-action"
                    text
                    type="danger"
                    :loading="deletingExperimentId === item.id"
                    :disabled="item.status === 'PENDING' || item.status === 'RUNNING'"
                    @click.stop="removeExperiment(item)"
                  >删除</ElButton>
                </div>
              </td>
            </tr>
            <tr v-if="!loadingList && experiments.length === 0">
              <td colspan="6" class="experiment-empty">暂无检索实验。先在上方试跑，再创建批量实验。</td>
            </tr>
          </tbody>
        </table>
      </div>
      <div class="experiment-pagination">
        <span>共 {{ total }} 条，第 {{ page }} / {{ pages || 1 }} 页</span>
        <ElButton native-type="button" :disabled="page <= 1 || loadingList" @click="changePage(page - 1)">上一页</ElButton>
        <ElButton native-type="button" :disabled="page >= pages || loadingList" @click="changePage(page + 1)">下一页</ElButton>
      </div>
    </section>

    <section v-if="selectedId !== undefined" class="experiment-panel experiment-detail-panel" aria-labelledby="experiment-detail-title">
      <div class="experiment-heading experiment-detail-heading">
        <div>
          <h2 id="experiment-detail-title">{{ selectedName || '检索实验 #' + selectedId }}</h2>
          <p v-if="detail" class="experiment-signature">
            Embedding 快照：{{ detail.embedding_signature.embedding_provider }} · {{ detail.embedding_signature.embedding_model }} · {{ detail.embedding_signature.embedding_dimension }} 维
          </p>
          <p v-if="detail" class="experiment-signature">AI 模式：{{ aiModeLabel(detail.runtime_config.ai_mode) }}</p>
        </div>
        <span class="experiment-status" :class="statusClass(status)">{{ formatStatus(status) }}</span>
      </div>
      <p v-if="detailError" class="experiment-error" role="alert">{{ detailError }}</p>
      <p v-if="detail?.error_message" class="experiment-error" role="alert">{{ detail.error_message }}</p>
      <p v-if="loadingDetail && !detail" class="experiment-muted">正在读取实验详情…</p>
      <template v-if="detail">
        <div class="experiment-summary-row">
          <div class="experiment-progress" aria-label="实验进度">
            <span>进度</span>
            <progress :value="detail.progress_current" :max="Math.max(detail.progress_total, 1)" />
            <strong>{{ detail.progress_current }} / {{ detail.progress_total }} 题次</strong>
            <span class="experiment-progress-percent">{{ progressPercent(detail.progress_current, detail.progress_total) }}%</span>
          </div>
          <p v-if="detail.best_config_index !== null" class="experiment-best" role="status">
            当前最优策略：<strong>{{ detailConfigGroups[detail.best_config_index]?.label ?? '#' + detail.best_config_index }}</strong>
          </p>
        </div>
        <p v-if="detail.best_config_index === null && detail.status === 'COMPLETED'" class="experiment-muted">没有可比较的完整配置结果。</p>

        <section v-if="experimentConclusion.length > 0" class="experiment-conclusion" aria-labelledby="experiment-conclusion-title">
          <h3 id="experiment-conclusion-title">实验结论</h3>
          <p v-for="line in experimentConclusion" :key="line">{{ line }}</p>
        </section>

        <div class="experiment-table-wrap">
          <table class="experiment-table experiment-results-table">
            <thead>
              <tr>
                <th scope="col">策略</th>
                <th scope="col">Chunk / Overlap · 候选 / 最终 · 重排</th>
                <th scope="col">引用命中率</th>
                <th scope="col">正确率</th>
                <th scope="col">拒答率</th>
                <th scope="col">平均检索耗时</th>
                <th scope="col">状态</th>
              </tr>
            </thead>
            <tbody>
              <tr
                v-for="(group, index) in detailConfigGroups"
                :key="group.label + index"
                :class="{
                  'experiment-row-selected': index === expandedConfigIndex,
                  'experiment-row-best': index === detail.best_config_index,
                }"
                @click="chooseConfig(index)"
              >
                <th scope="row">
                  <button
                    class="experiment-link"
                    type="button"
                    :title="group.label + (index === detail.best_config_index ? ' · 最优' : '')"
                    :aria-expanded="index === expandedConfigIndex"
                    @click.stop="chooseConfig(index)"
                  >
                    {{ group.label }}{{ index === detail.best_config_index ? ' · 最优' : '' }}
                  </button>
                </th>
                <td>{{ group.config.chunk_size }} / {{ group.config.chunk_overlap }} · {{ group.config.candidate_k }} / {{ group.config.top_k }} · {{ group.config.lightweight_rerank_enabled ? '轻量' : '无' }}{{ group.config.rerank_enabled ? ' + 模型' : '' }}</td>
                <td>{{ formatRate(configResult(index)?.citation_hit_rate) }}</td>
                <td>{{ formatRate(configResult(index)?.accuracy) }}</td>
                <td>{{ formatRate(configResult(index)?.reject_rate) }}</td>
                <td>{{ formatDuration(configResult(index)?.avg_retrieval_ms) }}</td>
                <td>
                  <span class="experiment-status-inline" :class="statusClass(detail.status)">
                    <i aria-hidden="true" />{{ configStatus(index) }}
                  </span>
                </td>
              </tr>
            </tbody>
          </table>
        </div>

        <div v-if="expandedConfigIndex !== undefined" class="experiment-cases">
          <h3>{{ detailConfigGroups[expandedConfigIndex]?.label }} 组逐题检索结果（{{ selectedConfigResults.length }}）</h3>
          <p class="experiment-muted">以下段号来自该组重新切分后的实验 Chunk，不对应原评测集中的来源段号。</p>
          <p v-if="selectedConfigResults.length === 0" class="experiment-muted">此配置尚无逐题结果。</p>
          <details v-for="(result, resultIndex) in selectedConfigResults" :key="result.case_id" class="experiment-case" :open="resultIndex === 0">
            <summary class="experiment-case-summary">
              <span class="experiment-case-chevron" aria-hidden="true">⌄</span>
              <strong>{{ detailConfigGroups[expandedConfigIndex]?.label }} 组逐题结果</strong>
              <span>（{{ selectedConfigResults.length }}）</span>
              <span>· 用例 #{{ result.case_id }}</span>
              <span>· 引用命中：<b>{{ result.source_hit === null ? '—' : result.source_hit ? '是' : '否' }}</b></span>
              <span>· 正确：<b>{{ result.correct === null ? '—' : result.correct ? '是' : '否' }}</b></span>
            </summary>
            <div class="experiment-case-body">
              <div class="experiment-case-heading">
                <span :class="result.status === 'FAILED' ? 'experiment-failed' : 'experiment-completed'">{{ result.status === 'FAILED' ? '单题失败' : '已完成' }}</span>
                <span>拒答：{{ result.refused === null ? '—' : result.refused ? '是' : '否' }}</span>
                <span>{{ formatDuration(result.retrieval_ms) }}</span>
              </div>
              <p v-if="result.error_message" class="experiment-error">{{ result.error_message }}</p>
              <p v-if="result.retrieved_sources.length === 0" class="experiment-muted">没有命中来源。</p>
              <ul v-else class="experiment-sources">
                <li v-for="source in result.retrieved_sources" :key="source.chunk_id">
                  <strong>{{ source.file_name }} · 实验分块第 {{ source.chunk_no }} 段</strong>
                  <span>排序 {{ source.rank_no }} · 检索分 {{ source.retrieval_score.toFixed(3) }} · 轻量分 {{ source.lightweight_score.toFixed(3) }} · {{ source.evidence_origin === 'NEIGHBOR' ? '邻接扩展' : '主召回' }}<template v-if="source.rerank_score !== null"> · 模型分 {{ source.rerank_score.toFixed(3) }}</template></span>
                  <p>{{ source.content }}</p>
                </li>
              </ul>
            </div>
          </details>
        </div>
      </template>
    </section>

    <ElDialog v-model="strategyEditorVisible" :title="strategyEditorTitle" width="640px" class="experiment-dialog">
      <form class="strategy-editor" @submit.prevent="saveStrategy">
        <label>
          <span>策略名称</span>
          <ElInput v-model="strategyDraft.name" maxlength="100" placeholder="例如：劳动合同高召回" />
        </label>
        <label>
          <span>使用说明</span>
          <ElInput v-model="strategyDraft.description" maxlength="255" placeholder="说明这组参数适合什么问题" />
        </label>
        <div class="strategy-editor-grid">
          <label><span>Chunk Size</span><ElInputNumber v-model="strategyDraft.config.chunk_size" :min="100" :max="2000" :step="50" controls-position="right" /></label>
          <label><span>Overlap</span><ElInputNumber v-model="strategyDraft.config.chunk_overlap" :min="0" :max="500" :step="10" controls-position="right" /></label>
          <label><span>Candidate Top-k</span><ElInputNumber v-model="strategyDraft.config.candidate_k" :min="5" :max="50" controls-position="right" /></label>
          <label><span>Final Top-k</span><ElInputNumber v-model="strategyDraft.config.top_k" :min="1" :max="20" controls-position="right" /></label>
          <label><span>Rerank Top-N</span><ElInputNumber v-model="strategyDraft.config.rerank_top_n" :min="1" :max="20" controls-position="right" /></label>
          <label><span>邻接扩展 Top-N</span><ElInputNumber v-model="strategyDraft.config.neighbor_expand_top_n" :min="0" :max="10" controls-position="right" /></label>
          <label><span>邻接半径</span><ElInputNumber v-model="strategyDraft.config.neighbor_radius" :min="0" :max="2" controls-position="right" /></label>
          <label><span>Evidence 上限</span><ElInputNumber v-model="strategyDraft.config.evidence_limit" :min="5" :max="20" controls-position="right" /></label>
          <label><span>阈值</span><ElInputNumber v-model="strategyDraft.config.score_threshold" :min="0" :max="1" :step="0.05" :precision="2" controls-position="right" /></label>
          <label class="strategy-editor-switch"><span>启用轻量重排</span><ElSwitch v-model="strategyDraft.config.lightweight_rerank_enabled" /></label>
          <label class="strategy-editor-switch"><span>启用旧模型重排</span><ElSwitch v-model="strategyDraft.config.rerank_enabled" /></label>
        </div>
        <p class="experiment-inline-hint">Overlap 必须小于 Chunk Size；Candidate Top-k 不得小于 Final Top-k；Evidence 上限和邻接扩展 Top-N 受 Final Top-k 约束。4C4G 环境建议关闭旧模型重排。</p>
        <p v-if="strategyDraftWarning" class="experiment-warning">{{ strategyDraftWarning }}</p>
        <p v-if="strategyEditorError" class="experiment-error" role="alert">{{ strategyEditorError }}</p>
        <div class="strategy-editor-actions">
          <ElButton text native-type="button" @click="resetStrategyDraft">恢复默认参数</ElButton>
          <ElButton native-type="button" @click="strategyEditorVisible = false">取消</ElButton>
          <ElButton class="experiment-primary" type="primary" native-type="submit" :loading="strategySaving">保存策略</ElButton>
        </div>
      </form>
    </ElDialog>

    <ElDialog
      v-model="strategyVersionVisible"
      :title="`${strategyVersionStrategy?.name ?? '检索策略'} · 版本记录`"
      width="720px"
      class="experiment-dialog"
    >
      <p v-if="strategyVersionError" class="experiment-error" role="alert">{{ strategyVersionError }}</p>
      <p v-if="strategyVersionLoading" class="experiment-muted">正在读取版本记录…</p>
      <div v-else-if="strategyVersions.length === 0" class="experiment-empty">暂无版本记录。</div>
      <div v-else class="strategy-version-list">
        <article v-for="version in strategyVersions" :key="version.id" class="strategy-version-item">
          <div>
            <strong>版本 {{ version.version }}</strong>
            <span>{{ formatCreatedAt(version.created_at) }} · {{ version.name }}</span>
            <p>{{ version.description || '未填写说明' }}</p>
          </div>
          <ElButton
            v-if="!strategyVersionStrategy?.is_builtin && version.version !== strategyVersionStrategy?.version"
            text
            :loading="restoringStrategyVersion === version.version"
            @click="restoreStrategyVersion(version)"
          >恢复</ElButton>
          <span v-else class="strategy-version-current">当前版本</span>
        </article>
      </div>
    </ElDialog>


  </section>
</template>

<style scoped>
.experiment-page {
  display: grid;
  width: min(100%, 1100px);
  margin: 0 auto;
  gap: 12px;
  color: #263548;
}

.experiment-panel {
  min-width: 0;
  padding: 14px 14px 12px;
  background: #fff;
  border: 1px solid #e1e9e4;
  border-radius: 9px;
}

.preview-form {
  display: grid;
  grid-template-columns: minmax(220px, 0.75fr) minmax(0, 2fr) 132px;
  align-items: end;
  gap: 12px;
}

.preview-form label {
  display: grid;
  min-width: 0;
  gap: 7px;
  color: #758195;
  font-size: 12px;
  line-height: 1.35;
}

.preview-form label > span {
  display: block;
  min-height: 16px;
}

.preview-question-field {
  width: 100%;
}

.preview-question-field :deep(.el-input.preview-question-input) {
  display: inline-flex;
  width: 100%;
  min-width: 0;
}

.preview-question-field :deep(.preview-question-input .el-input__wrapper) {
  width: 100%;
  min-height: 38px;
  padding: 0 10px;
  color: #354257;
  background: #fff;
  border: 1px solid #d8e0da;
  border-radius: 7px;
  box-shadow: none;
  font: inherit;
}

.preview-question-field :deep(.preview-question-input .el-input__inner) {
  color: #354257;
  font: inherit;
}

.preview-question-field :deep(.preview-question-input .el-input__inner::placeholder) {
  color: #a0a9b5;
}

.preview-submit {
  align-self: end;
  width: 132px;
  min-height: 38px;
  height: 38px;
  margin-bottom: 0;
}

.preview-limitations {
  margin: 12px 0 0;
  padding: 9px 11px;
  color: #8793a4;
  background: #f3f8f5;
  border: 1px solid #e3eee8;
  border-radius: 7px;
  font-size: 12px;
  line-height: 1.5;
}

.preview-result-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-top: 12px;
  padding: 9px 11px;
  background: #f7fbf8;
  border: 1px solid #e0ede5;
  border-radius: 8px;
}

.preview-result-identity {
  display: flex;
  min-width: 0;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  color: #315447;
  font-size: 12px;
}

.preview-result-identity strong {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.preview-result-identity span {
  flex: 0 0 auto;
  color: #8793a4;
  font-size: 11px;
}

.preview-result-actions {
  display: flex;
  flex: 0 0 auto;
  align-items: center;
  gap: 4px;
}

.preview-result-actions :deep(.el-button) {
  padding: 3px 6px;
  color: #26705d;
  font-size: 11px;
}

.preview-result-content {
  display: grid;
  gap: 12px;
  margin-top: 10px;
}

.experiment-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 20px;
  margin-bottom: 11px;
}

.experiment-heading-actions {
  display: flex;
  align-items: center;
  gap: 7px;
  flex: 0 0 auto;
}

.experiment-heading h2 {
  margin: 0;
  color: #20352d;
  font-size: 18px;
  font-weight: 700;
  line-height: 1.3;
}

.experiment-heading p {
  margin: 5px 0 0;
  color: #758195;
  font-size: 12px;
  line-height: 1.45;
}

.experiment-table-wrap {
  margin-top: 10px;
  overflow-x: auto;
}

.experiment-table {
  width: 100%;
  min-width: 900px;
  border-spacing: 0;
  text-align: left;
}

.experiment-table th,
.experiment-table td {
  padding: 7px 10px;
  border-bottom: 1px solid #edf0f1;
  vertical-align: middle;
}

.experiment-table th {
  color: #68758a;
  background: #f4f6f5;
  font-size: 12px;
  font-weight: 650;
  line-height: 1.35;
  white-space: nowrap;
}

.experiment-table th:first-child {
  border-radius: 6px 0 0 6px;
}

.experiment-table th:last-child {
  border-radius: 0 6px 6px 0;
}

.experiment-table td {
  color: #354257;
  font-size: 12px;
  line-height: 1.45;
}

.experiment-config-table {
  table-layout: fixed;
  text-align: center;
}

.experiment-config-table th,
.experiment-config-table td {
  text-align: center;
}

.experiment-config-table th:first-child,
.experiment-config-table td:first-child { width: 9%; }
.experiment-config-table th:nth-child(2),
.experiment-config-table td:nth-child(2) { width: 17%; }
.experiment-config-table th:nth-child(3),
.experiment-config-table td:nth-child(3) { width: 15%; }
.experiment-config-table th:nth-child(4),
.experiment-config-table td:nth-child(4) { width: 15%; }
.experiment-config-table th:nth-child(5),
.experiment-config-table td:nth-child(5) { width: 15%; }
.experiment-config-table th:nth-child(6),
.experiment-config-table td:nth-child(6) { width: 17%; }
.experiment-config-table th:nth-child(7),
.experiment-config-table td:nth-child(7) { width: 12%; }

.experiment-config-table tbody th {
  color: #354257;
  background: #fff;
  font-size: 12px;
}

.experiment-primary {
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
  box-shadow: none;
}

.experiment-primary:hover:not(:disabled) {
  background: #0f523c;
  border-color: #0f523c;
}

.experiment-primary:disabled {
  cursor: wait;
  opacity: 0.65;
}

.experiment-create-layout {
  display: block;
}

.experiment-create-layout .experiment-heading {
  margin: 0 0 14px;
}

.experiment-create-form {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  align-items: start;
  gap: 18px 24px;
}

.experiment-create-form label {
  display: grid;
  min-width: 0;
  align-content: start;
  gap: 6px;
  color: #758195;
  font-size: 12px;
  line-height: 1.35;
}

.experiment-create-form label > span {
  min-height: 16px;
}

.experiment-create-form input,
.experiment-create-form select {
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

.experiment-create-input {
  display: inline-flex;
  width: 100%;
  min-width: 0;
}

.experiment-create-select {
  display: block;
  width: 100%;
  min-width: 0;
}

.experiment-name-field {
  grid-column: 1;
  grid-row: 1;
}

.experiment-strategy-field {
  grid-column: 1;
  grid-row: 2;
}

.experiment-case-scope-field {
  grid-column: 2;
  grid-row: 1;
}

.experiment-answer-style-field {
  grid-column: 2;
  grid-row: 2;
}

.experiment-case-select-label {
  grid-column: 1 / -1;
  grid-row: auto;
}

.experiment-create-actions {
  display: flex;
  grid-column: 1 / -1;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  margin-top: 4px;
  padding-top: 14px;
  border-top: 1px solid #edf2ef;
}

.experiment-estimate {
  margin: 0;
  color: #536077;
  font-size: 12px;
  line-height: 1.5;
}

.experiment-create-action-end {
  display: flex;
  min-width: 0;
  align-items: center;
  justify-content: flex-end;
  gap: 16px;
  margin-left: auto;
}

.experiment-create-hint {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  color: #8793a4;
  font-size: 12px;
  line-height: 1.5;
  white-space: nowrap;
}

.experiment-create-hint :deep(svg) {
  width: 18px;
  height: 18px;
  color: #d59a4b;
}

.experiment-create-form .experiment-primary {
  flex: 0 0 auto;
  width: 132px;
  min-width: 108px;
  min-height: 38px;
  height: 38px;
  padding: 0 12px;
  white-space: nowrap;
}

.experiment-create-input :deep(.el-input__wrapper),
.experiment-create-select :deep(.el-select__wrapper) {
  min-height: 38px;
  padding: 0 10px;
  color: #354257;
  border: 1px solid #d8e0da;
  border-radius: 7px;
  box-shadow: none;
  font: inherit;
}

.experiment-create-input :deep(.el-input__inner) {
  color: #354257;
  font: inherit;
}

.experiment-create-input :deep(.el-input__inner::placeholder),
.experiment-create-select :deep(.el-select__placeholder) {
  color: #a0a9b5;
}

.experiment-create-form input::placeholder {
  color: #a0a9b5;
}

.experiment-list-table {
  min-width: 900px;
  table-layout: fixed;
}

.experiment-list-table th:nth-child(1),
.experiment-list-table td:nth-child(1) { width: 42px; }
.experiment-list-table th:nth-child(3),
.experiment-list-table td:nth-child(3) { width: 140px; }
.experiment-list-table th:nth-child(4),
.experiment-list-table td:nth-child(4) { width: 140px; }
.experiment-list-table th:nth-child(5),
.experiment-list-table td:nth-child(5) { width: 180px; }
.experiment-list-table th:nth-child(6),
.experiment-list-table td:nth-child(6) { width: 190px; }

.experiment-actions-header,
.experiment-actions-cell {
  text-align: center;
}

.experiment-action-group {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 4px;
  white-space: nowrap;
}

.experiment-action-group :deep(.el-button) {
  flex: 0 0 auto;
  margin: 0;
}

.experiment-action-group :deep(.el-button + .el-button) {
  margin-left: 0;
}

.experiment-select-cell {
  text-align: center;
}

.experiment-table input[type='radio'] {
  width: 17px;
  height: 17px;
  margin: 0;
  accent-color: #146348;
  cursor: pointer;
}

.experiment-list-table tbody tr {
  cursor: pointer;
}

.experiment-list-table tbody tr:hover,
.experiment-row-selected {
  background: #f0f7f4;
}

.experiment-row-archived {
  opacity: 0.72;
}

.experiment-link {
  padding: 0;
  color: #185b44;
  background: none;
  border: 0;
  cursor: pointer;
  font: inherit;
  font-weight: 650;
  text-align: left;
}

.experiment-status-inline {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  white-space: nowrap;
}

.experiment-status-inline i {
  display: block;
  width: 9px;
  height: 9px;
  flex: 0 0 auto;
  border-radius: 50%;
  background: currentColor;
}

.experiment-status-completed,
.experiment-completed { color: #168052; }
.experiment-status-running { color: #3175d6; }
.experiment-status-pending { color: #a47b22; }
.experiment-status-failed,
.experiment-failed { color: #c45656; }

.experiment-status {
  padding-top: 4px;
  font-size: 13px;
  font-weight: 650;
  white-space: nowrap;
}

.experiment-pagination {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  flex-wrap: wrap;
  gap: 9px;
  margin-top: 10px;
  color: #758195;
  font-size: 12px;
}

.experiment-archived-label {
  margin-left: 6px;
  padding: 2px 5px;
  color: #758195;
  background: #f1f3f2;
  border-radius: 4px;
  font-size: 10px;
}

.experiment-pagination button {
  min-height: 30px;
  padding: 0 11px;
  color: #536077;
  background: #fff;
  border: 1px solid #d8e0da;
  border-radius: 6px;
  cursor: pointer;
  font: inherit;
}

.experiment-pagination button:hover:not(:disabled) {
  color: #185b44;
  border-color: #a9c8b9;
}

.experiment-pagination button:disabled {
  color: #b4bbc5;
  cursor: default;
}

.experiment-detail-panel {
  padding-top: 16px;
}

.experiment-detail-heading {
  align-items: flex-start;
}

.experiment-signature,
.experiment-muted {
  margin: 5px 0 0;
  color: #8a95a5;
  font-size: 12px;
  line-height: 1.6;
}

.experiment-summary-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  align-items: center;
  gap: 18px;
  padding: 9px 0 12px;
  border-bottom: 1px solid #e7ece9;
}

.experiment-progress {
  display: grid;
  grid-template-columns: auto minmax(120px, 1fr) auto auto;
  align-items: center;
  gap: 10px;
  min-width: 0;
  color: #758195;
  font-size: 12px;
}

.experiment-progress progress {
  width: 100%;
  min-width: 90px;
  height: 9px;
  accent-color: #126047;
}

.experiment-progress strong {
  color: #354257;
  font-weight: 500;
  white-space: nowrap;
}

.experiment-progress-percent {
  color: #718096;
  white-space: nowrap;
}

.experiment-best {
  margin: 0;
  padding: 8px 13px;
  color: #26705d;
  background: #f0f7f4;
  border-radius: 999px;
  font-size: 12px;
  font-weight: 650;
  white-space: nowrap;
}

.experiment-best strong {
  color: #155a42;
  font-size: 14px;
}

.experiment-conclusion {
  margin: 12px 0;
  padding: 12px 14px;
  background: #f7faf8;
  border: 1px solid #dfece4;
  border-radius: 8px;
}

.experiment-conclusion h3 {
  margin: 0 0 7px;
  color: #315447;
  font-size: 13px;
}

.experiment-conclusion p {
  margin: 4px 0 0;
  color: #536077;
  font-size: 12px;
  line-height: 1.55;
}

.experiment-results-table {
  min-width: 900px;
  table-layout: fixed;
}

.experiment-results-table th:nth-child(1),
.experiment-results-table td:nth-child(1) { width: 11%; }
.experiment-results-table th:nth-child(2),
.experiment-results-table td:nth-child(2) { width: 22%; }
.experiment-results-table th:nth-child(3),
.experiment-results-table td:nth-child(3) { width: 14%; }
.experiment-results-table th:nth-child(4),
.experiment-results-table td:nth-child(4) { width: 12%; }
.experiment-results-table th:nth-child(5),
.experiment-results-table td:nth-child(5) { width: 12%; }
.experiment-results-table th:nth-child(6),
.experiment-results-table td:nth-child(6) { width: 17%; }
.experiment-results-table th:nth-child(7),
.experiment-results-table td:nth-child(7) { width: 12%; }

.experiment-results-table th:first-child,
.experiment-results-table td:first-child {
  min-width: 0;
}

.experiment-results-table .experiment-link {
  display: block;
  width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.experiment-results-table tbody tr {
  cursor: pointer;
}

.experiment-results-table tbody tr:hover,
.experiment-results-table tbody tr.experiment-row-selected,
.experiment-results-table tbody tr.experiment-row-best {
  background: #f0f7f4;
}

.experiment-results-table tbody tr.experiment-row-selected.experiment-row-best {
  background: #e8f3ed;
}

.experiment-cases {
  margin-top: 17px;
}

.experiment-cases h3 {
  margin: 0 0 7px;
  color: #20352d;
  font-size: 15px;
}

.experiment-case {
  margin-top: 8px;
  border: 1px solid #e0e8e3;
  border-radius: 7px;
  overflow: hidden;
}

.experiment-case-summary {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 6px;
  padding: 10px 12px;
  color: #536077;
  cursor: pointer;
  font-size: 12px;
  list-style: none;
}

.experiment-case-summary::-webkit-details-marker {
  display: none;
}

.experiment-case-summary strong {
  color: #354257;
}

.experiment-case-summary b {
  color: #168052;
  font-weight: 650;
}

.experiment-case-chevron {
  color: #185b44;
  font-size: 18px;
  line-height: 0.7;
  transition: transform 0.15s ease;
}

.experiment-case:not([open]) .experiment-case-chevron {
  transform: rotate(-90deg);
}

.experiment-case-body {
  display: grid;
  gap: 9px;
  padding: 0 12px 12px;
  border-top: 1px solid #edf0f1;
}

.experiment-case-heading {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 7px 12px;
  padding-top: 9px;
  color: #667286;
  font-size: 12px;
}

.experiment-sources {
  display: grid;
  gap: 10px;
  margin: 0;
  padding-left: 18px;
  color: #4e5a6b;
  font-size: 12px;
}

.experiment-sources li {
  display: grid;
  gap: 4px;
}

.experiment-sources li > span {
  color: #758195;
  font-size: 11px;
}

.experiment-sources p {
  margin: 0;
  line-height: 1.6;
  white-space: pre-wrap;
}

.experiment-error {
  margin: 9px 0;
  padding: 8px 10px;
  color: #a63c3c;
  background: #fff4f2;
  border: 1px solid #f0d5d1;
  border-radius: 7px;
  font-size: 12px;
  line-height: 1.5;
}

.experiment-empty {
  padding: 24px !important;
  color: #8a95a5 !important;
  text-align: center;
}

.experiment-secondary {
  min-height: 34px;
  padding: 0 13px;
  color: #126047 !important;
  background: #eff7f2 !important;
  border-color: #a9c8b9 !important;
  border-radius: 7px;
  font-size: 12px;
  font-weight: 650;
}

.experiment-secondary:hover:not(.is-disabled),
.experiment-secondary:focus:not(.is-disabled),
.experiment-secondary:active:not(.is-disabled) {
  color: #0f523c !important;
  background: #e4f2ea !important;
  border-color: #8fc0ab !important;
  box-shadow: 0 4px 10px rgb(18 96 71 / 10%);
}

.experiment-secondary.is-disabled,
.experiment-secondary.is-disabled:hover {
  color: #9bb4a7 !important;
  background: #f3f7f5 !important;
  border-color: #dbe7e0 !important;
  box-shadow: none;
}

.experiment-inline-hint {
  margin: 0 0 9px;
  color: #8793a4;
  font-size: 12px;
  line-height: 1.5;
}

.experiment-warning {
  margin: -6px 0 0;
  color: #a47726;
  font-size: 12px;
  line-height: 1.5;
}

.strategy-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 10px;
}

.strategy-card {
  display: flex;
  min-width: 0;
  flex-direction: column;
  padding: 12px;
  background: #fbfdfc;
  border: 1px solid #e2ebe6;
  border-radius: 8px;
  cursor: pointer;
  transition: border-color 0.15s ease, box-shadow 0.15s ease;
}

.strategy-card:focus-visible {
  outline: 2px solid #76ad92;
  outline-offset: 2px;
}

.strategy-card-disabled {
  cursor: not-allowed;
  opacity: 0.68;
  background: #f5f7f6;
}

.strategy-card-preview-selected {
  border-color: #76ad92;
  background: #f7fbf8;
  box-shadow: 0 4px 14px rgb(18 96 71 / 10%);
}

.strategy-card-select {
  display: flex;
  min-width: 0;
  align-items: center;
  gap: 7px;
  color: #20352d;
  font-size: 13px;
  font-weight: 700;
}

.strategy-card-select > span {
  display: block;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.strategy-card-select em {
  color: #8a9692;
  font-size: 10px;
  font-style: normal;
  font-weight: 500;
}

.strategy-version-list {
  display: grid;
  gap: 8px;
}

.strategy-version-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 10px 12px;
  background: #fafcfb;
  border: 1px solid #e2ebe6;
  border-radius: 7px;
}

.strategy-version-item > div {
  display: grid;
  min-width: 0;
  gap: 3px;
}

.strategy-version-item strong {
  color: #315447;
  font-size: 12px;
}

.strategy-version-item span,
.strategy-version-item p {
  overflow: hidden;
  margin: 0;
  color: #8793a4;
  font-size: 11px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.strategy-version-current {
  flex: 0 0 auto;
  color: #26705d !important;
  font-weight: 650;
}

.strategy-card p {
  min-height: 34px;
  margin: 8px 0;
  color: #7a8798;
  font-size: 11px;
  line-height: 1.5;
}

.strategy-card-meta {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 5px;
  margin: 0;
}

.strategy-card-meta div {
  min-width: 0;
  padding: 5px 6px;
  background: #f2f7f4;
  border-radius: 5px;
}

.strategy-card-meta dt {
  color: #8793a4;
  font-size: 10px;
}

.strategy-card-meta dd {
  overflow: hidden;
  margin: 2px 0 0;
  color: #315447;
  font-size: 11px;
  font-weight: 650;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.strategy-card-actions {
  display: flex;
  justify-content: flex-end;
  gap: 2px;
  margin-top: 8px;
  cursor: default;
}

.strategy-card-actions :deep(.el-button) {
  padding: 2px 5px;
  font-size: 11px;
}

.experiment-table-action {
  min-width: 32px;
  padding: 2px 4px;
  font-size: 11px;
}

.strategy-editor {
  display: grid;
  gap: 14px;
}

.strategy-editor > label,
.strategy-editor-grid label {
  display: grid;
  gap: 6px;
  color: #536077;
  font-size: 12px;
  font-weight: 650;
}

.strategy-editor-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 12px;
}

.strategy-editor-grid :deep(.el-input-number) {
  width: 100%;
}

.strategy-editor-switch {
  align-items: start;
}

.strategy-editor-actions {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 9px;
}

.strategy-editor-actions {
  padding-top: 5px;
  border-top: 1px solid #edf1ee;
}

.preview-loading {
  padding: 20px;
  color: #7a8798;
  text-align: center;
}

.preview-answer {
  padding: 12px 14px;
  background: #f1f8f4;
  border: 1px solid #d6e9dd;
  border-radius: 8px;
}

.preview-answer-refused {
  background: #fff8ee;
  border-color: #f0dfbd;
}

.preview-answer-heading {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  color: #185b44;
  font-size: 12px;
}

.preview-answer-heading span {
  color: #8a95a5;
  font-weight: 400;
}

.preview-answer-markdown {
  margin-top: 8px;
  color: #354257;
  font-size: 13px;
  line-height: 1.7;
  overflow-wrap: anywhere;
}

.preview-answer-markdown > * {
  margin: 0;
}

.preview-answer-markdown > * + * {
  margin-top: 0.75em;
}

.preview-answer-markdown :deep(ul),
.preview-answer-markdown :deep(ol) {
  padding-left: 1.45em;
}

.preview-trace h3 {
  margin: 0 0 8px;
  color: #20352d;
  font-size: 13px;
}

.preview-trace ol {
  display: grid;
  gap: 7px;
  margin: 0;
  padding: 0;
  list-style: none;
}

.preview-trace li {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  padding: 7px 9px;
  background: #fafcfb;
  border: 1px solid #edf1ee;
  border-radius: 6px;
}

.preview-trace-dot {
  width: 8px;
  height: 8px;
  flex: 0 0 auto;
  margin-top: 4px;
  border-radius: 50%;
  background: #199267;
}

.preview-trace-skipped { background: #b7a36c; }
.preview-trace-failed { background: #c45656; }

.preview-trace li div {
  display: grid;
  gap: 2px;
}

.preview-trace li strong {
  color: #354257;
  font-size: 12px;
}

.preview-trace li span:not(.preview-trace-dot) {
  color: #8793a4;
  font-size: 11px;
}

.preview-sources {
  border-top: 1px solid #edf1ee;
  padding-top: 10px;
}

.preview-sources summary {
  color: #26705d;
  cursor: pointer;
  font-size: 12px;
  font-weight: 650;
}

.preview-sources ul {
  display: grid;
  gap: 7px;
  margin: 9px 0 0;
  padding: 0;
  list-style: none;
}

.preview-sources li {
  padding: 8px 10px;
  background: #fafcfb;
  border: 1px solid #edf1ee;
  border-radius: 6px;
}

.preview-sources li strong,
.preview-sources li span {
  display: block;
  font-size: 11px;
}

.preview-sources li strong { color: #354257; }
.preview-sources li span { margin-top: 3px; color: #8793a4; }
.preview-sources li p { margin: 5px 0 0; color: #536077; font-size: 11px; line-height: 1.55; }

@media (max-width: 820px) {
  .strategy-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .strategy-editor-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .experiment-create-form {
    grid-template-columns: 1fr;
  }

  .experiment-name-field,
  .experiment-strategy-field,
  .experiment-case-scope-field,
  .experiment-answer-style-field,
  .experiment-case-select-label,
  .experiment-create-actions {
    grid-column: 1;
    grid-row: auto;
  }

  .experiment-create-form .experiment-primary {
    width: 108px;
  }

  .experiment-summary-row {
    grid-template-columns: 1fr;
    gap: 10px;
  }

  .experiment-best {
    justify-self: start;
  }
}

@media (max-width: 620px) {
  .experiment-page {
    width: 100%;
    gap: 10px;
  }

  .experiment-panel {
    padding: 12px 10px 10px;
  }

  .strategy-grid,
  .strategy-editor-grid {
    grid-template-columns: 1fr;
  }

  .experiment-heading-actions {
    align-self: flex-start;
  }

  .experiment-create-form {
    grid-template-columns: 1fr;
  }

  .experiment-create-form label {
    white-space: normal;
  }

  .experiment-create-actions {
    align-items: stretch;
    flex-direction: column;
    gap: 10px;
  }

  .experiment-create-action-end {
    width: 100%;
    margin-left: 0;
    justify-content: space-between;
  }

  .experiment-create-form .experiment-primary {
    width: 100%;
  }

  .preview-form {
    grid-template-columns: 1fr;
  }

  .preview-submit {
    width: 100%;
  }

  .preview-result-toolbar {
    align-items: flex-start;
    flex-direction: column;
  }

  .preview-result-actions {
    align-self: flex-end;
  }

  .experiment-progress {
    grid-template-columns: auto 1fr auto;
  }

  .experiment-progress progress {
    grid-column: 1 / -1;
    grid-row: 2;
  }

  .experiment-progress strong {
    grid-column: 2;
  }

  .experiment-progress-percent {
    grid-column: 3;
    grid-row: 1;
  }
}
</style>
