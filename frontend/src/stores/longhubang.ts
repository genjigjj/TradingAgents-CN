/**
 * 龙虎榜状态管理
 *
 * 使用 Pinia Composition API 管理龙虎榜页面状态，
 * 包括分析参数、数据概况、评分排名、AI 报告、推荐股票、历史报告和统计数据。
 */

import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import {
  longhubangApi,
  type LonghubangAnalyzeRequest,
  type LonghubangDataInfo,
  type ScoringResult,
  type LonghubangRecommendedStock,
  type AgentAnalysis,
  type LonghubangReport,
  type LonghubangStatistics,
  type LonghubangRecord,
  type TopYouziItem,
  type TopStockItem,
} from '@/api/longhubang'
import { analysisApi } from '@/api/analysis'

export const useLonghubangStore = defineStore('longhubang', () => {
  // ============================================================
  // 分析参数
  // ============================================================

  /** 分析模式: date / days */
  const analyzeMode = ref<string>('date')

  /** 指定日期 */
  const analyzeDate = ref<string | null>(null)

  /** 最近天数 */
  const analyzeDays = ref<number>(1)

  /** 用户选择的 LLM 模型（为空时自动选择） */
  const selectedModel = ref<string | null>(null)

  /** 可用模型列表（按厂家分组） */
  const availableModels = ref<Array<{
    provider: string
    provider_name: string
    models: Array<{ name: string; display_name: string }>
  }>>([])

  // ============================================================
  // 分析结果状态
  // ============================================================

  /** 分析任务 ID */
  const taskId = ref<string | null>(null)

  /** 分析状态 */
  const analysisStatus = ref<'idle' | 'fetching' | 'analyzing' | 'completed' | 'failed'>('idle')

  /** 分析进度 */
  const progress = ref(0)
  const currentStep = ref('')

  /** 数据概况 */
  const dataInfo = ref<LonghubangDataInfo | null>(null)

  /** 数据日期范围 */
  const dateRange = ref<string>('')

  /** 评分排名列表 */
  const scoringRanking = ref<ScoringResult[]>([])

  /** AI 分析师报告 */
  const agentsAnalysis = ref<Record<string, AgentAnalysis>>({})

  /** 推荐股票列表 */
  const recommendedStocks = ref<LonghubangRecommendedStock[]>([])

  /** 分析摘要 */
  const summary = ref<string>('')

  /** 加载状态 */
  const analyzeLoading = ref(false)

  /** 错误信息 */
  const errorMessage = ref<string | null>(null)

  // ============================================================
  // 历史报告状态
  // ============================================================

  /** 历史报告列表 */
  const reports = ref<LonghubangReport[]>([])
  const reportsTotal = ref(0)
  const reportsPage = ref(1)
  const reportsPageSize = ref(20)
  const reportsLoading = ref(false)

  // ============================================================
  // 统计数据状态
  // ============================================================

  /** 数据库统计信息 */
  const statistics = ref<LonghubangStatistics | null>(null)
  const statisticsLoading = ref(false)

  // ============================================================
  // 数据查询状态
  // ============================================================

  /** 历史数据记录 */
  const records = ref<LonghubangRecord[]>([])
  const recordsLoading = ref(false)

  /** 活跃游资排名 */
  const topYouzi = ref<TopYouziItem[]>([])

  /** 热门股票排名 */
  const topStocks = ref<TopStockItem[]>([])

  /** 轮询定时器 */
  let pollTimer: ReturnType<typeof setInterval> | null = null

  // ============================================================
  // 计算属性
  // ============================================================

  /** 是否正在分析中 */
  const isAnalyzing = computed(() =>
    analysisStatus.value === 'fetching' || analysisStatus.value === 'analyzing'
  )

  /** 是否有分析结果 */
  const hasResult = computed(() => analysisStatus.value === 'completed')

  /** 是否有评分数据 */
  const hasScoringData = computed(() => scoringRanking.value.length > 0)

  /** 是否有 AI 报告 */
  const hasAgentsReport = computed(() => Object.keys(agentsAnalysis.value).length > 0)

  /** 评分 TOP10 */
  const scoringTop10 = computed(() => scoringRanking.value.slice(0, 10))

  /** 评分 TOP5（用于雷达图） */
  const scoringTop5 = computed(() => scoringRanking.value.slice(0, 5))

  // ============================================================
  // Actions - 分析
  // ============================================================

  /** 触发龙虎榜分析 */
  async function startAnalysis() {
    analyzeLoading.value = true
    analysisStatus.value = 'fetching'
    progress.value = 0
    currentStep.value = '正在获取龙虎榜数据...'
    errorMessage.value = null

    // 清空之前的结果
    agentsAnalysis.value = {}
    recommendedStocks.value = []
    summary.value = ''
    dataInfo.value = null
    scoringRanking.value = []
    dateRange.value = ''

    try {
      const request: LonghubangAnalyzeRequest = {
        mode: analyzeMode.value,
        date: analyzeMode.value === 'date' ? analyzeDate.value : null,
        days: analyzeDays.value,
        model_name: selectedModel.value,
      }

      const res = await longhubangApi.analyze(request)
      if (res.success && res.data) {
        const data = res.data
        taskId.value = data.task_id
        dataInfo.value = data.data_info
        scoringRanking.value = data.scoring_ranking || []
        dateRange.value = data.date_range || ''

        if (!data.task_id) {
          // 没有数据，分析结束
          analysisStatus.value = 'completed'
          analyzeLoading.value = false
          currentStep.value = data.message || '未获取到龙虎榜数据'
          return
        }

        // 数据和评分已同步返回，开始轮询 AI 分析进度
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
    } catch {
      // 轮询失败不中断，继续下次轮询
    }
  }

  /** 获取任务完整结果 */
  async function fetchTaskResult() {
    if (!taskId.value) return

    try {
      const res = await analysisApi.getTaskResult(taskId.value)
      if (res.data) {
        const result = res.data
        // 提取 AI 分析师报告
        if (result.agents_analysis) {
          agentsAnalysis.value = result.agents_analysis
        }
        // 提取推荐股票
        if (result.recommended_stocks) {
          recommendedStocks.value = result.recommended_stocks
        }
        // 提取摘要
        if (result.summary) {
          summary.value = result.summary
        }
        // 更新评分排名（如果结果中包含更完整的数据）
        if (result.scoring_ranking && result.scoring_ranking.length > 0) {
          scoringRanking.value = result.scoring_ranking
        }
      }
    } catch {
      // 获取结果失败不影响状态
    }
  }

  /** 从历史报告加载结果到当前状态 */
  function loadFromReport(report: LonghubangReport) {
    taskId.value = report.task_id ?? null
    dataInfo.value = report.data_info
    scoringRanking.value = report.scoring_ranking || []
    agentsAnalysis.value = report.agents_analysis || {}
    recommendedStocks.value = report.recommended_stocks || []
    summary.value = report.summary || ''
    dateRange.value = report.data_date_range || ''
    analysisStatus.value = 'completed'
    progress.value = 100
    currentStep.value = '已加载历史报告'
    errorMessage.value = null
  }

  // ============================================================
  // Actions - 历史报告
  // ============================================================

  /** 查询历史报告列表 */
  async function fetchReports(page = 1, pageSize = 20) {
    reportsLoading.value = true
    try {
      const res = await longhubangApi.getReports(page, pageSize)
      if (res.success && res.data) {
        reports.value = res.data.items || []
        reportsTotal.value = res.data.total || 0
        reportsPage.value = page
        reportsPageSize.value = pageSize
      }
    } catch {
      reports.value = []
    } finally {
      reportsLoading.value = false
    }
  }

  /** 获取报告详情 */
  async function fetchReportDetail(reportId: string): Promise<LonghubangReport | null> {
    try {
      const res = await longhubangApi.getReportDetail(reportId)
      if (res.success && res.data) {
        return res.data
      }
      return null
    } catch {
      return null
    }
  }

  /** 删除历史报告 */
  async function deleteReport(reportId: string) {
    try {
      const res = await longhubangApi.deleteReport(reportId)
      if (res.success) {
        reports.value = reports.value.filter(r => r._id !== reportId)
        reportsTotal.value = Math.max(0, reportsTotal.value - 1)
        return true
      }
      return false
    } catch {
      return false
    }
  }

  // ============================================================
  // Actions - 统计数据
  // ============================================================

  /** 获取数据库统计信息 */
  async function fetchStatistics() {
    statisticsLoading.value = true
    try {
      const res = await longhubangApi.getStatistics()
      if (res.success && res.data) {
        statistics.value = res.data
      }
    } catch {
      statistics.value = null
    } finally {
      statisticsLoading.value = false
    }
  }

  // ============================================================
  // Actions - 数据查询
  // ============================================================

  /** 查询龙虎榜历史数据 */
  async function fetchRecords(startDate: string, endDate: string, stockCode?: string) {
    recordsLoading.value = true
    try {
      const res = await longhubangApi.getRecords(startDate, endDate, stockCode)
      if (res.success && res.data) {
        records.value = Array.isArray(res.data) ? res.data : []
      }
    } catch {
      records.value = []
    } finally {
      recordsLoading.value = false
    }
  }

  /** 查询活跃游资排名 */
  async function fetchTopYouzi(startDate: string, endDate: string, limit = 20) {
    try {
      const res = await longhubangApi.getTopYouzi(startDate, endDate, limit)
      if (res.success && res.data) {
        topYouzi.value = Array.isArray(res.data) ? res.data : []
      }
    } catch {
      topYouzi.value = []
    }
  }

  /** 查询热门股票排名 */
  async function fetchTopStocks(startDate: string, endDate: string, limit = 20) {
    try {
      const res = await longhubangApi.getTopStocks(startDate, endDate, limit)
      if (res.success && res.data) {
        topStocks.value = Array.isArray(res.data) ? res.data : []
      }
    } catch {
      topStocks.value = []
    }
  }

  // ============================================================
  // Actions - 通用
  // ============================================================

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
    analyzeMode.value = 'date'
    analyzeDate.value = null
    analyzeDays.value = 1
    taskId.value = null
    analysisStatus.value = 'idle'
    progress.value = 0
    currentStep.value = ''
    dataInfo.value = null
    dateRange.value = ''
    scoringRanking.value = []
    agentsAnalysis.value = {}
    recommendedStocks.value = []
    summary.value = ''
    analyzeLoading.value = false
    errorMessage.value = null
    batchResults.value = null
  }

  // 内部辅助：批量结果（龙虎榜不需要，但保留以防扩展）
  const batchResults = ref<any>(null)

  return {
    // 分析参数
    analyzeMode,
    analyzeDate,
    analyzeDays,
    selectedModel,
    availableModels,

    // 分析结果状态
    taskId,
    analysisStatus,
    progress,
    currentStep,
    dataInfo,
    dateRange,
    scoringRanking,
    agentsAnalysis,
    recommendedStocks,
    summary,
    analyzeLoading,
    errorMessage,

    // 历史报告状态
    reports,
    reportsTotal,
    reportsPage,
    reportsPageSize,
    reportsLoading,

    // 统计数据状态
    statistics,
    statisticsLoading,

    // 数据查询状态
    records,
    recordsLoading,
    topYouzi,
    topStocks,

    // 计算属性
    isAnalyzing,
    hasResult,
    hasScoringData,
    hasAgentsReport,
    scoringTop10,
    scoringTop5,

    // Actions - 分析
    startAnalysis,
    fetchAvailableModels,
    startPolling,
    stopPolling,
    pollProgress,
    fetchTaskResult,
    loadFromReport,

    // Actions - 历史报告
    fetchReports,
    fetchReportDetail,
    deleteReport,

    // Actions - 统计数据
    fetchStatistics,

    // Actions - 数据查询
    fetchRecords,
    fetchTopYouzi,
    fetchTopStocks,

    // Actions - 通用
    resetState,
  }
})
