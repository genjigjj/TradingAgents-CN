/**
 * 持仓分析 API 模块
 *
 * 提供持仓分析相关的前端 API 调用方法：
 * - 单只股票分析
 * - 批量分析
 * - 分析历史查询
 * - 最新分析汇总
 * - 同步分析结果到实时监测
 */

import { ApiClient, type ApiResponse } from './request'

// ---------------------------------------------------------------------------
// 类型定义
// ---------------------------------------------------------------------------

/** 分析结果中的进场区间 */
export interface EntryRange {
  min: number
  max: number
}

/** 行情数据快照 */
export interface MarketDataSnapshot {
  price: number
  change_pct: number
  volume: number
}

/** 技术指标快照 */
export interface IndicatorsSnapshot {
  ma5?: number
  ma20?: number
  ma60?: number
  rsi?: number
  macd?: {
    dif: number
    dea: number
    macd: number
  }
  kdj?: {
    k: number
    d: number
    j: number
  }
  boll?: {
    upper: number
    middle: number
    lower: number
  }
}

/** 单只股票分析结果 */
export interface AnalysisResult {
  _id?: string
  user_id: string
  position_code: string
  market: string
  analysis_time: string
  rating: string
  confidence: number
  current_price: number
  target_price: number
  entry_range: EntryRange
  entry_min: number
  entry_max: number
  take_profit: number
  stop_loss: number
  summary: string
  model_name?: string
  raw_response?: string
  full_report?: string
  market_data_snapshot?: MarketDataSnapshot
  indicators_snapshot?: IndicatorsSnapshot
  created_at?: string
}

/** 批量分析响应 */
export interface BatchAnalyzeResponse {
  task_id: string
}

/** 分析历史列表响应 */
export interface AnalysisHistoryResponse {
  items: AnalysisResult[]
  total: number
}

/** 最新分析汇总响应 */
export interface LatestAnalysesResponse {
  items: AnalysisResult[]
  total: number
}

/** 同步到监测的响应 */
export interface SyncToMonitorResponse {
  action: 'created' | 'updated'
  config_id: string
}

// ---------------------------------------------------------------------------
// API 方法
// ---------------------------------------------------------------------------

export const portfolioApi = {
  /**
   * 对单只持仓股票执行 AI 分析
   *
   * @param code 股票代码（如 600519）
   * @param params 可选参数：市场类型和模型名称
   */
  async analyzeSingle(
    code: string,
    params?: { market?: string; model_name?: string }
  ): Promise<ApiResponse<AnalysisResult>> {
    return ApiClient.post<AnalysisResult>(`/api/portfolio/analyze/${code}`, params ?? {})
  },

  /**
   * 提交批量持仓分析任务
   *
   * @param params 可选参数：股票代码列表和模型名称，codes 为空时分析所有持仓
   */
  async analyzeBatch(
    params?: { codes?: string[]; model_name?: string }
  ): Promise<ApiResponse<BatchAnalyzeResponse>> {
    return ApiClient.post<BatchAnalyzeResponse>('/api/portfolio/analyze-batch', params ?? {})
  },

  /**
   * 查询单只股票的分析历史
   *
   * @param code 股票代码
   * @param limit 返回记录数上限，默认 20，最大 200
   */
  async getAnalysisHistory(
    code: string,
    limit?: number
  ): Promise<ApiResponse<AnalysisHistoryResponse>> {
    return ApiClient.get<AnalysisHistoryResponse>(
      `/api/portfolio/analysis-history/${code}`,
      limit !== undefined ? { limit } : undefined
    )
  },

  /**
   * 查询所有持仓的最新分析结果汇总
   */
  async getLatestAnalyses(): Promise<ApiResponse<LatestAnalysesResponse>> {
    return ApiClient.get<LatestAnalysesResponse>('/api/portfolio/analysis-latest')
  },

  /**
   * 将分析结果同步到实时监测配置
   *
   * @param code 股票代码
   */
  async syncToMonitor(code: string): Promise<ApiResponse<SyncToMonitorResponse>> {
    return ApiClient.post<SyncToMonitorResponse>(`/api/portfolio/sync-to-monitor/${code}`)
  },

  /**
   * 快速实时分析（不依赖持仓，直接输入代码即时分析）
   *
   * @param codes 股票代码列表（1-10只）
   * @param params 可选参数：市场类型和模型名称
   */
  async quickAnalyze(
    codes: string[],
    params?: { market?: string; model_name?: string }
  ): Promise<ApiResponse<{ results: Array<{ code: string; success: boolean; data?: AnalysisResult; error?: string }>; total: number; success: number; failed: number }>> {
    return ApiClient.post('/api/portfolio/quick-analyze', {
      codes,
      ...params,
    })
  },
}
