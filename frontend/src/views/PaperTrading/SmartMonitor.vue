<template>
  <div class="smart-monitor">
    <!-- 顶部操作栏 -->
    <div class="action-bar">
      <div class="action-left">
        <el-button type="primary" @click="showCreateDialog = true">
          <el-icon><Plus /></el-icon>
          添加盯盘任务
        </el-button>
      </div>
      <div class="action-right">
        <el-button :icon="Refresh" text :loading="loading" @click="handleRefresh">刷新</el-button>
      </div>
    </div>

    <!-- 盯盘任务卡片网格 -->
    <div v-if="tasks.length > 0" class="task-grid">
      <el-card
        v-for="task in tasks"
        :key="task._id"
        shadow="hover"
        class="task-card"
      >
        <!-- 卡片头部：股票名称 + 状态 -->
        <div class="card-top">
          <div class="stock-info">
            <span class="stock-name">{{ task.stock_name || task.stock_code }}</span>
            <span class="stock-code-sub">{{ task.stock_code }}</span>
          </div>
          <el-tag
            :type="getStatusTagType(task.status)"
            size="small"
            effect="dark"
          >
            {{ getStatusLabel(task.status) }}
          </el-tag>
        </div>

        <!-- 启停开关 -->
        <div class="switch-row">
          <span class="label">自动盯盘</span>
          <el-switch
            :model-value="task.enabled"
            :disabled="task.status === 'position_cleared'"
            @change="(val: boolean) => handleToggleEnabled(task, val)"
          />
        </div>

        <!-- 检查间隔 -->
        <div class="interval-row">
          <span class="label">检查间隔</span>
          <el-select
            :model-value="task.check_interval"
            size="small"
            style="width: 120px"
            @change="(val: number) => handleChangeInterval(task, val)"
          >
            <el-option :value="60" label="1 分钟" />
            <el-option :value="180" label="3 分钟" />
            <el-option :value="300" label="5 分钟" />
            <el-option :value="600" label="10 分钟" />
            <el-option :value="900" label="15 分钟" />
            <el-option :value="1800" label="30 分钟" />
          </el-select>
        </div>

        <!-- 最新决策 -->
        <div v-if="task.last_decision" class="last-decision">
          <span class="label">最新决策</span>
          <div class="decision-brief">
            <el-tag
              :type="getActionTagType(task.last_decision.action)"
              size="small"
              effect="plain"
            >
              {{ getActionLabel(task.last_decision.action) }}
            </el-tag>
            <span class="confidence-text">
              信心度 {{ task.last_decision.confidence }}%
            </span>
          </div>
        </div>
        <div v-else class="last-decision">
          <span class="label">最新决策</span>
          <span class="no-decision">暂无决策</span>
        </div>

        <!-- 卡片底部操作 -->
        <div class="card-actions">
          <el-button
            type="primary"
            size="small"
            link
            @click="openDecisionHistory(task)"
          >
            决策历史
          </el-button>
          <el-button
            type="danger"
            size="small"
            link
            @click="handleDeleteTask(task)"
          >
            删除
          </el-button>
        </div>
      </el-card>
    </div>

    <!-- 空状态 -->
    <el-empty
      v-if="tasks.length === 0 && !loading"
      description="暂无盯盘任务，点击「添加盯盘任务」开始 AI 盯盘"
      :image-size="160"
    />

    <!-- 加载状态 -->
    <div v-if="loading" v-loading="true" class="loading-placeholder" />

    <!-- 创建盯盘任务对话框 -->
    <el-dialog
      v-model="showCreateDialog"
      title="添加盯盘任务"
      width="480px"
      :close-on-click-modal="false"
    >
      <el-form :model="createForm" label-width="100px">
        <el-form-item label="股票代码" required>
          <el-input
            v-model="createForm.stock_code"
            placeholder="请输入股票代码，如 600519"
          />
        </el-form-item>
        <el-form-item label="股票名称">
          <el-input
            v-model="createForm.stock_name"
            placeholder="可选，如 贵州茅台"
          />
        </el-form-item>
        <el-form-item label="检查间隔">
          <el-select v-model="createForm.check_interval" style="width: 100%">
            <el-option :value="60" label="1 分钟" />
            <el-option :value="180" label="3 分钟" />
            <el-option :value="300" label="5 分钟（推荐）" />
            <el-option :value="600" label="10 分钟" />
            <el-option :value="900" label="15 分钟" />
          </el-select>
        </el-form-item>
        <el-form-item label="仅交易时段">
          <el-switch v-model="createForm.trading_hours_only" />
        </el-form-item>
        <el-form-item label="自动通知">
          <el-switch v-model="createForm.auto_notify" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showCreateDialog = false">取消</el-button>
        <el-button type="primary" :loading="creating" @click="handleCreateTask">
          创建
        </el-button>
      </template>
    </el-dialog>

    <!-- 决策历史抽屉 -->
    <el-drawer
      v-model="showDecisionDrawer"
      :title="decisionDrawerTitle"
      size="560px"
      direction="rtl"
    >
      <!-- 一键下单提示 -->
      <div v-if="decisions.length > 0" class="decision-timeline">
        <div
          v-for="dec in decisions"
          :key="dec._id"
          class="decision-item"
          :class="'decision-' + dec.action.toLowerCase()"
        >
          <div class="decision-header">
            <el-tag
              :type="getActionTagType(dec.action)"
              size="small"
              effect="dark"
            >
              {{ getActionLabel(dec.action) }}
            </el-tag>
            <span class="decision-time">{{ formatTime(dec.decision_time) }}</span>
            <el-tag size="small" effect="plain" :type="getRiskTagType(dec.risk_level)">
              {{ getRiskLabel(dec.risk_level) }}
            </el-tag>
          </div>

          <div class="decision-confidence">
            <span>信心度</span>
            <el-progress
              :percentage="dec.confidence"
              :stroke-width="8"
              :color="getConfidenceColor(dec.confidence)"
              style="flex: 1; margin-left: 8px"
            />
          </div>

          <div class="decision-reasoning">{{ dec.reasoning }}</div>

          <!-- 关键价位 -->
          <div v-if="dec.key_price_levels" class="decision-prices">
            <el-tag type="success" size="small" effect="plain">
              支撑 {{ dec.key_price_levels.support?.toFixed(2) }}
            </el-tag>
            <el-tag type="warning" size="small" effect="plain">
              阻力 {{ dec.key_price_levels.resistance?.toFixed(2) }}
            </el-tag>
            <el-tag type="danger" size="small" effect="plain">
              止损 {{ dec.key_price_levels.stop_loss?.toFixed(2) }}
            </el-tag>
            <el-tag type="primary" size="small" effect="plain">
              止盈 {{ dec.key_price_levels.take_profit?.toFixed(2) }}
            </el-tag>
          </div>

          <!-- 一键下单按钮（仅 BUY/SELL） -->
          <div v-if="dec.action === 'BUY' || dec.action === 'SELL'" class="decision-order">
            <el-button
              :type="dec.action === 'BUY' ? 'success' : 'danger'"
              size="small"
              :loading="orderingDecisionId === dec._id"
              @click="handlePlaceOrder(dec)"
            >
              一键{{ dec.action === 'BUY' ? '买入' : '卖出' }}
            </el-button>
          </div>
        </div>
      </div>

      <el-empty
        v-if="decisions.length === 0 && !loadingDecisions"
        description="暂无决策记录"
        :image-size="120"
      />

      <div v-if="loadingDecisions" v-loading="true" class="loading-placeholder" />
    </el-drawer>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus, Refresh } from '@element-plus/icons-vue'
import {
  smartMonitorApi,
  type SmartMonitorTask,
  type SmartMonitorDecision,
} from '@/api/smartMonitor'
import { paperApi } from '@/api/paper'

// ============================================================
// 状态
// ============================================================

const loading = ref(false)
const creating = ref(false)
const loadingDecisions = ref(false)

/** 盯盘任务列表 */
const tasks = ref<SmartMonitorTask[]>([])

/** 创建对话框 */
const showCreateDialog = ref(false)
const createForm = ref({
  stock_code: '',
  stock_name: '',
  check_interval: 300,
  trading_hours_only: true,
  auto_notify: true,
})

/** 决策历史抽屉 */
const showDecisionDrawer = ref(false)
const decisionDrawerTitle = ref('决策历史')
const decisions = ref<SmartMonitorDecision[]>([])
const currentTaskForDecision = ref<SmartMonitorTask | null>(null)

/** 一键下单状态 */
const orderingDecisionId = ref<string | null>(null)

// ============================================================
// 状态/动作标签映射
// ============================================================

function getStatusTagType(status: string): 'success' | 'info' | 'warning' | 'danger' {
  if (status === 'running') return 'success'
  if (status === 'stopped') return 'info'
  if (status === 'position_cleared') return 'warning'
  return 'info'
}

function getStatusLabel(status: string): string {
  if (status === 'running') return '运行中'
  if (status === 'stopped') return '已停止'
  if (status === 'position_cleared') return '持仓已清空'
  return status
}

function getActionTagType(action: string): 'success' | 'danger' | 'info' {
  if (action === 'BUY') return 'success'
  if (action === 'SELL') return 'danger'
  return 'info'
}

function getActionLabel(action: string): string {
  if (action === 'BUY') return '买入'
  if (action === 'SELL') return '卖出'
  return '持有'
}

function getRiskTagType(level: string): 'success' | 'warning' | 'danger' {
  if (level === 'low') return 'success'
  if (level === 'medium') return 'warning'
  return 'danger'
}

function getRiskLabel(level: string): string {
  if (level === 'low') return '低风险'
  if (level === 'medium') return '中风险'
  return '高风险'
}

function getConfidenceColor(confidence: number): string {
  if (confidence >= 80) return '#67C23A'
  if (confidence >= 60) return '#409EFF'
  if (confidence >= 40) return '#E6A23C'
  return '#F56C6C'
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
// 数据加载
// ============================================================

async function fetchTasks() {
  loading.value = true
  try {
    const res = await smartMonitorApi.listTasks()
    if (res.success) {
      tasks.value = res.data?.items ?? []
    }
  } catch (err: any) {
    ElMessage.error(err?.message || '加载盯盘任务失败')
  } finally {
    loading.value = false
  }
}

async function handleRefresh() {
  await fetchTasks()
  ElMessage.success('已刷新')
}

// ============================================================
// 创建任务
// ============================================================

async function handleCreateTask() {
  if (!createForm.value.stock_code.trim()) {
    ElMessage.warning('请输入股票代码')
    return
  }

  creating.value = true
  try {
    const res = await smartMonitorApi.createTask({
      stock_code: createForm.value.stock_code.trim(),
      stock_name: createForm.value.stock_name.trim(),
      check_interval: createForm.value.check_interval,
      auto_notify: createForm.value.auto_notify,
      trading_hours_only: createForm.value.trading_hours_only,
    })
    if (res.success) {
      ElMessage.success('盯盘任务创建成功')
      showCreateDialog.value = false
      // 重置表单
      createForm.value = {
        stock_code: '',
        stock_name: '',
        check_interval: 300,
        trading_hours_only: true,
        auto_notify: true,
      }
      await fetchTasks()
    } else {
      ElMessage.error(res.message || '创建失败')
    }
  } catch (err: any) {
    ElMessage.error(err?.message || '创建盯盘任务失败')
  } finally {
    creating.value = false
  }
}

// ============================================================
// 启停开关
// ============================================================

async function handleToggleEnabled(task: SmartMonitorTask, enabled: boolean) {
  try {
    const res = await smartMonitorApi.updateTask(task._id, { enabled })
    if (res.success) {
      ElMessage.success(enabled ? '已启动盯盘' : '已停止盯盘')
      await fetchTasks()
    } else {
      ElMessage.error(res.message || '操作失败')
    }
  } catch (err: any) {
    ElMessage.error(err?.message || '操作失败')
  }
}

// ============================================================
// 编辑间隔
// ============================================================

async function handleChangeInterval(task: SmartMonitorTask, interval: number) {
  try {
    const res = await smartMonitorApi.updateTask(task._id, { check_interval: interval })
    if (res.success) {
      ElMessage.success('检查间隔已更新')
      await fetchTasks()
    } else {
      ElMessage.error(res.message || '更新失败')
    }
  } catch (err: any) {
    ElMessage.error(err?.message || '更新失败')
  }
}

// ============================================================
// 删除任务
// ============================================================

async function handleDeleteTask(task: SmartMonitorTask) {
  try {
    await ElMessageBox.confirm(
      `确定删除 ${task.stock_name || task.stock_code} 的盯盘任务？`,
      '确认删除',
      { type: 'warning' }
    )
  } catch {
    return // 用户取消
  }

  try {
    const res = await smartMonitorApi.deleteTask(task._id)
    if (res.success) {
      ElMessage.success('盯盘任务已删除')
      await fetchTasks()
    } else {
      ElMessage.error(res.message || '删除失败')
    }
  } catch (err: any) {
    ElMessage.error(err?.message || '删除失败')
  }
}

// ============================================================
// 决策历史
// ============================================================

async function openDecisionHistory(task: SmartMonitorTask) {
  currentTaskForDecision.value = task
  decisionDrawerTitle.value = `${task.stock_name || task.stock_code} 决策历史`
  showDecisionDrawer.value = true
  loadingDecisions.value = true

  try {
    const res = await smartMonitorApi.getDecisions({
      stock_code: task.stock_code,
      limit: 50,
    })
    if (res.success) {
      decisions.value = res.data?.items ?? []
    }
  } catch (err: any) {
    ElMessage.error(err?.message || '加载决策历史失败')
  } finally {
    loadingDecisions.value = false
  }
}

// ============================================================
// 一键下单
// ============================================================

async function handlePlaceOrder(dec: SmartMonitorDecision) {
  const side = dec.action === 'BUY' ? 'buy' : 'sell'
  const sideLabel = dec.action === 'BUY' ? '买入' : '卖出'

  try {
    await ElMessageBox.confirm(
      `确定${sideLabel} ${dec.stock_name || dec.stock_code}？\n信心度: ${dec.confidence}%`,
      `一键${sideLabel}`,
      { type: 'warning' }
    )
  } catch {
    return // 用户取消
  }

  orderingDecisionId.value = dec._id
  try {
    const res = await paperApi.placeOrder({
      code: dec.stock_code,
      side,
      quantity: 100, // 默认 100 股
      analysis_id: dec._id,
    })
    if (res.success) {
      ElMessage.success(`${sideLabel}委托已提交`)
    } else {
      ElMessage.error(res.message || '下单失败')
    }
  } catch (err: any) {
    ElMessage.error(err?.message || '下单失败')
  } finally {
    orderingDecisionId.value = null
  }
}

// ============================================================
// 生命周期
// ============================================================

onMounted(() => {
  fetchTasks()
})
</script>

<style scoped>
.smart-monitor {
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

/* 盯盘任务卡片网格 */
.task-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
  gap: 16px;
}

.task-card {
  transition: transform 0.2s, box-shadow 0.2s;
}

.task-card:hover {
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

.switch-row,
.interval-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 12px;
}

.switch-row .label,
.interval-row .label,
.last-decision .label {
  font-size: 13px;
  color: #909399;
}

.last-decision {
  margin-bottom: 12px;
}

.decision-brief {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 6px;
}

.confidence-text {
  font-size: 12px;
  color: #606266;
}

.no-decision {
  font-size: 13px;
  color: #C0C4CC;
  margin-top: 4px;
  display: block;
}

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

/* 决策历史时间线 */
.decision-timeline {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.decision-item {
  padding: 14px;
  border-radius: 8px;
  border-left: 4px solid #DCDFE6;
  background: #FAFAFA;
}

.decision-item.decision-buy {
  border-left-color: #67C23A;
  background: #F0F9EB;
}

.decision-item.decision-sell {
  border-left-color: #F56C6C;
  background: #FEF0F0;
}

.decision-item.decision-hold {
  border-left-color: #909399;
  background: #F4F4F5;
}

.decision-header {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 10px;
}

.decision-time {
  font-size: 12px;
  color: #909399;
  margin-left: auto;
}

.decision-confidence {
  display: flex;
  align-items: center;
  margin-bottom: 10px;
  font-size: 13px;
  color: #909399;
}

.decision-reasoning {
  font-size: 13px;
  color: #606266;
  line-height: 1.6;
  margin-bottom: 10px;
}

.decision-prices {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-bottom: 10px;
}

.decision-order {
  display: flex;
  justify-content: flex-end;
}
</style>
