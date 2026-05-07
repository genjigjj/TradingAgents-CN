/**
 * 主力选股状态管理
 *
 * 使用 Pinia Composition API 管理主力选股页面状态，
 * 包括筛选参数、候选列表、分析结果、进度跟踪和历史记录。
 */

import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import {
  mainForceApi,
  type MainForceScreenParams,
  type MainForceAnalyzeRequest,
  type MainForceCandidate,
  type MainForceAnalyzeResult,
  type MainForceBatchHistory,
} from '@/api/mainForce'
import { analysisApi } from '@/api/analysis'

// ============================================================
// 默认值
// ============================================================

const defaultScreenParams: MainForceScreenParams = {
  time_range: '3m',
  start_date: null,
  end_date: null,
  top_n: 5,
  max_change_pct: 30,
  min_market_cap: 50,
  max_market_cap: 5000,
}

export const useMainForceStore = defineStore('mainForce', () => {
  // ============================================================
  // 状态
  // ============================================================

  /** 筛选参数 */
  const screenParams = ref<MainForceScreenParams>({ ...defaultScreenParams })

  /** 候选股票列表 */
  const candidates = ref<MainForceCandidate[]>([])

  /** 筛选统计 */
  const totalFetched = ref(0)
  const totalFiltered = ref(0)

  /** 分析模式: overview / batch */
  const analysisMode = ref<string>('overview')

  /** 批量分析数量 */
  const batchCount = ref<number>(10)

  /** 研究深度 */
  const researchDepth = ref<string>('标准')

  /** 选择的分析师 */
  const selectedAnalysts = ref<string[]>(['fund_flow', 'industry', 'fundamental'])

  /** 用户选择的 LLM 模型（为空时自动选择） */
  const selectedModel = ref<string | null>(null)

  /** 可用模型列表（按厂家分组） */
  const availableModels = ref<Array<{
    provider: string
    provider_name: string
    models: Array<{ name: string; display_name: string }>
  }>>([])

  /** 整体分析任务 ID */
  const taskId = ref<string | null>(null)

  /** 批量分析 batch ID */
  const batchId = ref<string | null>(null)

  /** 分析进度 */
  const progress = ref(0)
  const currentStep = ref('')
  const analysisStatus = ref<'idle' | 'screening' | 'analyzing' | 'completed' | 'failed'>('idle')

  /** 整体分析结果 */
  const overviewAnalysis = ref<Record<string, string>>({})
  const recommendedStocks = ref<any[]>([])

  /** 批量分析结果 */
  const batchResults = ref<any>(null)

  /** 历史记录 */
  const batchHistory = ref<MainForceBatchHistory[]>([])
  const historyTotal = ref(0)
  const historyPage = ref(1)
  const historyPageSize = ref(20)
  const historyLoading = ref(false)

  /** 加载状态 */
  const screenLoading = ref(false)
  const analyzeLoading = ref(false)

  /** 错误信息 */
  const errorMessage = ref<string | null>(null)

  /** 轮询定时器 */
  let pollTimer: ReturnType<typeof setInterval> | null = null

  /** 轮询失败计数（连续 404 次数） */
  let pollFailCount = 0

  // ============================================================
  // 计算属性
  // ============================================================

  /** 是否正在分析中 */
  const isAnalyzing = computed(() =>
    analysisStatus.value === 'screening' || analysisStatus.value === 'analyzing'
  )

  /** 是否有分析结果 */
  const hasResult = computed(() => analysisStatus.value === 'completed')

  /** 是否有候选股票 */
  const hasCandidates = computed(() => candidates.value.length > 0)

  // ============================================================
  // Actions
  // ============================================================

  /** 重置筛选参数为默认值 */
  function resetScreenParams() {
    screenParams.value = { ...defaultScreenParams }
  }

  /** 仅执行筛选（不触发 AI 分析） */
  async function fetchScreen() {
    screenLoading.value = true
    errorMessage.value = null
    try {
      const res = await mainForceApi.screen(screenParams.value)
      if (res.success && res.data) {
        candidates.value = res.data.candidates
        totalFetched.value = res.data.total_fetched
        totalFiltered.value = res.data.total_filtered
      } else {
        errorMessage.value = res.message || '筛选失败'
      }
    } catch (err: any) {
      errorMessage.value = err.message || '筛选请求失败'
    } finally {
      screenLoading.value = false
    }
  }

  /** 触发完整分析流程（筛选 + AI 分析） */
  async function startAnalysis() {
    analyzeLoading.value = true
    analysisStatus.value = 'screening'
    progress.value = 0
    currentStep.value = '正在筛选候选股票...'
    errorMessage.value = null
    overviewAnalysis.value = {}
    recommendedStocks.value = []
    batchResults.value = null

    try {
      const request: MainForceAnalyzeRequest = {
        params: screenParams.value,
        analysis_mode: analysisMode.value,
        batch_count: analysisMode.value === 'batch' ? batchCount.value : null,
        research_depth: researchDepth.value,
        selected_analysts: selectedAnalysts.value,
        model_name: selectedModel.value,
      }

      const res = await mainForceApi.analyze(request)
      if (res.success && res.data) {
        const data = res.data as MainForceAnalyzeResult
        taskId.value = data.task_id
        batchId.value = data.batch_id ?? null
        totalFetched.value = data.total_fetched
        totalFiltered.value = data.total_filtered

        if (!data.task_id) {
          // 没有候选股票，分析结束
          analysisStatus.value = 'completed'
          analyzeLoading.value = false
          currentStep.value = '未筛选到符合条件的候选股票'
          return
        }

        // 开始轮询进度
        analysisStatus.value = 'analyzing'
        currentStep.value = 'AI 分析师团队分析中...'
        startPolling()
      } else {
        analysisStatus.value = 'failed'
        errorMessage.value = res.message || '分析请求失败'
        analyzeLoading.value = false
      }
    } catch (err: any) {
      analysisStatus.value = 'failed'
      errorMessage.value = err.message || '分析请求失败'
      analyzeLoading.value = false
    }
  }

  /** 开始轮询分析进度 */
  function startPolling() {
    stopPolling()
    pollFailCount = 0
    pollTimer = setInterval(async () => {
      await pollProgress()
    }, 3000)
  }

  /** 停止轮询 */
  function stopPolling() {
    if (pollTimer) {
      clearInterval(pollTimer)
      pollTimer = null
    }
  }

  /** 轮询分析进度 */
  async function pollProgress() {
    if (!taskId.value) return

    try {
      const res = await analysisApi.getTaskStatus(taskId.value)
      if (res.success && res.data) {
        pollFailCount = 0  // 成功时重置失败计数
        const task = res.data
        progress.value = task.progress ?? 0
        currentStep.value = task.step_detail || task.current_step || '分析中...'

        if (task.status === 'completed') {
          stopPolling()
          analysisStatus.value = 'completed'
          analyzeLoading.value = false
          progress.value = 100
          currentStep.value = '分析完成'

          // 获取完整结果
          await fetchTaskResult()
        } else if (task.status === 'failed') {
          stopPolling()
          analysisStatus.value = 'failed'
          analyzeLoading.value = false
          errorMessage.value = task.error_message || '分析任务失败'
          currentStep.value = '分析失败'
        }
      }
    } catch (err: any) {
      // 轮询失败时检查是否是 404（任务不存在）
      const status = err?.response?.status || err?.status
      if (status === 404) {
        pollFailCount++
        if (pollFailCount >= 10) {
          // 连续 10 次 404，认为任务丢失
          stopPolling()
          analysisStatus.value = 'failed'
          analyzeLoading.value = false
          errorMessage.value = '分析任务状态查询失败，任务可能未正常启动'
          currentStep.value = '分析失败'
        }
      }
    }
  }

  /** 获取任务完整结果 */
  async function fetchTaskResult() {
    if (!taskId.value) return

    try {
      const res = await analysisApi.getTaskResult(taskId.value)
      if (res.data) {
        const result = res.data
        // 提取整体分析报告
        if (result.overview_analysis) {
          overviewAnalysis.value = result.overview_analysis
        }
        // 提取推荐股票
        if (result.recommended_stocks) {
          recommendedStocks.value = result.recommended_stocks
        }
        // 提取候选列表（如果结果中包含）
        if (result.candidates) {
          candidates.value = result.candidates
        }
        // 提取批量分析结果
        if (result.batch_results) {
          batchResults.value = result.batch_results
        }
      }
    } catch (err) {
      // 获取结果失败时记录错误，便于排查
      console.error('[主力选股] 获取分析结果失败:', err)
    }
  }

  /** 查询批量分析历史 */
  async function fetchBatchHistory(page = 1, pageSize = 20) {
    historyLoading.value = true
    try {
      const res = await mainForceApi.getBatchHistory(page, pageSize)
      if (res.success && res.data) {
        batchHistory.value = res.data.items || []
        historyTotal.value = res.data.total || 0
        historyPage.value = page
        historyPageSize.value = pageSize
      }
    } catch {
      batchHistory.value = []
    } finally {
      historyLoading.value = false
    }
  }

  /** 删除批量分析历史记录 */
  async function deleteBatchHistory(historyId: string) {
    try {
      const res = await mainForceApi.deleteBatchHistory(historyId)
      if (res.success) {
        // 从列表中移除
        batchHistory.value = batchHistory.value.filter(h => h._id !== historyId)
        historyTotal.value = Math.max(0, historyTotal.value - 1)
        return true
      }
      return false
    } catch {
      return false
    }
  }

  /** 获取可用模型列表 */
  async function fetchAvailableModels() {
    try {
      const { configApi } = await import('@/api/config')
      const models = await configApi.getAvailableModels()
      if (models) {
        availableModels.value = models
      }
    } catch {
      availableModels.value = []
    }
  }

  /** 重置所有状态 */
  function resetState() {
    stopPolling()
    screenParams.value = { ...defaultScreenParams }
    candidates.value = []
    totalFetched.value = 0
    totalFiltered.value = 0
    analysisMode.value = 'overview'
    batchCount.value = 10
    taskId.value = null
    batchId.value = null
    progress.value = 0
    currentStep.value = ''
    analysisStatus.value = 'idle'
    overviewAnalysis.value = {}
    recommendedStocks.value = []
    batchResults.value = null
    errorMessage.value = null
    screenLoading.value = false
    analyzeLoading.value = false
  }

  return {
    // 状态
    screenParams,
    candidates,
    totalFetched,
    totalFiltered,
    analysisMode,
    batchCount,
    researchDepth,
    selectedAnalysts,
    selectedModel,
    availableModels,
    taskId,
    batchId,
    progress,
    currentStep,
    analysisStatus,
    overviewAnalysis,
    recommendedStocks,
    batchResults,
    batchHistory,
    historyTotal,
    historyPage,
    historyPageSize,
    historyLoading,
    screenLoading,
    analyzeLoading,
    errorMessage,

    // 计算属性
    isAnalyzing,
    hasResult,
    hasCandidates,

    // Actions
    resetScreenParams,
    fetchScreen,
    startAnalysis,
    startPolling,
    stopPolling,
    pollProgress,
    fetchTaskResult,
    fetchBatchHistory,
    deleteBatchHistory,
    fetchAvailableModels,
    resetState,
  }
})
