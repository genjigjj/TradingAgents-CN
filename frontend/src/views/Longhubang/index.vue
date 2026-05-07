<template>
  <div class="longhubang">
    <!-- 页面标题 -->
    <div class="page-header">
      <h1 class="page-title">
        <el-icon><DataAnalysis /></el-icon>
        龙虎榜分析
      </h1>
      <p class="page-description">
        AI 智能评分排名 + 5 位分析师协同分析，洞察游资动向与市场热点
      </p>
    </div>

    <!-- ============================================================ -->
    <!-- 主标签页 -->
    <!-- ============================================================ -->
    <el-tabs v-model="activeMainTab" type="border-card" class="main-tabs">
      <!-- ======================================================== -->
      <!-- 龙虎榜分析标签页 -->
      <!-- ======================================================== -->
      <el-tab-pane label="龙虎榜分析" name="analysis">
        <!-- 分析参数设置 -->
        <el-card class="filter-panel" shadow="never">
          <template #header>
            <div class="card-header">
              <span>分析参数</span>
              <div class="header-actions">
                <el-button text @click="handleReset">
                  <el-icon><Refresh /></el-icon>
                  重置
                </el-button>
              </div>
            </div>
          </template>

          <el-form label-width="120px" class="filter-form">
            <el-row :gutter="24">
              <!-- 分析模式 -->
              <el-col :span="8">
                <el-form-item label="分析模式">
                  <el-radio-group v-model="store.analyzeMode">
                    <el-radio-button value="date">指定日期</el-radio-button>
                    <el-radio-button value="days">最近 N 天</el-radio-button>
                  </el-radio-group>
                </el-form-item>
              </el-col>

              <!-- 指定日期 -->
              <el-col :span="8" v-if="store.analyzeMode === 'date'">
                <el-form-item label="分析日期">
                  <el-date-picker
                    v-model="store.analyzeDate"
                    type="date"
                    placeholder="选择日期（默认最近交易日）"
                    value-format="YYYY-MM-DD"
                    style="width: 100%"
                  />
                </el-form-item>
              </el-col>

              <!-- 最近 N 天 -->
              <el-col :span="8" v-if="store.analyzeMode === 'days'">
                <el-form-item label="天数">
                  <div class="days-control">
                    <el-input-number
                      v-model="store.analyzeDays"
                      :min="1"
                      :max="10"
                      :step="1"
                      controls-position="right"
                      style="width: 140px"
                    />
                    <span class="days-hint">天（1~10）</span>
                  </div>
                </el-form-item>
              </el-col>

              <!-- 操作按钮 -->
              <el-col :span="8">
                <el-form-item label=" ">
                  <el-button
                    type="primary"
                    size="large"
                    :loading="store.isAnalyzing"
                    :disabled="store.isAnalyzing"
                    @click="handleStartAnalysis"
                  >
                    <el-icon><DataAnalysis /></el-icon>
                    开始分析
                  </el-button>
                </el-form-item>
              </el-col>
            </el-row>

            <!-- AI 模型选择 -->
            <el-row :gutter="24">
              <el-col :span="12">
                <el-form-item label="AI 分析模型">
                  <el-select
                    v-model="store.selectedModel"
                    placeholder="自动选择（推荐）"
                    clearable
                    filterable
                    style="width: 100%"
                  >
                    <el-option-group
                      v-for="group in store.availableModels"
                      :key="group.provider"
                      :label="group.provider_name"
                    >
                      <el-option
                        v-for="m in group.models"
                        :key="m.name"
                        :label="m.display_name || m.name"
                        :value="m.name"
                      />
                    </el-option-group>
                  </el-select>
                </el-form-item>
              </el-col>
              <el-col :span="12">
                <el-form-item>
                  <span class="model-hint">留空则自动选择系统推荐的模型</span>
                </el-form-item>
              </el-col>
            </el-row>
          </el-form>
        </el-card>

        <!-- 分析进度 -->
        <el-card
          v-if="store.analysisStatus !== 'idle'"
          class="progress-panel"
          shadow="never"
        >
          <template #header>
            <div class="card-header">
              <span>分析进度</span>
              <el-tag :type="statusTagType" size="small">{{ statusText }}</el-tag>
            </div>
          </template>

          <div class="progress-content">
            <el-progress
              :percentage="store.progress"
              :status="progressStatus"
              :stroke-width="20"
              :striped="store.isAnalyzing"
              :striped-flow="store.isAnalyzing"
              :duration="10"
            />

            <!-- 分析步骤指示器 -->
            <div class="analysis-steps" v-if="store.isAnalyzing || store.analysisStatus === 'completed'">
              <el-steps :active="currentStepIndex" finish-status="success" align-center size="small">
                <el-step v-for="step in analysisSteps" :key="step.key" :title="step.title" :icon="step.icon">
                  <template #description>
                    <span class="step-desc" :class="{ active: step.key === currentStepKey }">
                      {{ step.desc }}
                    </span>
                  </template>
                </el-step>
              </el-steps>
            </div>

            <p class="progress-step" v-else>{{ store.currentStep }}</p>

            <el-alert
              v-if="store.errorMessage"
              :title="store.errorMessage"
              type="error"
              show-icon
              :closable="false"
              style="margin-top: 12px"
            />
          </div>
        </el-card>

        <!-- 数据概况卡片 -->
        <div v-if="store.dataInfo" class="data-overview">
          <el-row :gutter="16">
            <el-col :span="6">
              <el-card shadow="hover" class="stat-card">
                <div class="stat-value">{{ store.dataInfo.total_records || 0 }}</div>
                <div class="stat-label">龙虎榜记录数</div>
              </el-card>
            </el-col>
            <el-col :span="6">
              <el-card shadow="hover" class="stat-card">
                <div class="stat-value">{{ store.dataInfo.total_stocks || 0 }}</div>
                <div class="stat-label">涉及股票数</div>
              </el-card>
            </el-col>
            <el-col :span="6">
              <el-card shadow="hover" class="stat-card">
                <div class="stat-value">{{ store.dataInfo.total_youzi || 0 }}</div>
                <div class="stat-label">涉及游资数</div>
              </el-card>
            </el-col>
            <el-col :span="6">
              <el-card shadow="hover" class="stat-card">
                <div class="stat-value">{{ store.recommendedStocks.length }}</div>
                <div class="stat-label">推荐股票数</div>
              </el-card>
            </el-col>
          </el-row>
          <div v-if="store.dateRange" class="date-range-info">
            <el-tag type="info" effect="plain">数据日期范围: {{ store.dateRange }}</el-tag>
          </div>
        </div>

        <!-- AI 评分 TOP10 表格 -->
        <el-card
          v-if="store.hasScoringData"
          class="scoring-panel"
          shadow="never"
        >
          <template #header>
            <div class="card-header">
              <span>AI 智能评分 TOP10</span>
              <el-button text type="primary" @click="downloadReport">
                <el-icon><Download /></el-icon>
                下载报告
              </el-button>
            </div>
          </template>

          <el-table :data="store.scoringTop10" stripe style="width: 100%">
            <el-table-column prop="rank" label="排名" width="70" align="center">
              <template #default="{ row }">
                <el-tag
                  :type="row.rank <= 3 ? 'danger' : row.rank <= 5 ? 'warning' : 'info'"
                  size="small"
                  effect="dark"
                >
                  #{{ row.rank }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="stock_name" label="股票名称" width="120" />
            <el-table-column prop="stock_code" label="代码" width="100">
              <template #default="{ row }">
                <el-link type="primary">{{ row.stock_code }}</el-link>
              </template>
            </el-table-column>
            <el-table-column prop="total_score" label="综合评分" width="110" align="center" sortable>
              <template #default="{ row }">
                <span class="score-value" :style="{ color: getScoreColor(row.total_score) }">
                  {{ row.total_score.toFixed(1) }}
                </span>
              </template>
            </el-table-column>
            <el-table-column label="资金含金量(30)" width="150">
              <template #default="{ row }">
                <el-progress
                  :percentage="(row.capital_quality / 30) * 100"
                  :stroke-width="12"
                  :color="getProgressColor(row.capital_quality / 30)"
                  :format="() => row.capital_quality.toFixed(1)"
                />
              </template>
            </el-table-column>
            <el-table-column label="净买入额(25)" width="140">
              <template #default="{ row }">
                <el-progress
                  :percentage="(row.net_inflow_score / 25) * 100"
                  :stroke-width="12"
                  :color="getProgressColor(row.net_inflow_score / 25)"
                  :format="() => row.net_inflow_score.toFixed(1)"
                />
              </template>
            </el-table-column>
            <el-table-column label="卖出压力(20)" width="140">
              <template #default="{ row }">
                <el-progress
                  :percentage="(row.sell_pressure / 20) * 100"
                  :stroke-width="12"
                  :color="getProgressColor(row.sell_pressure / 20)"
                  :format="() => row.sell_pressure.toFixed(1)"
                />
              </template>
            </el-table-column>
            <el-table-column label="机构共振(15)" width="140">
              <template #default="{ row }">
                <el-progress
                  :percentage="(row.institution_score / 15) * 100"
                  :stroke-width="12"
                  :color="getProgressColor(row.institution_score / 15)"
                  :format="() => row.institution_score.toFixed(1)"
                />
              </template>
            </el-table-column>
            <el-table-column label="加分项(10)" width="130">
              <template #default="{ row }">
                <el-progress
                  :percentage="(row.bonus / 10) * 100"
                  :stroke-width="12"
                  :color="getProgressColor(row.bonus / 10)"
                  :format="() => row.bonus.toFixed(1)"
                />
              </template>
            </el-table-column>
            <el-table-column prop="top_youzi_count" label="顶级游资" width="90" align="center">
              <template #default="{ row }">
                <el-tag v-if="row.top_youzi_count > 0" type="danger" size="small">
                  {{ row.top_youzi_count }}
                </el-tag>
                <span v-else>-</span>
              </template>
            </el-table-column>
            <el-table-column prop="buy_seats" label="买方数" width="80" align="center" />
            <el-table-column prop="has_institution" label="机构" width="70" align="center">
              <template #default="{ row }">
                <el-tag v-if="row.has_institution" type="success" size="small">是</el-tag>
                <el-tag v-else type="info" size="small">否</el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="net_inflow" label="净流入" width="120" align="right">
              <template #default="{ row }">
                <span :class="row.net_inflow >= 0 ? 'text-red' : 'text-green'">
                  {{ formatAmount(row.net_inflow) }}
                </span>
              </template>
            </el-table-column>
          </el-table>
        </el-card>

        <!-- ECharts 评分可视化 -->
        <el-row v-if="store.hasScoringData" :gutter="16" class="charts-row">
          <!-- 柱状图 -->
          <el-col :span="14">
            <el-card shadow="never" class="chart-card">
              <template #header>
                <span>TOP10 综合评分柱状图</span>
              </template>
              <v-chart :option="barChartOption" style="height: 400px" autoresize />
            </el-card>
          </el-col>
          <!-- 雷达图 -->
          <el-col :span="10">
            <el-card shadow="never" class="chart-card">
              <template #header>
                <span>TOP5 五维评分雷达图</span>
              </template>
              <v-chart :option="radarChartOption" style="height: 400px" autoresize />
            </el-card>
          </el-col>
        </el-row>

        <!-- AI 分析师报告面板 -->
        <el-card
          v-if="store.hasAgentsReport"
          class="report-panel"
          shadow="never"
        >
          <template #header>
            <div class="card-header">
              <span>AI 分析师报告</span>
            </div>
          </template>

          <el-tabs v-model="activeAgentTab" type="border-card">
            <el-tab-pane
              v-for="agent in agentPanels"
              :key="agent.key"
              :label="agent.label"
              :name="agent.key"
            >
              <div
                class="report-content"
                v-html="renderMarkdown(getAgentAnalysis(agent.key))"
              />
            </el-tab-pane>
          </el-tabs>
        </el-card>

        <!-- 推荐股票列表 -->
        <el-card
          v-if="store.recommendedStocks.length > 0"
          class="recommend-panel"
          shadow="never"
        >
          <template #header>
            <div class="card-header">
              <span>推荐股票 ({{ store.recommendedStocks.length }} 只)</span>
            </div>
          </template>

          <el-table :data="store.recommendedStocks" stripe style="width: 100%">
            <el-table-column prop="rank" label="排名" width="70" align="center">
              <template #default="{ row }">
                <el-tag type="danger" size="small" effect="dark">#{{ row.rank }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="name" label="股票名称" width="120" />
            <el-table-column prop="code" label="代码" width="100">
              <template #default="{ row }">
                <el-link type="primary">{{ row.code }}</el-link>
              </template>
            </el-table-column>
            <el-table-column prop="net_inflow" label="净流入" width="120" align="right">
              <template #default="{ row }">
                <span :class="row.net_inflow >= 0 ? 'text-red' : 'text-green'">
                  {{ formatAmount(row.net_inflow) }}
                </span>
              </template>
            </el-table-column>
            <el-table-column prop="reason" label="推荐理由" min-width="250" show-overflow-tooltip />
            <el-table-column prop="confidence" label="确定性" width="90" align="center">
              <template #default="{ row }">
                <el-tag
                  :type="row.confidence === '高' ? 'danger' : row.confidence === '中' ? 'warning' : 'info'"
                  size="small"
                >
                  {{ row.confidence }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="hold_period" label="持有周期" width="100" align="center" />
          </el-table>
        </el-card>

        <!-- 数据详情区 -->
        <el-card
          v-if="store.dataInfo && hasDataDetails"
          class="data-detail-panel"
          shadow="never"
        >
          <template #header>
            <div class="card-header">
              <span>数据详情</span>
            </div>
          </template>

          <el-tabs v-model="activeDetailTab" type="card">
            <!-- 活跃游资 -->
            <el-tab-pane label="活跃游资 TOP10" name="youzi">
              <el-table
                :data="topYouziList"
                stripe
                style="width: 100%"
                size="small"
              >
                <el-table-column type="index" label="#" width="50" />
                <el-table-column prop="youzi_name" label="游资名称" min-width="180" />
                <el-table-column prop="trade_count" label="交易次数" width="100" align="center" />
                <el-table-column prop="total_buy" label="总买入" width="130" align="right">
                  <template #default="{ row }">
                    {{ formatAmount(row.total_buy) }}
                  </template>
                </el-table-column>
                <el-table-column prop="total_sell" label="总卖出" width="130" align="right">
                  <template #default="{ row }">
                    {{ formatAmount(row.total_sell) }}
                  </template>
                </el-table-column>
                <el-table-column prop="total_net_inflow" label="总净流入" width="130" align="right">
                  <template #default="{ row }">
                    <span :class="row.total_net_inflow >= 0 ? 'text-red' : 'text-green'">
                      {{ formatAmount(row.total_net_inflow) }}
                    </span>
                  </template>
                </el-table-column>
              </el-table>
            </el-tab-pane>

            <!-- 净流入 TOP20 -->
            <el-tab-pane label="净流入 TOP20" name="stocks">
              <el-table
                :data="topStocksList"
                stripe
                style="width: 100%"
                size="small"
              >
                <el-table-column type="index" label="#" width="50" />
                <el-table-column prop="stock_name" label="股票名称" min-width="100" />
                <el-table-column prop="stock_code" label="代码" width="90" />
                <el-table-column prop="youzi_count" label="游资关注" width="80" align="center" />
                <el-table-column prop="total_net_inflow" label="净流入" width="120" align="right">
                  <template #default="{ row }">
                    <span :class="row.total_net_inflow >= 0 ? 'text-red' : 'text-green'">
                      {{ formatAmount(row.total_net_inflow) }}
                    </span>
                  </template>
                </el-table-column>
                <el-table-column prop="concepts" label="概念" min-width="200" show-overflow-tooltip />
              </el-table>

              <v-chart
                v-if="netInflowChartOption"
                :option="netInflowChartOption"
                style="height: 400px; margin-top: 24px"
                autoresize
              />
            </el-tab-pane>

            <!-- 热门概念 -->
            <el-tab-pane label="热门概念 TOP20" name="concepts">
              <el-row :gutter="16">
                <el-col :span="10">
                  <el-table
                    :data="topConceptsList"
                    stripe
                    style="width: 100%"
                    size="small"
                  >
                    <el-table-column type="index" label="#" width="50" />
                    <el-table-column prop="name" label="概念名称" min-width="150" />
                    <el-table-column prop="count" label="出现次数" width="100" align="center" />
                  </el-table>
                </el-col>
                <el-col :span="14">
                  <v-chart
                    v-if="conceptPieChartOption"
                    :option="conceptPieChartOption"
                    style="height: 400px"
                    autoresize
                  />
                </el-col>
              </el-row>
            </el-tab-pane>
          </el-tabs>
        </el-card>

        <!-- 空状态 -->
        <el-empty
          v-if="showEmpty"
          description="选择分析模式后点击「开始分析」，AI 将为您评分排名并生成分析报告"
          :image-size="200"
        />
      </el-tab-pane>

    </el-tabs>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, watch, onMounted, onUnmounted } from 'vue'
import { ElMessage } from 'element-plus'
import {
  DataAnalysis,
  Refresh,
  Download,
  TrendCharts,
  Warning,
  Star,
  Aim,
} from '@element-plus/icons-vue'
import VChart from 'vue-echarts'
import { use } from 'echarts/core'
import { BarChart, RadarChart, PieChart } from 'echarts/charts'
import {
  GridComponent,
  TooltipComponent,
  LegendComponent,
  RadarComponent,
} from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import { useLonghubangStore } from '@/stores/longhubang'
import type { LonghubangReport } from '@/api/longhubang'
import { marked } from 'marked'

// 配置 marked
marked.setOptions({ breaks: true, gfm: true })

// 注册 ECharts 组件
use([BarChart, RadarChart, PieChart, GridComponent, TooltipComponent, LegendComponent, RadarComponent, CanvasRenderer])

// ============================================================
// Store
// ============================================================
const store = useLonghubangStore()

// ============================================================
// 本地状态
// ============================================================

/** 主标签页 */
const activeMainTab = ref('analysis')

/** 数据详情标签页 */
const activeDetailTab = ref('youzi')

/** 当前激活的分析师报告标签 */
const activeAgentTab = ref('chief')

/** 展开的历史报告行 */
const expandedReportIds = ref<string[]>([])

/** 天数滑块标记 */
const daysMarks = {
  1: '1',
  3: '3',
  5: '5',
  7: '7',
  10: '10',
}

// ============================================================
// 分析师面板配置
// ============================================================
const agentPanels = [
  { key: 'chief', label: '首席策略师', role: '综合策略', icon: Star, tagType: 'danger' as const },
  { key: 'youzi', label: '游资行为分析师', role: '游资追踪', icon: TrendCharts, tagType: 'warning' as const },
  { key: 'stock', label: '个股潜力分析师', role: '个股挖掘', icon: Aim, tagType: 'primary' as const },
  { key: 'theme', label: '题材追踪分析师', role: '题材热点', icon: DataAnalysis, tagType: 'success' as const },
  { key: 'risk', label: '风险控制专家', role: '风险评估', icon: Warning, tagType: 'info' as const },
]

// ============================================================
// 分析步骤配置
// ============================================================
const analysisSteps = [
  { key: 'youzi', title: '游资分析', desc: '分析游资操作特征' },
  { key: 'stock', title: '个股挖掘', desc: '挖掘潜力股票' },
  { key: 'theme', title: '题材追踪', desc: '识别热点题材' },
  { key: 'risk', title: '风险评估', desc: '识别风险陷阱' },
  { key: 'chief', title: '综合决策', desc: '首席策略师综合' },
  { key: 'save', title: '保存结果', desc: '持久化分析结果' },
]

/** 当前步骤 key */
const currentStepKey = computed(() => {
  const msg = store.currentStep || ''
  if (msg.includes('游资')) return 'youzi'
  if (msg.includes('个股')) return 'stock'
  if (msg.includes('题材')) return 'theme'
  if (msg.includes('风险')) return 'risk'
  if (msg.includes('首席') || msg.includes('策略师')) return 'chief'
  if (msg.includes('保存')) return 'save'
  return ''
})

/** 当前步骤索引 */
const currentStepIndex = computed(() => {
  if (store.analysisStatus === 'completed') return analysisSteps.length
  const idx = analysisSteps.findIndex(s => s.key === currentStepKey.value)
  if (idx >= 0) return idx
  // 回退：基于进度百分比推断
  const p = store.progress
  if (p >= 95) return 5
  if (p >= 78) return 4
  if (p >= 58) return 3
  if (p >= 38) return 2
  if (p >= 18) return 1
  return 0
})

// ============================================================
// 计算属性
// ============================================================

/** 是否显示空状态 */
const showEmpty = computed(() => {
  return (
    store.analysisStatus === 'idle' &&
    !store.dataInfo &&
    !store.hasScoringData
  )
})

/** 进度条状态 */
const progressStatus = computed(() => {
  if (store.analysisStatus === 'completed') return 'success'
  if (store.analysisStatus === 'failed') return 'exception'
  return undefined
})

/** 状态标签类型 */
const statusTagType = computed((): 'success' | 'warning' | 'danger' | 'info' | 'primary' => {
  const map: Record<string, 'success' | 'warning' | 'danger' | 'info' | 'primary'> = {
    idle: 'info',
    fetching: 'warning',
    analyzing: 'primary',
    completed: 'success',
    failed: 'danger',
  }
  return map[store.analysisStatus] || 'info'
})

/** 状态文字 */
const statusText = computed(() => {
  const map: Record<string, string> = {
    idle: '待开始',
    fetching: '获取数据中',
    analyzing: 'AI 分析中',
    completed: '已完成',
    failed: '失败',
  }
  return map[store.analysisStatus] || '未知'
})

/** 是否有数据详情 */
const hasDataDetails = computed(() => {
  const info = store.dataInfo
  if (!info) return false
  return (
    (info.top_youzi && info.top_youzi.length > 0) ||
    (info.top_stocks && info.top_stocks.length > 0) ||
    (info.top_concepts && info.top_concepts.length > 0)
  )
})

/** 活跃游资列表 */
const topYouziList = computed(() => {
  const raw = store.dataInfo?.top_youzi
  if (!raw) return []
  if (Array.isArray(raw)) return raw
  // 兼容旧的字典格式 {游资名: 净流入金额}
  return Object.entries(raw).map(([name, amount]) => ({
    youzi_name: name,
    total_net_inflow: amount as number,
    total_buy: 0,
    total_sell: 0,
    trade_count: '-',
  }))
})

/** 净流入 TOP 股票列表 */
const topStocksList = computed(() => {
  return store.dataInfo?.top_stocks || []
})

/** 热门概念列表 — 后端返回 {concept: count} 字典，转为数组 */
const topConceptsList = computed(() => {
  const raw = store.dataInfo?.hot_concepts || store.dataInfo?.top_concepts
  if (!raw) return []
  if (Array.isArray(raw)) return raw
  // 字典格式转数组
  return Object.entries(raw).map(([name, count], idx) => ({
    rank: idx + 1,
    name,
    count: count as number,
  }))
})

// ============================================================
// ECharts 配置
// ============================================================

/** TOP10 综合评分柱状图 */
const barChartOption = computed(() => {
  const data = store.scoringTop10
  if (!data || data.length === 0) return {}

  return {
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'shadow' },
    },
    grid: {
      left: '3%',
      right: '4%',
      bottom: '3%',
      containLabel: true,
    },
    xAxis: {
      type: 'category',
      data: data.map((d) => d.stock_name),
      axisLabel: {
        rotate: 30,
        fontSize: 11,
      },
    },
    yAxis: {
      type: 'value',
      name: '评分',
      max: 100,
    },
    series: [
      {
        name: '资金含金量',
        type: 'bar',
        stack: 'total',
        data: data.map((d) => d.capital_quality),
        itemStyle: { color: '#f56c6c' },
      },
      {
        name: '净买入额',
        type: 'bar',
        stack: 'total',
        data: data.map((d) => d.net_inflow_score),
        itemStyle: { color: '#e6a23c' },
      },
      {
        name: '卖出压力',
        type: 'bar',
        stack: 'total',
        data: data.map((d) => d.sell_pressure),
        itemStyle: { color: '#409eff' },
      },
      {
        name: '机构共振',
        type: 'bar',
        stack: 'total',
        data: data.map((d) => d.institution_score),
        itemStyle: { color: '#67c23a' },
      },
      {
        name: '加分项',
        type: 'bar',
        stack: 'total',
        data: data.map((d) => d.bonus),
        itemStyle: { color: '#909399' },
      },
    ],
    legend: {
      data: ['资金含金量', '净买入额', '卖出压力', '机构共振', '加分项'],
      bottom: 0,
    },
  }
})

/** TOP5 五维评分雷达图 */
const radarChartOption = computed(() => {
  const data = store.scoringTop5
  if (!data || data.length === 0) return {}

  const colors = ['#f56c6c', '#e6a23c', '#409eff', '#67c23a', '#909399']

  return {
    tooltip: {},
    legend: {
      data: data.map((d) => d.stock_name),
      bottom: 0,
    },
    radar: {
      indicator: [
        { name: '资金含金量', max: 30 },
        { name: '净买入额', max: 25 },
        { name: '卖出压力', max: 20 },
        { name: '机构共振', max: 15 },
        { name: '加分项', max: 10 },
      ],
      shape: 'polygon',
    },
    series: [
      {
        type: 'radar',
        data: data.map((d, i) => ({
          value: [
            d.capital_quality,
            d.net_inflow_score,
            d.sell_pressure,
            d.institution_score,
            d.bonus,
          ],
          name: d.stock_name,
          lineStyle: { color: colors[i % colors.length] },
          itemStyle: { color: colors[i % colors.length] },
          areaStyle: { color: colors[i % colors.length], opacity: 0.1 },
        })),
      },
    ],
  }
})

/** 资金净流入柱状图 */
const netInflowChartOption = computed(() => {
  const data = topStocksList.value
  if (!data || data.length === 0) return null

  const top10 = data.slice(0, 10)
  return {
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'shadow' },
    },
    grid: {
      left: '3%',
      right: '4%',
      bottom: '3%',
      containLabel: true,
    },
    xAxis: {
      type: 'category',
      data: top10.map((d: any) => d.stock_name || d.name),
      axisLabel: { rotate: 30, fontSize: 10 },
    },
    yAxis: {
      type: 'value',
      name: '净流入(万)',
      axisLabel: {
        formatter: (val: number) => (val / 10000).toFixed(0),
      },
    },
    series: [
      {
        name: '净流入',
        type: 'bar',
        data: top10.map((d: any) => ({
          value: d.total_net_inflow || d.net_inflow || 0,
          itemStyle: {
            color: (d.total_net_inflow || d.net_inflow || 0) >= 0 ? '#f56c6c' : '#67c23a',
          },
        })),
      },
    ],
  }
})

/** 热门概念饼图 */
const conceptPieChartOption = computed(() => {
  const data = topConceptsList.value
  if (!data || data.length === 0) return null

  const top10 = data.slice(0, 10)
  return {
    tooltip: {
      trigger: 'item',
      formatter: '{b}: {c} ({d}%)',
    },
    legend: {
      orient: 'vertical',
      right: '5%',
      top: 'center',
      data: top10.map((d: any) => d.name || d.concept),
    },
    series: [
      {
        name: '热门概念',
        type: 'pie',
        radius: ['35%', '65%'],
        center: ['40%', '50%'],
        avoidLabelOverlap: true,
        itemStyle: {
          borderRadius: 6,
          borderColor: '#fff',
          borderWidth: 2,
        },
        label: {
          show: true,
          formatter: '{b}: {c}',
        },
        data: top10.map((d: any) => ({
          name: d.name || d.concept,
          value: d.count || d.stock_count || 1,
        })),
      },
    ],
  }
})

// ============================================================
// 方法
// ============================================================

/** 获取分析师报告内容 */
function getAgentAnalysis(key: string): string {
  const report = store.agentsAnalysis[key]
  if (!report) return '暂无报告内容'
  return typeof report === 'string' ? report : report.analysis || '暂无报告内容'
}

/** 重置参数 */
function handleReset() {
  store.analyzeMode = 'date'
  store.analyzeDate = null
  store.analyzeDays = 1
}

/** 开始分析 */
async function handleStartAnalysis() {
  await store.startAnalysis()
  if (store.analysisStatus === 'failed') {
    ElMessage.error(store.errorMessage || '分析启动失败')
  }
}

/** 刷新历史报告 */
function handleRefreshReports() {
  store.fetchReports(store.reportsPage, store.reportsPageSize)
}

/** 报告分页变更 */
function handleReportsPageChange(page: number) {
  store.fetchReports(page, store.reportsPageSize)
}

/** 报告每页数量变更 */
function handleReportsPageSizeChange(size: number) {
  store.fetchReports(1, size)
}

/** 展开报告行 */
function handleReportExpand(_row: LonghubangReport, expandedRows: LonghubangReport[]) {
  expandedReportIds.value = expandedRows.map((r) => r._id)
}

/** 加载历史报告到当前结果 */
function handleLoadReport(report: LonghubangReport) {
  store.loadFromReport(report)
  activeMainTab.value = 'analysis'
  ElMessage.success('已加载历史报告到当前结果')
}

/** 删除历史报告 */
async function handleDeleteReport(reportId: string) {
  const success = await store.deleteReport(reportId)
  if (success) {
    ElMessage.success('报告已删除')
  } else {
    ElMessage.error('删除失败')
  }
}

/** 刷新统计数据 */
function handleRefreshStatistics() {
  store.fetchStatistics()
}

/** Markdown 渲染（使用 marked 库） */
function renderMarkdown(text: string): string {
  if (!text) return ''
  try {
    return String(marked.parse(text))
  } catch {
    return text
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/\n/g, '<br/>')
  }
}

/** 格式化金额 */
function formatAmount(amount: number): string {
  if (amount == null) return '-'
  const abs = Math.abs(amount)
  const sign = amount >= 0 ? '+' : '-'
  if (abs >= 1e8) return `${sign}${(abs / 1e8).toFixed(2)}亿`
  if (abs >= 1e4) return `${sign}${(abs / 1e4).toFixed(2)}万`
  return `${sign}${abs.toFixed(2)}`
}

/** 格式化日期 */
function formatDate(dateStr: string): string {
  if (!dateStr) return '-'
  try {
    const d = new Date(dateStr)
    return d.toLocaleString('zh-CN', {
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
    })
  } catch {
    return dateStr
  }
}

/** 评分颜色 */
function getScoreColor(score: number): string {
  if (score >= 80) return '#f56c6c'
  if (score >= 60) return '#e6a23c'
  if (score >= 40) return '#409eff'
  return '#909399'
}

/** 进度条颜色 */
function getProgressColor(ratio: number): string {
  if (ratio >= 0.8) return '#f56c6c'
  if (ratio >= 0.6) return '#e6a23c'
  if (ratio >= 0.4) return '#409eff'
  return '#909399'
}

/** 下载 Markdown 报告 */
function downloadReport() {
  const lines: string[] = []
  lines.push('# 龙虎榜分析报告\n')
  lines.push(`生成时间: ${new Date().toLocaleString()}\n`)
  if (store.dateRange) {
    lines.push(`数据日期范围: ${store.dateRange}\n`)
  }

  // 数据概况
  if (store.dataInfo) {
    lines.push('\n## 数据概况\n')
    lines.push(`- 龙虎榜记录数: ${store.dataInfo.total_records || 0}`)
    lines.push(`- 涉及股票数: ${store.dataInfo.total_stocks || 0}`)
    lines.push(`- 涉及游资数: ${store.dataInfo.total_youzi || 0}`)
    lines.push('')
  }

  // 评分排名
  if (store.scoringRanking.length > 0) {
    lines.push('\n## AI 智能评分排名\n')
    lines.push('| 排名 | 股票名称 | 代码 | 综合评分 | 资金含金量 | 净买入额 | 卖出压力 | 机构共振 | 加分项 |')
    lines.push('|------|----------|------|----------|------------|----------|----------|----------|--------|')
    store.scoringRanking.forEach((s) => {
      lines.push(
        `| ${s.rank} | ${s.stock_name} | ${s.stock_code} | ${s.total_score.toFixed(1)} | ${s.capital_quality.toFixed(1)} | ${s.net_inflow_score.toFixed(1)} | ${s.sell_pressure.toFixed(1)} | ${s.institution_score.toFixed(1)} | ${s.bonus.toFixed(1)} |`
      )
    })
    lines.push('')
  }

  // 分析师报告
  for (const agent of agentPanels) {
    const content = getAgentAnalysis(agent.key)
    if (content && content !== '暂无报告内容') {
      lines.push(`\n## ${agent.label}（${agent.role}）\n`)
      lines.push(content)
      lines.push('')
    }
  }

  // 推荐股票
  if (store.recommendedStocks.length > 0) {
    lines.push('\n## 推荐股票\n')
    store.recommendedStocks.forEach((s) => {
      lines.push(`### ${s.rank}. ${s.name} (${s.code})`)
      lines.push(`- 净流入: ${formatAmount(s.net_inflow)}`)
      lines.push(`- 推荐理由: ${s.reason}`)
      lines.push(`- 确定性: ${s.confidence}`)
      lines.push(`- 持有周期: ${s.hold_period}`)
      lines.push('')
    })
  }

  // 摘要
  if (store.summary) {
    lines.push('\n## 分析摘要\n')
    lines.push(store.summary)
  }

  const blob = new Blob([lines.join('\n')], { type: 'text/markdown;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `龙虎榜分析报告_${new Date().toISOString().slice(0, 10)}.md`
  a.click()
  URL.revokeObjectURL(url)
  ElMessage.success('报告下载成功')
}

// ============================================================
// 标签页切换时加载数据
// ============================================================
watch(activeMainTab, (tab) => {
  if (tab === 'history' && store.reports.length === 0) {
    store.fetchReports()
  }
  if (tab === 'statistics' && !store.statistics) {
    store.fetchStatistics()
  }
})

// ============================================================
// 生命周期
// ============================================================
onMounted(() => {
  // 加载可用模型列表
  store.fetchAvailableModels()

  // 如果有正在进行的分析任务，恢复轮询
  if (store.taskId && store.isAnalyzing) {
    store.startPolling()
  }
})

onUnmounted(() => {
  store.stopPolling()
})
</script>

<style lang="scss" scoped>
.longhubang {
  .page-header {
    margin-bottom: 24px;

    .page-title {
      display: flex;
      align-items: center;
      gap: 8px;
      font-size: 24px;
      font-weight: 600;
      color: var(--el-text-color-primary);
      margin: 0 0 8px 0;
    }

    .page-description {
      color: var(--el-text-color-regular);
      margin: 0;
    }
  }

  .card-header {
    display: flex;
    justify-content: space-between;
    align-items: center;

    .header-actions {
      display: flex;
      gap: 8px;
    }
  }

  .main-tabs {
    :deep(.el-tabs__content) {
      padding: 20px;
    }
  }

  .filter-panel {
    margin-bottom: 24px;

    .filter-form {
      .days-control {
        display: flex;
        align-items: center;
        gap: 8px;

        .days-hint {
          color: var(--el-text-color-secondary);
          font-size: 13px;
          white-space: nowrap;
        }
      }

      .model-hint {
        color: var(--el-text-color-placeholder);
        font-size: 12px;
        line-height: 32px;
      }
    }
  }

  .progress-panel {
    margin-bottom: 24px;

    .progress-content {
      .progress-step {
        margin-top: 12px;
        color: var(--el-text-color-regular);
        font-size: 14px;
        text-align: center;
      }

      .analysis-steps {
        margin-top: 20px;
        padding: 0 16px;

        .step-desc {
          font-size: 12px;
          color: var(--el-text-color-secondary);
          transition: color 0.3s;

          &.active {
            color: var(--el-color-primary);
            font-weight: 600;
          }
        }
      }
    }
  }

  .data-overview {
    margin-bottom: 24px;

    .stat-card {
      text-align: center;
      cursor: default;

      .stat-value {
        font-size: 28px;
        font-weight: 700;
        color: var(--el-color-primary);
        line-height: 1.4;
      }

      .stat-label {
        font-size: 13px;
        color: var(--el-text-color-secondary);
        margin-top: 4px;
      }
    }

    .date-range-info {
      margin-top: 12px;
      text-align: center;
    }
  }

  .scoring-panel {
    margin-bottom: 24px;

    .score-value {
      font-weight: 700;
      font-size: 16px;
    }
  }

  .charts-row {
    margin-bottom: 24px;

    .chart-card {
      height: 100%;
    }
  }

  .report-panel {
    margin-bottom: 24px;

    .agent-collapse-title {
      display: flex;
      align-items: center;
      gap: 8px;
      width: 100%;

      .agent-name {
        font-weight: 600;
        font-size: 15px;
      }
    }

    .report-content {
      padding: 16px;
      line-height: 1.8;
      font-size: 14px;
      color: var(--el-text-color-primary);

      :deep(h2) {
        font-size: 18px;
        margin: 16px 0 8px;
        color: var(--el-color-primary);
      }

      :deep(h3) {
        font-size: 16px;
        margin: 12px 0 6px;
      }

      :deep(h4) {
        font-size: 15px;
        margin: 10px 0 4px;
      }

      :deep(strong) {
        color: var(--el-text-color-primary);
      }
    }
  }

  .recommend-panel {
    margin-bottom: 24px;
  }

  .data-detail-panel {
    margin-bottom: 24px;
  }

  .history-panel {
    .report-expand-content {
      padding: 12px 24px;

      .expand-section {
        margin-bottom: 16px;

        h4 {
          font-size: 14px;
          font-weight: 600;
          margin: 0 0 8px 0;
          color: var(--el-text-color-primary);
        }

        p {
          margin: 0;
          line-height: 1.6;
          color: var(--el-text-color-regular);
        }
      }
    }

    .pagination-wrapper {
      display: flex;
      justify-content: center;
      margin-top: 16px;
    }
  }

  .statistics-panel {
    .statistics-content {
      :deep(.el-statistic) {
        text-align: center;
        padding: 16px 0;

        .el-statistic__head {
          font-size: 14px;
          color: var(--el-text-color-secondary);
        }

        .el-statistic__content {
          font-size: 28px;
          font-weight: 700;
          color: var(--el-color-primary);
        }
      }
    }
  }

  .text-red {
    color: #f56c6c;
  }

  .text-green {
    color: #67c23a;
  }
}
</style>
