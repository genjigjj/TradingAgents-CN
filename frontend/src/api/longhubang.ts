/**
 * 龙虎榜 API 模块
 *
 * 封装龙虎榜分析相关的所有 API 调用，包括分析触发、报告管理、统计查询、
 * 历史数据查询、游资排名和热门股票排名。
 * 使用 ApiClient 统一请求封装。
 */

import { ApiClient } from './request'

// ============================================================
// 请求参数类型
// ============================================================

/** 龙虎榜分析请求 */
export interface LonghubangAnalyzeRequest {
  /** 分析模式: date(指定日期) / days(最近N天) */
  mode: string
  /** 指定日期 YYYY-MM-DD（mode=date 时使用） */
  date?: string | null
  /** 最近天数（mode=days 时使用，1-10） */
  days: number
  /** 用户指定的 LLM 模型名称（留空则自动选择） */
  model_name?: string | null
}

// ============================================================
// 响应数据类型
// ============================================================

/** 评分结果 */
export interface ScoringResult {
  /** 排名 */
  rank: number
  /** 股票名称 */
  stock_name: string
  /** 股票代码 */
  stock_code: string
  /** 综合评分(0-100) */
  total_score: number
  /** 买入资金含金量(0-30) */
  capital_quality: number
  /** 净买入额评分(0-25) */
  net_inflow_score: number
  /** 卖出压力评分(0-20) */
  sell_pressure: number
  /** 机构共振评分(0-15) */
  institution_score: number
  /** 其他加分项(0-10) */
  bonus: number
  /** 顶级游资数量 */
  top_youzi_count: number
  /** 买方席位数 */
  buy_seats: number
  /** 是否有机构参与 */
  has_institution: boolean
  /** 净流入金额(元) */
  net_inflow: number
}

/** 数据概况信息 */
export interface LonghubangDataInfo {
  /** 总记录数 */
  total_records: number
  /** 涉及股票数 */
  total_stocks: number
  /** 涉及游资数 */
  total_youzi: number
  /** 总买入金额 */
  total_buy_amount?: number
  /** 总卖出金额 */
  total_sell_amount?: number
  /** 总净流入金额 */
  total_net_inflow?: number
  /** 活跃游资 TOP */
  top_youzi?: any[]
  /** 资金净流入 TOP 股票 */
  top_stocks?: any[]
  /** 热门概念 TOP */
  top_concepts?: any[]
}

/** 龙虎榜分析响应 */
export interface LonghubangAnalyzeResult {
  /** 分析任务 ID */
  task_id: string | null
  /** 数据概况信息 */
  data_info: LonghubangDataInfo
  /** 评分排名列表 */
  scoring_ranking: ScoringResult[]
  /** 数据日期范围 */
  date_range?: string
  /** 保存的记录数 */
  saved_count?: number
  /** 消息 */
  message?: string
}

/** 推荐股票 */
export interface LonghubangRecommendedStock {
  rank: number
  code: string
  name: string
  net_inflow: number
  reason: string
  confidence: string
  hold_period: string
}

/** 分析师报告 */
export interface AgentAnalysis {
  agent_name: string
  analysis: string
}

/** 龙虎榜分析报告 */
export interface LonghubangReport {
  _id: string
  analysis_date: string
  data_date_range: string
  data_info: LonghubangDataInfo
  scoring_ranking: ScoringResult[]
  agents_analysis: Record<string, AgentAnalysis>
  recommended_stocks: LonghubangRecommendedStock[]
  summary: string
  task_id?: string | null
  created_at: string
}

/** 报告列表分页响应 */
export interface ReportsResponse {
  total: number
  page: number
  page_size: number
  items: LonghubangReport[]
}

/** 龙虎榜记录 */
export interface LonghubangRecord {
  date: string
  stock_code: string
  stock_name: string
  youzi_name: string
  yingye_bu: string
  buy_amount: number
  sell_amount: number
  net_inflow: number
  concepts?: string | null
}

/** 数据库统计信息 */
export interface LonghubangStatistics {
  /** 总记录数 */
  total_records: number
  /** 涉及股票数 */
  total_stocks: number
  /** 涉及游资数 */
  total_youzi: number
  /** 分析报告数 */
  total_reports: number
  /** 数据日期范围 */
  date_range: {
    start: string | null
    end: string | null
  }
}

/** 活跃游资排名项 */
export interface TopYouziItem {
  youzi_name: string
  trade_count: number
  total_buy: number
  total_sell: number
  total_net_inflow: number
}

/** 热门股票排名项 */
export interface TopStockItem {
  stock_code: string
  stock_name: string
  youzi_count: number
  total_buy: number
  total_sell: number
  total_net_inflow: number
  concepts?: string | null
}

// ============================================================
// API 方法
// ============================================================

export const longhubangApi = {
  /**
   * 触发龙虎榜分析
   * 同步返回数据概况和评分排名，AI 分析师报告通过任务 ID 异步获取
   * @param request 分析请求参数
   */
  analyze: (request: LonghubangAnalyzeRequest) =>
    ApiClient.post<LonghubangAnalyzeResult>('/api/longhubang/analyze', request),

  /**
   * 查询历史报告列表
   * @param page 页码
   * @param pageSize 每页数量
   */
  getReports: (page = 1, pageSize = 20) =>
    ApiClient.get<ReportsResponse>('/api/longhubang/reports', {
      page,
      page_size: pageSize,
    }),

  /**
   * 查询报告详情
   * @param reportId 报告 ID
   */
  getReportDetail: (reportId: string) =>
    ApiClient.get<LonghubangReport>(`/api/longhubang/reports/${reportId}`),

  /**
   * 删除指定的历史报告
   * @param reportId 报告 ID
   */
  deleteReport: (reportId: string) =>
    ApiClient.delete(`/api/longhubang/reports/${reportId}`),

  /**
   * 获取数据库统计信息
   */
  getStatistics: () =>
    ApiClient.get<LonghubangStatistics>('/api/longhubang/statistics'),

  /**
   * 查询龙虎榜历史数据
   * @param startDate 开始日期 YYYY-MM-DD
   * @param endDate 结束日期 YYYY-MM-DD
   * @param stockCode 股票代码（可选）
   */
  getRecords: (startDate: string, endDate: string, stockCode?: string) =>
    ApiClient.get<LonghubangRecord[]>('/api/longhubang/records', {
      start_date: startDate,
      end_date: endDate,
      ...(stockCode ? { stock_code: stockCode } : {}),
    }),

  /**
   * 查询活跃游资排名
   * @param startDate 开始日期 YYYY-MM-DD
   * @param endDate 结束日期 YYYY-MM-DD
   * @param limit 返回数量（默认 20）
   */
  getTopYouzi: (startDate: string, endDate: string, limit = 20) =>
    ApiClient.get<TopYouziItem[]>('/api/longhubang/top-youzi', {
      start_date: startDate,
      end_date: endDate,
      limit,
    }),

  /**
   * 查询热门股票排名
   * @param startDate 开始日期 YYYY-MM-DD
   * @param endDate 结束日期 YYYY-MM-DD
   * @param limit 返回数量（默认 20）
   */
  getTopStocks: (startDate: string, endDate: string, limit = 20) =>
    ApiClient.get<TopStockItem[]>('/api/longhubang/top-stocks', {
      start_date: startDate,
      end_date: endDate,
      limit,
    }),
}
