/**
 * 主力选股 API 模块
 *
 * 封装主力选股相关的所有 API 调用，包括筛选、分析、批量历史查询和删除。
 * 使用 ApiClient 统一请求封装。
 */

import { ApiClient } from './request'

// ============================================================
// 请求参数类型
// ============================================================

/** 主力选股筛选参数 */
export interface MainForceScreenParams {
  /** 时间区间: 3m/6m/1y/custom */
  time_range: string
  /** 自定义开始日期 YYYY-MM-DD */
  start_date?: string | null
  /** 自定义结束日期 YYYY-MM-DD */
  end_date?: string | null
  /** 精选推荐数量 (3-10) */
  top_n: number
  /** 最大涨跌幅限制(%) */
  max_change_pct: number
  /** 最小市值(亿) */
  min_market_cap: number
  /** 最大市值(亿) */
  max_market_cap: number
}

/** 主力选股分析请求 */
export interface MainForceAnalyzeRequest {
  /** 筛选参数 */
  params: MainForceScreenParams
  /** 分析模式: overview/batch */
  analysis_mode: string
  /** 批量分析数量(10/20/30/50) */
  batch_count?: number | null
  /** 研究深度 */
  research_depth: string
  /** 选择的分析师 */
  selected_analysts: string[]
  /** 指定 LLM 模型名称（为空时自动选择） */
  model_name?: string | null
}

// ============================================================
// 响应数据类型
// ============================================================

/** 候选股票 */
export interface MainForceCandidate {
  /** 股票代码 */
  code: string
  /** 股票名称 */
  name: string
  /** 所属行业 */
  industry: string
  /** 主力资金净流入(元) */
  net_inflow: number
  /** 区间涨跌幅(%) */
  change_pct: number
  /** 总市值(亿) */
  market_cap: number
  /** 市盈率 */
  pe_ratio?: number | null
  /** 市净率 */
  pb_ratio?: number | null
}

/** 筛选结果 */
export interface MainForceScreenResult {
  /** 获取的原始股票数量 */
  total_fetched: number
  /** 筛选后的股票数量 */
  total_filtered: number
  /** 候选股票列表 */
  candidates: MainForceCandidate[]
}

/** 分析响应 */
export interface MainForceAnalyzeResult {
  /** 整体分析任务 ID */
  task_id: string | null
  /** 批量分析 batch ID */
  batch_id?: string | null
  /** 候选股票数量 */
  candidates_count: number
  /** 获取的原始股票数量 */
  total_fetched: number
  /** 筛选后的股票数量 */
  total_filtered: number
}

/** 推荐股票 */
export interface RecommendedStock {
  code: string
  name: string
  reason: string
  highlights: string[]
  risks: string[]
  position: string
  period: string
}

/** 批量分析单项结果 */
export interface BatchResultItem {
  code: string
  name: string
  status: string
  rating?: string | null
  confidence?: number | null
  entry_range?: string | null
  take_profit?: string | null
  stop_loss?: string | null
  target_price?: string | null
  advice?: string | null
  error_message?: string | null
}

/** 批量分析历史记录 */
export interface MainForceBatchHistory {
  _id: string
  analysis_date: string
  params: Record<string, any>
  candidates_count: number
  filtered_count: number
  recommended_count: number
  overview_analysis: Record<string, string>
  recommended_stocks: RecommendedStock[]
  batch_results: {
    total: number
    success: number
    failed: number
    duration_seconds: number
    results: BatchResultItem[]
  }
  task_id?: string | null
  created_at: string
}

/** 批量历史分页响应 */
export interface BatchHistoryResponse {
  total: number
  page: number
  page_size: number
  items: MainForceBatchHistory[]
}

// ============================================================
// API 方法
// ============================================================

export const mainForceApi = {
  /**
   * 筛选主力资金股票
   * @param params 筛选参数
   */
  screen: (params: MainForceScreenParams) =>
    ApiClient.post<MainForceScreenResult>('/api/main-force/screen', params),

  /**
   * 触发完整分析流程（筛选 + AI 分析）
   * @param request 分析请求参数
   */
  analyze: (request: MainForceAnalyzeRequest) =>
    ApiClient.post<MainForceAnalyzeResult>('/api/main-force/analyze', request),

  /**
   * 查询批量分析历史
   * @param page 页码
   * @param pageSize 每页数量
   */
  getBatchHistory: (page = 1, pageSize = 20) =>
    ApiClient.get<BatchHistoryResponse>('/api/main-force/batch-history', {
      page,
      page_size: pageSize,
    }),

  /**
   * 删除指定的批量分析历史记录
   * @param historyId 历史记录 ID
   */
  deleteBatchHistory: (historyId: string) =>
    ApiClient.delete(`/api/main-force/batch-history/${historyId}`),
}
