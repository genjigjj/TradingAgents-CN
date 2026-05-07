/**
 * 实时监测 API 模块
 *
 * 提供实时监测相关的前端 API 调用方法：
 * - 创建/同步监测配置
 * - 列出监测配置
 * - 更新监测配置
 * - 删除监测配置
 * - 查询通知历史
 */

import { ApiClient, type ApiResponse } from './request'

// ---------------------------------------------------------------------------
// 类型定义
// ---------------------------------------------------------------------------

/** 进场区间 */
export interface EntryRange {
  min: number
  max: number
}

/** 监测配置 */
export interface MonitorConfig {
  _id: string
  user_id: string
  symbol: string
  name: string
  market: string
  rating: string
  entry_range: EntryRange
  take_profit: number
  stop_loss: number
  current_price: number
  last_checked: string | null
  check_interval: number
  notification_enabled: boolean
  trading_hours_only: boolean
  enabled: boolean
  source: 'manual' | 'analysis_sync'
  created_at: string
  updated_at: string
}

/** 通知记录 */
export interface MonitorNotification {
  _id: string
  user_id: string
  config_id: string
  symbol: string
  name: string
  type: 'entry' | 'take_profit' | 'stop_loss'
  message: string
  current_price: number
  trigger_value: number
  triggered_at: string
  sent: boolean
  read: boolean
  source: string
  created_at: string
}

/** 创建/同步监测配置请求 */
export interface MonitorConfigPayload {
  symbol: string
  name?: string
  market?: string
  rating?: string
  entry_range?: EntryRange
  take_profit?: number
  stop_loss?: number
  current_price?: number
  check_interval?: number
  notification_enabled?: boolean
  trading_hours_only?: boolean
  enabled?: boolean
  source?: string
}

/** 通知历史查询参数 */
export interface NotificationQueryParams {
  symbol?: string
  notification_type?: string
  limit?: number
}

/** 配置列表响应 */
export interface ConfigListResponse {
  items: MonitorConfig[]
  total: number
}

/** 通知列表响应 */
export interface NotificationListResponse {
  items: MonitorNotification[]
  total: number
}

// ---------------------------------------------------------------------------
// API 方法
// ---------------------------------------------------------------------------

export const stockMonitorApi = {
  /**
   * 创建或同步监测配置
   *
   * @param data 监测配置参数
   */
  async createConfig(data: MonitorConfigPayload): Promise<ApiResponse<MonitorConfig>> {
    return ApiClient.post<MonitorConfig>('/api/monitor/stocks', data)
  },

  /**
   * 列出当前用户的所有监测配置
   */
  async listConfigs(): Promise<ApiResponse<ConfigListResponse>> {
    return ApiClient.get<ConfigListResponse>('/api/monitor/stocks')
  },

  /**
   * 更新监测配置
   *
   * @param id 配置 ID
   * @param data 更新参数
   */
  async updateConfig(
    id: string,
    data: Partial<MonitorConfigPayload>
  ): Promise<ApiResponse<MonitorConfig>> {
    return ApiClient.put<MonitorConfig>(`/api/monitor/stocks/${id}`, data)
  },

  /**
   * 删除监测配置
   *
   * @param id 配置 ID
   */
  async deleteConfig(id: string): Promise<ApiResponse<null>> {
    return ApiClient.delete<null>(`/api/monitor/stocks/${id}`)
  },

  /**
   * 查询通知历史
   *
   * @param params 查询参数（股票代码、通知类型、数量限制）
   */
  async getNotifications(
    params?: NotificationQueryParams
  ): Promise<ApiResponse<NotificationListResponse>> {
    return ApiClient.get<NotificationListResponse>('/api/monitor/notifications', params)
  },
}
