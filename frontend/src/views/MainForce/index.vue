<template>
  <div class="main-force">
    <!-- 页面标题 -->
    <div class="page-header">
      <h1 class="page-title">
        <el-icon><TrendCharts /></el-icon>
        主力选股
      </h1>
      <p class="page-description">
        基于问财主力资金净流入排名数据，经智能筛选后由资金流向、行业板块、财务基本面三大 AI 分析师团队多维度分析，最终由综合研究员精选优质投资标的股票并生成分析报告
      </p>
    </div>

    <!-- ============================================================ -->
    <!-- 参数设置区 -->
    <!-- ============================================================ -->
    <el-card class="filter-panel" shadow="never">
      <template #header>
        <div class="card-header">
          <span>参数设置</span>
          <div class="header-actions">
            <el-button text @click="handleReset">
              <el-icon><Refresh /></el-icon>
              重置
            </el-button>
          </div>
        </div>
      </template>

      <el-form :model="store.screenParams" label-width="120px" class="filter-form">
        <el-row :gutter="24">
          <!-- 时间区间 -->
          <el-col :span="8">
            <el-form-item label="时间区间">
              <el-select
                v-model="store.screenParams.time_range"
                placeholder="选择时间区间"
                @change="handleTimeRangeChange"
              >
                <el-option label="最近 3 个月" value="3m" />
                <el-option label="最近 6 个月" value="6m" />
                <el-option label="最近 1 年" value="1y" />
                <el-option label="自定义日期" value="custom" />
              </el-select>
            </el-form-item>
          </el-col>

          <!-- 精选数量（始终显示） -->
          <el-col :span="8">
            <el-form-item label="精选数量">
              <div class="top-n-control">
                <el-input-number
                  v-model="store.screenParams.top_n"
                  :min="3"
                  :max="10"
                  :step="1"
                  controls-position="right"
                  style="width: 140px"
                />
                <span class="top-n-hint">只（3~10）</span>
              </div>
            </el-form-item>
          </el-col>
        </el-row>

        <!-- 自定义日期（单独一行，仅在自定义模式下显示） -->
        <el-row :gutter="24" v-if="store.screenParams.time_range === 'custom'">
          <el-col :span="8">
            <el-form-item label="开始日期">
              <el-date-picker
                v-model="store.screenParams.start_date"
                type="date"
                placeholder="选择开始日期"
                value-format="YYYY-MM-DD"
                style="width: 100%"
              />
            </el-form-item>
          </el-col>
          <el-col :span="8">
            <el-form-item label="结束日期">
              <el-date-picker
                v-model="store.screenParams.end_date"
                type="date"
                placeholder="选择结束日期"
                value-format="YYYY-MM-DD"
                style="width: 100%"
              />
            </el-form-item>
          </el-col>
        </el-row>

        <!-- 高级筛选参数 -->
        <el-collapse v-model="advancedExpanded">
          <el-collapse-item title="高级筛选参数" name="advanced">
            <el-row :gutter="24">
              <el-col :span="8">
                <el-form-item label="最大涨跌幅(%)">
                  <el-input-number
                    v-model="store.screenParams.max_change_pct"
                    :min="5"
                    :max="100"
                    :step="5"
                    :precision="0"
                    style="width: 100%"
                  />
                </el-form-item>
              </el-col>
              <el-col :span="8">
                <el-form-item label="最小市值(亿)">
                  <el-input-number
                    v-model="store.screenParams.min_market_cap"
                    :min="0"
                    :max="store.screenParams.max_market_cap"
                    :step="10"
                    :precision="0"
                    style="width: 100%"
                  />
                </el-form-item>
              </el-col>
              <el-col :span="8">
                <el-form-item label="最大市值(亿)">
                  <el-input-number
                    v-model="store.screenParams.max_market_cap"
                    :min="store.screenParams.min_market_cap"
                    :max="100000"
                    :step="100"
                    :precision="0"
                    style="width: 100%"
                  />
                </el-form-item>
              </el-col>
            </el-row>

            <!-- AI 模型选择 -->
            <el-row :gutter="24" style="margin-top: 12px">
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
                  <span class="model-hint">
                    留空则自动选择系统推荐的模型
                  </span>
                </el-form-item>
              </el-col>
            </el-row>
          </el-collapse-item>
        </el-collapse>

        <!-- 操作按钮 -->
        <el-row>
          <el-col :span="24">
            <div class="filter-actions">
              <el-button
                type="primary"
                size="large"
                :loading="store.isAnalyzing"
                :disabled="store.isAnalyzing"
                @click="handleStartAnalysis"
              >
                <el-icon><TrendCharts /></el-icon>
                开始主力选股
              </el-button>
              <el-button size="large" @click="handleScreenOnly" :loading="store.screenLoading">
                仅筛选候选
              </el-button>
            </div>
          </el-col>
        </el-row>
      </el-form>
    </el-card>

    <!-- ============================================================ -->
    <!-- 分析进度区 -->
    <!-- ============================================================ -->
    <el-card
      v-if="store.analysisStatus !== 'idle'"
      class="progress-panel"
      shadow="never"
    >
      <template #header>
        <div class="card-header">
          <span>分析进度</span>
          <el-tag
            :type="statusTagType"
            size="small"
          >
            {{ statusText }}
          </el-tag>
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
          <el-steps :active="mfCurrentStepIndex" finish-status="success" align-center size="small">
            <el-step v-for="step in mfAnalysisSteps" :key="step.key" :title="step.title">
              <template #description>
                <span class="step-desc" :class="{ active: step.key === mfCurrentStepKey }">
                  {{ step.desc }}
                </span>
              </template>
            </el-step>
          </el-steps>
        </div>

        <p class="progress-step" v-else>{{ store.currentStep }}</p>

        <!-- 统计信息 -->
        <div v-if="store.totalFetched > 0" class="progress-stats">
          <el-tag type="info" effect="plain">获取: {{ store.totalFetched }} 只</el-tag>
          <el-tag type="success" effect="plain">筛选: {{ store.totalFiltered }} 只</el-tag>
          <el-tag v-if="store.recommendedStocks.length > 0" type="warning" effect="plain">
            推荐: {{ store.recommendedStocks.length }} 只
          </el-tag>
        </div>

        <!-- 错误信息 -->
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

    <!-- ============================================================ -->
    <!-- 分析师报告区 -->
    <!-- ============================================================ -->
    <el-card
      v-if="hasOverviewAnalysis"
      class="report-panel"
      shadow="never"
    >
      <template #header>
        <div class="card-header">
          <span>AI 分析师报告</span>
          <el-button text type="primary" @click="downloadReport">
            <el-icon><Download /></el-icon>
            下载报告
          </el-button>
        </div>
      </template>

      <el-tabs v-model="activeReportTab" type="border-card">
        <el-tab-pane
          v-for="analyst in analystTabs"
          :key="analyst.key"
          :label="analyst.label"
          :name="analyst.key"
        >
          <div class="report-content" v-html="renderMarkdown(store.overviewAnalysis[analyst.key] || '暂无报告内容')" />
        </el-tab-pane>
      </el-tabs>
    </el-card>

    <!-- ============================================================ -->
    <!-- 精选推荐区 -->
    <!-- ============================================================ -->
    <el-card
      v-if="store.recommendedStocks.length > 0"
      class="recommend-panel"
      shadow="never"
    >
      <template #header>
        <div class="card-header">
          <span>精选推荐 ({{ store.recommendedStocks.length }} 只)</span>
        </div>
      </template>

      <el-collapse v-model="expandedStocks">
        <el-collapse-item
          v-for="(stock, index) in store.recommendedStocks"
          :key="stock.code || index"
          :name="index"
        >
          <template #title>
            <div class="stock-collapse-title">
              <el-tag type="danger" size="small" effect="dark" class="rank-tag">
                #{{ index + 1 }}
              </el-tag>
              <span class="stock-name">{{ stock.name }}</span>
              <span class="stock-code">{{ stock.code }}</span>
              <el-tag size="small" effect="plain">{{ stock.period || '中线' }}</el-tag>
              <el-tag size="small" type="warning" effect="plain">仓位: {{ stock.position || '10%' }}</el-tag>
            </div>
          </template>

          <div class="stock-detail">
            <!-- 推荐理由 -->
            <div class="detail-section" v-if="stock.reason">
              <h4>推荐理由</h4>
              <p>{{ stock.reason }}</p>
            </div>

            <!-- 投资亮点 -->
            <div class="detail-section" v-if="stock.highlights && stock.highlights.length > 0">
              <h4>投资亮点</h4>
              <ul>
                <li v-for="(h, i) in stock.highlights" :key="i">{{ h }}</li>
              </ul>
            </div>

            <!-- 风险提示 -->
            <div class="detail-section" v-if="stock.risks && stock.risks.length > 0">
              <h4>风险提示</h4>
              <ul class="risk-list">
                <li v-for="(r, i) in stock.risks" :key="i">{{ r }}</li>
              </ul>
            </div>

            <!-- 建议仓位与周期 -->
            <div class="detail-meta">
              <el-descriptions :column="3" border size="small">
                <el-descriptions-item label="建议仓位">{{ stock.position || '-' }}</el-descriptions-item>
                <el-descriptions-item label="投资周期">{{ stock.period || '-' }}</el-descriptions-item>
                <el-descriptions-item label="股票代码">{{ stock.code || '-' }}</el-descriptions-item>
              </el-descriptions>
            </div>
          </div>
        </el-collapse-item>
      </el-collapse>
    </el-card>

    <!-- ============================================================ -->
    <!-- 候选列表区 -->
    <!-- ============================================================ -->
    <el-card
      v-if="store.candidates.length > 0"
      class="candidates-panel"
      shadow="never"
    >
      <template #header>
        <div class="card-header">
          <span>候选股票列表 ({{ store.candidates.length }} 只)</span>
          <div class="header-actions">
            <el-button text type="primary" @click="downloadCsv">
              <el-icon><Download /></el-icon>
              导出 CSV
            </el-button>
          </div>
        </div>
      </template>

      <el-table
        :data="paginatedCandidates"
        stripe
        style="width: 100%"
        :default-sort="{ prop: 'net_inflow', order: 'descending' }"
      >
        <el-table-column prop="code" label="股票代码" width="110">
          <template #default="{ row }">
            <el-link type="primary">{{ row.code }}</el-link>
          </template>
        </el-table-column>
        <el-table-column prop="name" label="股票名称" width="120" />
        <el-table-column prop="industry" label="行业" width="110" />
        <el-table-column prop="net_inflow" label="主力净流入" width="140" align="right" sortable>
          <template #default="{ row }">
            <span :class="row.net_inflow >= 0 ? 'text-red' : 'text-green'">
              {{ formatAmount(row.net_inflow) }}
            </span>
          </template>
        </el-table-column>
        <el-table-column prop="change_pct" label="涨跌幅" width="100" align="right" sortable>
          <template #default="{ row }">
            <span :class="getChangeClass(row.change_pct)">
              {{ row.change_pct > 0 ? '+' : '' }}{{ row.change_pct?.toFixed(2) }}%
            </span>
          </template>
        </el-table-column>
        <el-table-column prop="market_cap" label="市值(亿)" width="110" align="right" sortable>
          <template #default="{ row }">
            {{ row.market_cap?.toFixed(2) }}
          </template>
        </el-table-column>
        <el-table-column prop="pe_ratio" label="市盈率" width="100" align="right">
          <template #default="{ row }">
            {{ row.pe_ratio != null ? row.pe_ratio.toFixed(2) : '-' }}
          </template>
        </el-table-column>
        <el-table-column prop="pb_ratio" label="市净率" width="100" align="right">
          <template #default="{ row }">
            {{ row.pb_ratio != null ? row.pb_ratio.toFixed(2) : '-' }}
          </template>
        </el-table-column>
      </el-table>

      <!-- 分页 -->
      <div class="pagination-wrapper" v-if="store.candidates.length > candidatePageSize">
        <el-pagination
          v-model:current-page="candidatePage"
          v-model:page-size="candidatePageSize"
          :page-sizes="[20, 50, 100]"
          :total="store.candidates.length"
          layout="total, sizes, prev, pager, next"
        />
      </div>
    </el-card>

    <!-- ============================================================ -->
    <!-- 批量分析区 -->
    <!-- ============================================================ -->
    <el-card
      v-if="store.hasCandidates && store.hasResult"
      class="batch-panel"
      shadow="never"
    >
      <template #header>
        <div class="card-header">
          <span>批量深度分析</span>
        </div>
      </template>

      <el-form label-width="120px">
        <el-row :gutter="24">
          <el-col :span="8">
            <el-form-item label="分析数量">
              <el-select v-model="store.batchCount" placeholder="选择分析数量">
                <el-option :label="`TOP 10`" :value="10" />
                <el-option :label="`TOP 20`" :value="20" />
                <el-option :label="`TOP 30`" :value="30" />
                <el-option :label="`TOP 50`" :value="50" />
              </el-select>
            </el-form-item>
          </el-col>
          <el-col :span="8">
            <el-form-item label="分析模式">
              <el-select v-model="store.analysisMode" placeholder="选择分析模式">
                <el-option label="整体分析" value="overview" />
                <el-option label="批量深度分析" value="batch" />
              </el-select>
            </el-form-item>
          </el-col>
          <el-col :span="8">
            <el-form-item label="研究深度">
              <el-select v-model="store.researchDepth" placeholder="选择研究深度">
                <el-option label="快速" value="快速" />
                <el-option label="标准" value="标准" />
                <el-option label="深度" value="深度" />
              </el-select>
            </el-form-item>
          </el-col>
        </el-row>

        <div class="filter-actions">
          <el-button
            type="warning"
            size="large"
            :loading="store.analyzeLoading"
            :disabled="store.isAnalyzing"
            @click="handleBatchAnalysis"
          >
            <el-icon><DataAnalysis /></el-icon>
            启动批量分析
          </el-button>
        </div>
      </el-form>

      <!-- 批量分析结果 -->
      <div v-if="store.batchResults" class="batch-results">
        <el-divider content-position="left">批量分析结果</el-divider>

        <div class="batch-stats">
          <el-tag type="success" effect="plain">成功: {{ store.batchResults.success }}</el-tag>
          <el-tag type="danger" effect="plain">失败: {{ store.batchResults.failed }}</el-tag>
          <el-tag type="info" effect="plain">
            耗时: {{ formatDuration(store.batchResults.duration_seconds) }}
          </el-tag>
        </div>

        <el-table
          v-if="store.batchResults.results && store.batchResults.results.length > 0"
          :data="store.batchResults.results"
          stripe
          style="width: 100%; margin-top: 16px"
        >
          <el-table-column prop="code" label="代码" width="100" />
          <el-table-column prop="name" label="名称" width="120" />
          <el-table-column prop="status" label="状态" width="80">
            <template #default="{ row }">
              <el-tag :type="row.status === 'success' ? 'success' : 'danger'" size="small">
                {{ row.status === 'success' ? '成功' : '失败' }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="rating" label="评级" width="100" />
          <el-table-column prop="confidence" label="信心度" width="100">
            <template #default="{ row }">
              {{ row.confidence != null ? `${(row.confidence * 100).toFixed(0)}%` : '-' }}
            </template>
          </el-table-column>
          <el-table-column prop="entry_range" label="进场区间" width="120" />
          <el-table-column prop="take_profit" label="止盈位" width="100" />
          <el-table-column prop="stop_loss" label="止损位" width="100" />
          <el-table-column prop="target_price" label="目标价" width="100" />
          <el-table-column prop="advice" label="投资建议" min-width="200" show-overflow-tooltip />
        </el-table>
      </div>
    </el-card>

    <!-- 空状态 -->
    <el-empty
      v-if="showEmpty"
      description="设置参数后点击「开始主力选股」，AI 分析师团队将为您精选优质标的"
      :image-size="200"
    />
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { ElMessage } from 'element-plus'
import {
  TrendCharts,
  Refresh,
  Download,
  DataAnalysis,
} from '@element-plus/icons-vue'
import { useMainForceStore } from '@/stores/mainForce'
import { marked } from 'marked'

// 配置 marked
marked.setOptions({ breaks: true, gfm: true })

// ============================================================
// Store
// ============================================================
const store = useMainForceStore()

// ============================================================
// 本地状态
// ============================================================

/** 高级筛选展开状态 */
const advancedExpanded = ref<string[]>([])

/** 当前激活的分析师报告标签 */
const activeReportTab = ref('fund_flow_analyst')

/** 展开的推荐股票 */
const expandedStocks = ref<number[]>([0])

/** 候选列表分页 */
const candidatePage = ref(1)
const candidatePageSize = ref(20)

// ============================================================
// 分析师标签页配置
// ============================================================
const analystTabs = [
  { key: 'fund_flow_analyst', label: '资金流向分析' },
  { key: 'industry_analyst', label: '行业板块分析' },
  { key: 'fundamental_analyst', label: '财务基本面分析' },
  { key: 'comprehensive_researcher', label: '综合研究员' },
]

// ============================================================
// 分析步骤配置
// ============================================================
const mfAnalysisSteps = [
  { key: 'fund_flow', title: '资金流向', desc: '分析主力资金流向' },
  { key: 'industry', title: '行业板块', desc: '分析行业板块趋势' },
  { key: 'fundamental', title: '财务基本面', desc: '分析财务指标' },
  { key: 'researcher', title: '综合研究', desc: '综合研究员精选' },
  { key: 'save', title: '保存结果', desc: '持久化分析结果' },
]

const mfCurrentStepKey = computed(() => {
  const msg = store.currentStep || ''
  if (msg.includes('资金流向')) return 'fund_flow'
  if (msg.includes('行业板块')) return 'industry'
  if (msg.includes('财务') || msg.includes('基本面')) return 'fundamental'
  if (msg.includes('综合') || msg.includes('研究员')) return 'researcher'
  if (msg.includes('保存')) return 'save'
  return ''
})

const mfCurrentStepIndex = computed(() => {
  if (store.analysisStatus === 'completed') return mfAnalysisSteps.length
  const idx = mfAnalysisSteps.findIndex(s => s.key === mfCurrentStepKey.value)
  if (idx >= 0) return idx
  // 回退：基于进度百分比推断
  const p = store.progress
  if (p >= 95) return 4
  if (p >= 80) return 3
  if (p >= 60) return 2
  if (p >= 35) return 1
  return 0
})

// ============================================================
// 计算属性
// ============================================================

/** 是否有整体分析报告 */
const hasOverviewAnalysis = computed(() => {
  return Object.keys(store.overviewAnalysis).length > 0
})

/** 是否显示空状态 */
const showEmpty = computed(() => {
  return (
    store.analysisStatus === 'idle' &&
    store.candidates.length === 0 &&
    !store.screenLoading
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
    screening: 'warning',
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
    screening: '筛选中',
    analyzing: '分析中',
    completed: '已完成',
    failed: '失败',
  }
  return map[store.analysisStatus] || '未知'
})

/** 分页后的候选列表 */
const paginatedCandidates = computed(() => {
  const start = (candidatePage.value - 1) * candidatePageSize.value
  return store.candidates.slice(start, start + candidatePageSize.value)
})

// ============================================================
// 方法
// ============================================================

/** 时间区间变更 */
function handleTimeRangeChange(val: string) {
  if (val !== 'custom') {
    store.screenParams.start_date = null
    store.screenParams.end_date = null
  }
}

/** 重置参数 */
function handleReset() {
  store.resetScreenParams()
  advancedExpanded.value = []
  candidatePage.value = 1
}

/** 仅筛选候选 */
async function handleScreenOnly() {
  await store.fetchScreen()
  if (store.errorMessage) {
    ElMessage.error(store.errorMessage)
  } else {
    ElMessage.success(`筛选完成，共 ${store.candidates.length} 只候选股票`)
  }
}

/** 开始完整分析 */
async function handleStartAnalysis() {
  store.analysisMode = 'overview'
  await store.startAnalysis()
  if (store.analysisStatus === 'failed') {
    ElMessage.error(store.errorMessage || '分析启动失败')
  }
}

/** 启动批量分析 */
async function handleBatchAnalysis() {
  store.analysisMode = 'batch'
  await store.startAnalysis()
  if (store.analysisStatus === 'failed') {
    ElMessage.error(store.errorMessage || '批量分析启动失败')
  }
}

/** Markdown 渲染（使用 marked 库） */
function renderMarkdown(text: string): string {
  if (!text) return ''
  try {
    return String(marked.parse(text))
  } catch {
    // 降级：基本的 HTML 转义和换行处理
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

/** 涨跌幅样式 */
function getChangeClass(val: number): string {
  if (val > 0) return 'text-red'
  if (val < 0) return 'text-green'
  return ''
}

/** 格式化耗时 */
function formatDuration(seconds: number): string {
  if (seconds == null) return '-'
  if (seconds < 60) return `${seconds.toFixed(0)}秒`
  const min = Math.floor(seconds / 60)
  const sec = Math.round(seconds % 60)
  return `${min}分${sec}秒`
}

/** 下载 Markdown 报告 */
function downloadReport() {
  const lines: string[] = []
  lines.push('# 主力选股分析报告\n')
  lines.push(`生成时间: ${new Date().toLocaleString()}\n`)

  // 分析师报告
  for (const tab of analystTabs) {
    const content = store.overviewAnalysis[tab.key]
    if (content) {
      lines.push(`\n## ${tab.label}\n`)
      lines.push(content)
    }
  }

  // 精选推荐
  if (store.recommendedStocks.length > 0) {
    lines.push('\n## 精选推荐\n')
    store.recommendedStocks.forEach((s: any, i: number) => {
      lines.push(`### ${i + 1}. ${s.name} (${s.code})`)
      if (s.reason) lines.push(`- 推荐理由: ${s.reason}`)
      if (s.highlights?.length) lines.push(`- 投资亮点: ${s.highlights.join('；')}`)
      if (s.risks?.length) lines.push(`- 风险提示: ${s.risks.join('；')}`)
      lines.push(`- 建议仓位: ${s.position || '-'}`)
      lines.push(`- 投资周期: ${s.period || '-'}`)
      lines.push('')
    })
  }

  const blob = new Blob([lines.join('\n')], { type: 'text/markdown;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `主力选股报告_${new Date().toISOString().slice(0, 10)}.md`
  a.click()
  URL.revokeObjectURL(url)
  ElMessage.success('报告下载成功')
}

/** 下载候选列表 CSV */
function downloadCsv() {
  if (store.candidates.length === 0) {
    ElMessage.warning('暂无候选数据')
    return
  }

  const header = '股票代码,股票名称,行业,主力净流入,涨跌幅(%),市值(亿),市盈率,市净率'
  const rows = store.candidates.map((c) =>
    [
      c.code,
      c.name,
      c.industry,
      c.net_inflow,
      c.change_pct?.toFixed(2),
      c.market_cap?.toFixed(2),
      c.pe_ratio != null ? c.pe_ratio.toFixed(2) : '',
      c.pb_ratio != null ? c.pb_ratio.toFixed(2) : '',
    ].join(',')
  )

  const csvContent = '\uFEFF' + [header, ...rows].join('\n')
  const blob = new Blob([csvContent], { type: 'text/csv;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `主力选股候选_${new Date().toISOString().slice(0, 10)}.csv`
  a.click()
  URL.revokeObjectURL(url)
  ElMessage.success('CSV 导出成功')
}

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
.main-force {
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

  .filter-panel {
    margin-bottom: 24px;

    .filter-form {
      .top-n-control {
        display: flex;
        align-items: center;
        gap: 8px;

        .top-n-hint {
          color: var(--el-text-color-secondary);
          font-size: 13px;
          white-space: nowrap;
        }
      }

      .filter-actions {
        display: flex;
        justify-content: center;
        gap: 16px;
        margin-top: 24px;
      }
    }

    :deep(.el-collapse) {
      border: none;
      margin-top: 12px;
    }

    :deep(.el-collapse-item__header) {
      background: transparent;
      font-size: 14px;
      color: var(--el-text-color-regular);
    }

    :deep(.el-collapse-item__wrap) {
      border: none;
      background: transparent;
    }

    .model-hint {
      color: var(--el-text-color-placeholder);
      font-size: 12px;
      line-height: 32px;
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

      .progress-stats {
        display: flex;
        justify-content: center;
        gap: 12px;
        margin-top: 12px;
      }
    }
  }

  .report-panel {
    margin-bottom: 24px;

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

    .stock-collapse-title {
      display: flex;
      align-items: center;
      gap: 10px;
      width: 100%;

      .rank-tag {
        min-width: 32px;
        text-align: center;
      }

      .stock-name {
        font-weight: 600;
        font-size: 15px;
      }

      .stock-code {
        color: var(--el-text-color-secondary);
        font-size: 13px;
      }
    }

    .stock-detail {
      padding: 12px 16px;

      .detail-section {
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

        ul {
          margin: 0;
          padding-left: 20px;

          li {
            line-height: 1.8;
            color: var(--el-text-color-regular);
          }
        }

        .risk-list li {
          color: var(--el-color-danger);
        }
      }

      .detail-meta {
        margin-top: 12px;
      }
    }
  }

  .candidates-panel {
    margin-bottom: 24px;

    .pagination-wrapper {
      display: flex;
      justify-content: center;
      margin-top: 16px;
    }
  }

  .batch-panel {
    margin-bottom: 24px;

    .filter-actions {
      display: flex;
      justify-content: center;
      gap: 16px;
      margin-top: 16px;
    }

    .batch-results {
      .batch-stats {
        display: flex;
        gap: 12px;
        margin-bottom: 12px;
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
