/**
 * AI 盯盘 API 模块
 *
 * 提供 AI 盯盘相关的前端 API 调用方法：
 * - 创建盯盘任务
 * - 列出盯盘任务
 * - 更新盯盘任务
 * - 删除盯盘任务
 * - 查询 AI 决策历史
 */

import { ApiClient, type ApiResponse } from './request'

// ---------------------------------------------------------------------------
// 类型定义
// ---------------------------------------------------------------------------

/** 持仓信息 */
export interface PositionInfo {
  quantity: number
  avg_cost: number
  currency: string
}

/** 最新决策摘要 */
export interface LastDecision {
  action: 'BUY' | 'SELL' | 'HOLD'
  confidence: number
  time: string
}

/** 关键价位 */
export interface KeyPriceLevels {
  support: number
  resistance: number
  stop_loss: number
  take_profit: number
}

/** 盯盘任务 */
export interface SmartMonitorTask {
  _id: string
  user_id: string
  stock_code: string
  stock_name: string
  market: string
  enabled: boolean
  status: 'running' | 'stopped' | 'position_cleared'
  check_interval: number
  auto_notify: boolean
  trading_hours_only: boolean
  position_info: PositionInfo
  last_check_time: string | null
  last_decision: LastDecision | null
  created_at: string
  updated_at: string
}

/** AI 决策记录 */
export interface SmartMonitorDecision {
  _id: string
  user_id: string
  stock_code: string
  stock_name: string
  decision_time: string
  trading_session: string
  action: 'BUY' | 'SELL' | 'HOLD'
  confidence: number
  reasoning: string
  risk_level: 'low' | 'medium' | 'high'
  key_price_levels: KeyPriceLevels
  position_size_pct: number
  stop_loss_pct: number
  take_profit_pct: number
  model_name: string
  created_at: string
}

/** 创建盯盘任务请求 */
export interface CreateTaskPayload {
  stock_code: string
  stock_name?: string
  check_interval?: number
  auto_notify?: boolean
  trading_hours_only?: boolean
}

/** 更新盯盘任务请求 */
export interface UpdateTaskPayload {
  enabled?: boolean
  check_interval?: number
  auto_notify?: boolean
  trading_hours_only?: boolean
  stock_name?: string
}

/** 决策历史查询参数 */
export interface DecisionQueryParams {
  stock_code?: string
  start_time?: string
  end_time?: string
  limit?: number
}

/** 任务列表响应 */
export interface TaskListResponse {
  items: SmartMonitorTask[]
  total: number
}

/** 决策列表响应 */
export interface DecisionListResponse {
  items: SmartMonitorDecision[]
  total: number
}

// ---------------------------------------------------------------------------
// API 方法
// ---------------------------------------------------------------------------

export const smartMonitorApi = {
  /**
   * 创建盯盘任务
   *
   * @param data 创建任务参数
   */
  async createTask(data: CreateTaskPayload): Promise<ApiResponse<SmartMonitorTask>> {
    return ApiClient.post<SmartMonitorTask>('/api/monitor/smart/tasks', data)
  },

  /**
   * 列出当前用户的所有盯盘任务
   */
  async listTasks(): Promise<ApiResponse<TaskListResponse>> {
    return ApiClient.get<TaskListResponse>('/api/monitor/smart/tasks')
  },

  /**
   * 更新盯盘任务配置
   *
   * @param taskId 任务 ID
   * @param data 更新参数
   */
  async updateTask(
    taskId: string,
    data: UpdateTaskPayload
  ): Promise<ApiResponse<SmartMonitorTask>> {
    return ApiClient.put<SmartMonitorTask>(`/api/monitor/smart/tasks/${taskId}`, data)
  },

  /**
   * 删除盯盘任务
   *
   * @param taskId 任务 ID
   */
  async deleteTask(taskId: string): Promise<ApiResponse<null>> {
    return ApiClient.delete<null>(`/api/monitor/smart/tasks/${taskId}`)
  },

  /**
   * 查询 AI 决策历史
   *
   * @param params 查询参数（股票代码、时间范围、数量限制）
   */
  async getDecisions(params?: DecisionQueryParams): Promise<ApiResponse<DecisionListResponse>> {
    return ApiClient.get<DecisionListResponse>('/api/monitor/smart/decisions', params)
  },
}
