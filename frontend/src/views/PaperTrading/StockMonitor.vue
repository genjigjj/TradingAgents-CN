<template>
  <div class="stock-monitor">
    <!-- 顶部操作栏 -->
    <div class="action-bar">
      <div class="action-left">
        <el-button type="primary" @click="showCreateDialog = true">
          <el-icon><Plus /></el-icon>
          添加监测
        </el-button>
      </div>
      <div class="action-right">
        <el-button :icon="Refresh" text :loading="loading" @click="handleRefresh">刷新</el-button>
      </div>
    </div>

    <!-- 监测卡片网格 -->
    <div v-if="configs.length > 0" class="config-grid">
      <el-card
        v-for="config in configs"
        :key="config._id"
        shadow="hover"
        class="config-card"
      >
        <!-- 卡片头部：股票名称 + 评级 -->
        <div class="card-top">
          <div class="stock-info">
            <span class="stock-name">{{ config.name || config.symbol }}</span>
            <span class="stock-code-sub">{{ config.symbol }}</span>
          </div>
          <el-tag
            v-if="config.rating"
            :type="getRatingTagType(config.rating)"
            size="small"
            effect="dark"
          >
            {{ config.rating }}
          </el-tag>
        </div>

        <!-- 当前价格 -->
        <div class="price-row">
          <span class="label">当前价格</span>
          <span class="price-value">
            {{ config.current_price > 0 ? config.current_price.toFixed(2) : '--' }}
          </span>
        </div>

        <!-- 进场区间条形图 -->
        <div v-if="config.entry_range && config.entry_range.min > 0" class="entry-range-bar">
          <span class="label">进场区间</span>
          <div class="range-bar-container">
            <div class="range-bar">
              <div
                class="range-fill"
                :style="getRangeBarStyle(config)"
              />
              <div
                v-if="config.current_price > 0"
                class="price-marker"
                :style="getPriceMarkerStyle(config)"
              />
            </div>
            <div class="range-labels">
              <span>{{ config.entry_range.min.toFixed(2) }}</span>
              <span>{{ config.entry_range.max.toFixed(2) }}</span>
            </div>
          </div>
        </div>

        <!-- 止盈止损标记 -->
        <div class="tp-sl-row">
          <el-tag type="success" size="small" effect="plain">
            止盈 {{ config.take_profit > 0 ? config.take_profit.toFixed(2) : '--' }}
          </el-tag>
          <el-tag type="danger" size="small" effect="plain">
            止损 {{ config.stop_loss > 0 ? config.stop_loss.toFixed(2) : '--' }}
          </el-tag>
        </div>

        <!-- 启停开关 -->
        <div class="switch-row">
          <span class="label">启用监测</span>
          <el-switch
            :model-value="config.enabled"
            @change="(val: boolean) => handleToggleEnabled(config, val)"
          />
        </div>

        <!-- 卡片底部操作 -->
        <div class="card-actions">
          <el-button
            type="primary"
            size="small"
            link
            @click="openEditDialog(config)"
          >
            编辑
          </el-button>
          <el-button
            type="danger"
            size="small"
            link
            @click="handleDeleteConfig(config)"
          >
            删除
          </el-button>
        </div>
      </el-card>
    </div>

    <!-- 空状态 -->
    <el-empty
      v-if="configs.length === 0 && !loading"
      description="暂无监测配置，点击「添加监测」开始实时监测"
      :image-size="160"
    />

    <!-- 加载状态 -->
    <div v-if="loading" v-loading="true" class="loading-placeholder" />

    <!-- 通知历史区域 -->
    <div class="notification-section">
      <div class="section-header">
        <h3>通知历史</h3>
        <div class="filter-bar">
          <el-select
            v-model="notificationFilter"
            placeholder="按类型过滤"
            clearable
            size="small"
            style="width: 140px"
            @change="fetchNotifications"
          >
            <el-option value="entry" label="进场通知" />
            <el-option value="take_profit" label="止盈通知" />
            <el-option value="stop_loss" label="止损通知" />
          </el-select>
        </div>
      </div>

      <div v-if="notifications.length > 0" class="notification-list">
        <div
          v-for="notif in notifications"
          :key="notif._id"
          class="notification-item"
          :class="'notif-' + notif.type"
        >
          <div class="notif-header">
            <el-tag
              :type="getNotifTagType(notif.type)"
              size="small"
              effect="dark"
            >
              {{ getNotifTypeLabel(notif.type) }}
            </el-tag>
            <span class="notif-stock">{{ notif.name || notif.symbol }}</span>
            <span class="notif-time">{{ formatTime(notif.triggered_at) }}</span>
          </div>
          <div class="notif-message">{{ notif.message }}</div>
        </div>
      </div>

      <el-empty
        v-if="notifications.length === 0 && !loadingNotifications"
        description="暂无通知记录"
        :image-size="100"
      />

      <div v-if="loadingNotifications" v-loading="true" class="loading-placeholder-sm" />
    </div>

    <!-- 添加/编辑监测对话框 -->
    <el-dialog
      v-model="showCreateDialog"
      :title="editingConfig ? '编辑监测配置' : '添加监测'"
      width="520px"
      :close-on-click-modal="false"
      @close="resetForm"
    >
      <el-form :model="configForm" label-width="100px">
        <el-form-item label="股票代码" required>
          <el-input
            v-model="configForm.symbol"
            placeholder="请输入股票代码，如 600519"
            :disabled="!!editingConfig"
          />
        </el-form-item>
        <el-form-item label="股票名称">
          <el-input
            v-model="configForm.name"
            placeholder="可选，如 贵州茅台"
          />
        </el-form-item>
        <el-form-item label="进场区间">
          <div style="display: flex; gap: 8px; align-items: center; width: 100%">
            <el-input-number
              v-model="configForm.entry_min"
              :min="0"
              :precision="2"
              :step="1"
              placeholder="下限"
              style="flex: 1"
            />
            <span>—</span>
            <el-input-number
              v-model="configForm.entry_max"
              :min="0"
              :precision="2"
              :step="1"
              placeholder="上限"
              style="flex: 1"
            />
          </div>
        </el-form-item>
        <el-form-item label="止盈价">
          <el-input-number
            v-model="configForm.take_profit"
            :min="0"
            :precision="2"
            :step="1"
            style="width: 100%"
          />
        </el-form-item>
        <el-form-item label="止损价">
          <el-input-number
            v-model="configForm.stop_loss"
            :min="0"
            :precision="2"
            :step="1"
            style="width: 100%"
          />
        </el-form-item>
        <el-form-item label="检查间隔">
          <el-select v-model="configForm.check_interval" style="width: 100%">
            <el-option :value="30" label="30 秒" />
            <el-option :value="60" label="1 分钟（推荐）" />
            <el-option :value="120" label="2 分钟" />
            <el-option :value="300" label="5 分钟" />
            <el-option :value="600" label="10 分钟" />
          </el-select>
        </el-form-item>
        <el-form-item label="仅交易时段">
          <el-switch v-model="configForm.trading_hours_only" />
        </el-form-item>
        <el-form-item label="启用通知">
          <el-switch v-model="configForm.notification_enabled" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showCreateDialog = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="handleSaveConfig">
          {{ editingConfig ? '保存' : '创建' }}
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus, Refresh } from '@element-plus/icons-vue'
import {
  stockMonitorApi,
  type MonitorConfig,
  type MonitorNotification,
} from '@/api/stockMonitor'

// ============================================================
// 状态
// ============================================================

const loading = ref(false)
const saving = ref(false)
const loadingNotifications = ref(false)

/** 监测配置列表 */
const configs = ref<MonitorConfig[]>([])

/** 通知历史 */
const notifications = ref<MonitorNotification[]>([])
const notificationFilter = ref<string>('')

/** 创建/编辑对话框 */
const showCreateDialog = ref(false)
const editingConfig = ref<MonitorConfig | null>(null)
const configForm = ref({
  symbol: '',
  name: '',
  entry_min: 0,
  entry_max: 0,
  take_profit: 0,
  stop_loss: 0,
  check_interval: 60,
  trading_hours_only: true,
  notification_enabled: true,
})

// ============================================================
// 标签映射
// ============================================================

function getRatingTagType(rating: string): 'success' | 'warning' | 'danger' | 'info' {
  if (rating.includes('买入') || rating.includes('强烈')) return 'success'
  if (rating.includes('持有') || rating.includes('观望')) return 'warning'
  if (rating.includes('卖出') || rating.includes('减持')) return 'danger'
  return 'info'
}

function getNotifTagType(type: string): 'success' | 'warning' | 'danger' {
  if (type === 'entry') return 'success'
  if (type === 'take_profit') return 'warning'
  return 'danger'
}

function getNotifTypeLabel(type: string): string {
  if (type === 'entry') return '进场'
  if (type === 'take_profit') return '止盈'
  if (type === 'stop_loss') return '止损'
  return type
}

function formatTime(isoStr: string | null | undefined): string {
  if (!isoStr) return '-'
  try {
    const d = new Date(isoStr)
    return d.toLocaleString('zh-CN', {
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
    })
  } catch {
    return isoStr
  }
}

// ============================================================
// 进场区间条形图计算
// ============================================================

function getRangeBarStyle(config: MonitorConfig) {
  // 进场区间填充色
  return {
    width: '100%',
    background: '#67C23A33',
    borderRadius: '4px',
    height: '100%',
  }
}

function getPriceMarkerStyle(config: MonitorConfig) {
  const { entry_range, current_price, stop_loss, take_profit } = config
  if (!entry_range || entry_range.min <= 0 || entry_range.max <= 0) return { display: 'none' }

  // 计算价格在整个范围（止损 ~ 止盈）中的位置
  const rangeMin = stop_loss > 0 ? stop_loss : entry_range.min * 0.95
  const rangeMax = take_profit > 0 ? take_profit : entry_range.max * 1.05
  const totalRange = rangeMax - rangeMin
  if (totalRange <= 0) return { display: 'none' }

  const pct = Math.max(0, Math.min(100, ((current_price - rangeMin) / totalRange) * 100))

  // 根据价格位置决定颜色
  let color = '#909399' // 默认灰色
  if (current_price >= entry_range.min && current_price <= entry_range.max) {
    color = '#67C23A' // 进场区间内 - 绿色
  } else if (take_profit > 0 && current_price >= take_profit) {
    color = '#E6A23C' // 止盈 - 橙色
  } else if (stop_loss > 0 && current_price <= stop_loss) {
    color = '#F56C6C' // 止损 - 红色
  }

  return {
    left: `${pct}%`,
    background: color,
  }
}

// ============================================================
// 数据加载
// ============================================================

async function fetchConfigs() {
  loading.value = true
  try {
    const res = await stockMonitorApi.listConfigs()
    if (res.success) {
      configs.value = res.data?.items ?? []
    }
  } catch (err: any) {
    ElMessage.error(err?.message || '加载监测配置失败')
  } finally {
    loading.value = false
  }
}

async function fetchNotifications() {
  loadingNotifications.value = true
  try {
    const params: Record<string, any> = { limit: 50 }
    if (notificationFilter.value) {
      params.notification_type = notificationFilter.value
    }
    const res = await stockMonitorApi.getNotifications(params)
    if (res.success) {
      notifications.value = res.data?.items ?? []
    }
  } catch (err: any) {
    ElMessage.error(err?.message || '加载通知历史失败')
  } finally {
    loadingNotifications.value = false
  }
}

async function handleRefresh() {
  await Promise.all([fetchConfigs(), fetchNotifications()])
  ElMessage.success('已刷新')
}

// ============================================================
// 创建/编辑配置
// ============================================================

function resetForm() {
  editingConfig.value = null
  configForm.value = {
    symbol: '',
    name: '',
    entry_min: 0,
    entry_max: 0,
    take_profit: 0,
    stop_loss: 0,
    check_interval: 60,
    trading_hours_only: true,
    notification_enabled: true,
  }
}

function openEditDialog(config: MonitorConfig) {
  editingConfig.value = config
  configForm.value = {
    symbol: config.symbol,
    name: config.name,
    entry_min: config.entry_range?.min ?? 0,
    entry_max: config.entry_range?.max ?? 0,
    take_profit: config.take_profit,
    stop_loss: config.stop_loss,
    check_interval: config.check_interval,
    trading_hours_only: config.trading_hours_only,
    notification_enabled: config.notification_enabled,
  }
  showCreateDialog.value = true
}

async function handleSaveConfig() {
  if (!configForm.value.symbol.trim()) {
    ElMessage.warning('请输入股票代码')
    return
  }

  saving.value = true
  try {
    if (editingConfig.value) {
      // 编辑模式 — 更新
      const res = await stockMonitorApi.updateConfig(editingConfig.value._id, {
        name: configForm.value.name,
        entry_range: {
          min: configForm.value.entry_min,
          max: configForm.value.entry_max,
        },
        take_profit: configForm.value.take_profit,
        stop_loss: configForm.value.stop_loss,
        check_interval: configForm.value.check_interval,
        trading_hours_only: configForm.value.trading_hours_only,
        notification_enabled: configForm.value.notification_enabled,
      })
      if (res.success) {
        ElMessage.success('监测配置已更新')
        showCreateDialog.value = false
        resetForm()
        await fetchConfigs()
      } else {
        ElMessage.error(res.message || '更新失败')
      }
    } else {
      // 创建模式
      const res = await stockMonitorApi.createConfig({
        symbol: configForm.value.symbol.trim(),
        name: configForm.value.name.trim(),
        entry_range: {
          min: configForm.value.entry_min,
          max: configForm.value.entry_max,
        },
        take_profit: configForm.value.take_profit,
        stop_loss: configForm.value.stop_loss,
        check_interval: configForm.value.check_interval,
        trading_hours_only: configForm.value.trading_hours_only,
        notification_enabled: configForm.value.notification_enabled,
      })
      if (res.success) {
        ElMessage.success('监测配置创建成功')
        showCreateDialog.value = false
        resetForm()
        await fetchConfigs()
      } else {
        ElMessage.error(res.message || '创建失败')
      }
    }
  } catch (err: any) {
    ElMessage.error(err?.message || '保存监测配置失败')
  } finally {
    saving.value = false
  }
}

// ============================================================
// 启停开关
// ============================================================

async function handleToggleEnabled(config: MonitorConfig, enabled: boolean) {
  try {
    const res = await stockMonitorApi.updateConfig(config._id, { enabled })
    if (res.success) {
      ElMessage.success(enabled ? '已启用监测' : '已停用监测')
      await fetchConfigs()
    } else {
      ElMessage.error(res.message || '操作失败')
    }
  } catch (err: any) {
    ElMessage.error(err?.message || '操作失败')
  }
}

// ============================================================
// 删除配置
// ============================================================

async function handleDeleteConfig(config: MonitorConfig) {
  try {
    await ElMessageBox.confirm(
      `确定删除 ${config.name || config.symbol} 的监测配置？`,
      '确认删除',
      { type: 'warning' }
    )
  } catch {
    return // 用户取消
  }

  try {
    const res = await stockMonitorApi.deleteConfig(config._id)
    if (res.success) {
      ElMessage.success('监测配置已删除')
      await fetchConfigs()
    } else {
      ElMessage.error(res.message || '删除失败')
    }
  } catch (err: any) {
    ElMessage.error(err?.message || '删除失败')
  }
}

// ============================================================
// 生命周期
// ============================================================

onMounted(() => {
  fetchConfigs()
  fetchNotifications()
})
</script>

<style scoped>
.stock-monitor {
  padding: 0;
}

/* 顶部操作栏 */
.action-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 16px;
}

.action-left {
  display: flex;
  align-items: center;
}

/* 监测卡片网格 */
.config-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
  gap: 16px;
  margin-bottom: 24px;
}

.config-card {
  transition: transform 0.2s, box-shadow 0.2s;
}

.config-card:hover {
  transform: translateY(-2px);
}

.card-top {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 14px;
}

.stock-info {
  display: flex;
  flex-direction: column;
}

.stock-name {
  font-size: 17px;
  font-weight: 600;
  color: #303133;
}

.stock-code-sub {
  font-size: 12px;
  color: #909399;
  margin-top: 2px;
}

/* 价格行 */
.price-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 12px;
}

.price-value {
  font-size: 20px;
  font-weight: 700;
  color: #303133;
}

/* 进场区间条形图 */
.entry-range-bar {
  margin-bottom: 12px;
}

.entry-range-bar .label {
  font-size: 13px;
  color: #909399;
  margin-bottom: 6px;
  display: block;
}

.range-bar-container {
  width: 100%;
}

.range-bar {
  position: relative;
  height: 8px;
  background: #EBEEF5;
  border-radius: 4px;
  overflow: visible;
}

.range-fill {
  position: absolute;
  top: 0;
  left: 0;
}

.price-marker {
  position: absolute;
  top: -3px;
  width: 14px;
  height: 14px;
  border-radius: 50%;
  transform: translateX(-50%);
  border: 2px solid #fff;
  box-shadow: 0 1px 4px rgba(0, 0, 0, 0.2);
}

.range-labels {
  display: flex;
  justify-content: space-between;
  font-size: 11px;
  color: #909399;
  margin-top: 4px;
}

/* 止盈止损行 */
.tp-sl-row {
  display: flex;
  gap: 8px;
  margin-bottom: 12px;
}

/* 开关行 */
.switch-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 12px;
}

.switch-row .label,
.price-row .label {
  font-size: 13px;
  color: #909399;
}

/* 卡片底部操作 */
.card-actions {
  display: flex;
  justify-content: space-between;
  border-top: 1px solid #EBEEF5;
  padding-top: 10px;
}

/* 加载占位 */
.loading-placeholder {
  min-height: 200px;
}

.loading-placeholder-sm {
  min-height: 100px;
}

/* 通知历史区域 */
.notification-section {
  margin-top: 24px;
}

.section-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 12px;
}

.section-header h3 {
  font-size: 16px;
  font-weight: 600;
  color: #303133;
  margin: 0;
}

.filter-bar {
  display: flex;
  gap: 8px;
}

/* 通知列表 */
.notification-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.notification-item {
  padding: 12px 14px;
  border-radius: 8px;
  border-left: 4px solid #DCDFE6;
  background: #FAFAFA;
}

.notification-item.notif-entry {
  border-left-color: #67C23A;
  background: #F0F9EB;
}

.notification-item.notif-take_profit {
  border-left-color: #E6A23C;
  background: #FDF6EC;
}

.notification-item.notif-stop_loss {
  border-left-color: #F56C6C;
  background: #FEF0F0;
}

.notif-header {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 6px;
}

.notif-stock {
  font-size: 14px;
  font-weight: 500;
  color: #303133;
}

.notif-time {
  font-size: 12px;
  color: #909399;
  margin-left: auto;
}

.notif-message {
  font-size: 13px;
  color: #606266;
  line-height: 1.5;
}
</style>
