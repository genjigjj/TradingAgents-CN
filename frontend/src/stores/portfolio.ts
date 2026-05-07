/**
 * 持仓分析状态管理
 *
 * 使用 Pinia Composition API 管理持仓分析页面状态，
 * 包括分析结果列表、加载状态、批量分析任务进度跟踪。
 */

import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import {
  portfolioApi,
  type AnalysisResult,
} from '@/api/portfolio'
import { analysisApi } from '@/api/analysis'

export const usePortfolioStore = defineStore('portfolio', () => {
  // ============================================================
  // 状态
  // ============================================================

  /** 所有持仓的最新分析结果列表 */
  const analyses = ref<AnalysisResult[]>([])

  /** 加载状态 */
  const loading = ref(false)

  /** 当前批量分析任务 ID */
  const currentTaskId = ref<string | null>(null)

  /** 批量分析进度百分比 (0-100) */
  const batchProgress = ref(0)

  /** 当前进度步骤描述 */
  const currentStep = ref('')

  /** 错误信息 */
  const errorMessage = ref<string | null>(null)

  /** 轮询定时器 */
  let pollTimer: ReturnType<typeof setInterval> | null = null

  // ============================================================
  // 计算属性
  // ============================================================

  /** 是否正在执行批量分析 */
  const isBatchAnalyzing = computed(() => currentTaskId.value !== null && loading.value)

  /** 是否有分析结果 */
  const hasAnalyses = computed(() => analyses.value.length > 0)

  // ============================================================
  // Actions
  // ============================================================

  /**
   * 获取所有持仓的最新分析结果
   */
  async function fetchLatestAnalyses() {
    loading.value = true
    errorMessage.value = null
    try {
      const res = await portfolioApi.getLatestAnalyses()
      if (res.success && res.data) {
        analyses.value = res.data.items || []
      } else {
        errorMessage.value = res.message || '获取分析结果失败'
      }
    } catch (err: any) {
      errorMessage.value = err.message || '获取分析结果失败'
    } finally {
      loading.value = false
    }
  }

  /**
   * 对单只持仓股票执行 AI 分析
   *
   * @param code 股票代码
   * @param params 可选参数：市场类型和模型名称
   * @returns 分析结果，失败时返回 null
   */
  async function analyzeSingle(
    code: string,
    params?: { market?: string; model_name?: string }
  ): Promise<AnalysisResult | null> {
    loading.value = true
    errorMessage.value = null
    try {
      const res = await portfolioApi.analyzeSingle(code, params)
      if (res.success && res.data) {
        // 更新本地列表中对应股票的分析结果
        const idx = analyses.value.findIndex(a => a.position_code === code)
        if (idx >= 0) {
          analyses.value[idx] = res.data
        } else {
          analyses.value.push(res.data)
        }
        return res.data
      } else {
        errorMessage.value = res.message || '分析失败'
        return null
      }
    } catch (err: any) {
      errorMessage.value = err.message || '分析请求失败'
      return null
    } finally {
      loading.value = false
    }
  }

  /**
   * 提交批量持仓分析任务
   *
   * @param params 可选参数：股票代码列表和模型名称，codes 为空时分析所有持仓
   * @returns task_id，失败时返回 null
   */
  async function analyzeBatch(
    params?: { codes?: string[]; model_name?: string }
  ): Promise<string | null> {
    loading.value = true
    batchProgress.value = 0
    currentStep.value = '正在提交批量分析任务...'
    errorMessage.value = null

    try {
      const res = await portfolioApi.analyzeBatch(params)
      if (res.success && res.data) {
        currentTaskId.value = res.data.task_id
        // 开始轮询进度
        startPolling()
        return res.data.task_id
      } else {
        errorMessage.value = res.message || '批量分析提交失败'
        loading.value = false
        return null
      }
    } catch (err: any) {
      errorMessage.value = err.message || '批量分析请求失败'
      loading.value = false
      return null
    }
  }

  /**
   * 轮询批量分析任务进度
   *
   * @param taskId 任务 ID
   */
  async function pollProgress(taskId: string) {
    try {
      const res = await analysisApi.getTaskStatus(taskId)
      if (res.success && res.data) {
        const task = res.data
        batchProgress.value = task.progress ?? 0
        currentStep.value = task.step_detail || task.current_step || ''

        // 根据任务状态显示友好提示
        if (!currentStep.value) {
          if (task.status === 'pending' || task.status === 'queued') {
            currentStep.value = '⏳ 任务排队中，等待 Worker 处理...'
          } else if (task.status === 'running' || task.status === 'processing') {
            currentStep.value = '🔄 分析进行中...'
          } else {
            currentStep.value = '分析中...'
          }
        }

        if (task.status === 'completed') {
          stopPolling()
          batchProgress.value = 100
          currentStep.value = '✅ 批量分析完成'
          currentTaskId.value = null
          loading.value = false

          // 刷新最新分析结果
          await fetchLatestAnalyses()
        } else if (task.status === 'failed') {
          stopPolling()
          currentTaskId.value = null
          loading.value = false
          errorMessage.value = task.error_message || '批量分析任务失败'
          currentStep.value = '❌ 分析失败'
        }
      }
    } catch {
      // 轮询失败不中断，继续下次轮询
    }
  }

  /**
   * 开始轮询进度
   */
  function startPolling() {
    stopPolling()
    pollTimer = setInterval(async () => {
      if (currentTaskId.value) {
        await pollProgress(currentTaskId.value)
      }
    }, 3000)
  }

  /**
   * 停止轮询
   */
  function stopPolling() {
    if (pollTimer) {
      clearInterval(pollTimer)
      pollTimer = null
    }
  }

  /**
   * 重置所有状态
   */
  function resetState() {
    stopPolling()
    analyses.value = []
    loading.value = false
    currentTaskId.value = null
    batchProgress.value = 0
    currentStep.value = ''
    errorMessage.value = null
  }

  return {
    // 状态
    analyses,
    loading,
    currentTaskId,
    batchProgress,
    currentStep,
    errorMessage,

    // 计算属性
    isBatchAnalyzing,
    hasAnalyses,

    // Actions
    fetchLatestAnalyses,
    analyzeSingle,
    analyzeBatch,
    pollProgress,
    startPolling,
    stopPolling,
    resetState,
  }
})
