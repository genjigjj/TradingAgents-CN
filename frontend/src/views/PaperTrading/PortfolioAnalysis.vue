<template>
  <div class="portfolio-analysis">
    <!-- 顶部操作栏 -->
    <div class="action-bar">
      <div class="action-left">
        <el-button
          type="primary"
          :loading="store.isBatchAnalyzing"
          :disabled="store.isBatchAnalyzing"
          @click="handleBatchAnalyze"
        >
          <el-icon><DataAnalysis /></el-icon>
          批量分析全部持仓
        </el-button>
        <el-button
          type="success"
          :loading="quickAnalyzing"
          @click="showQuickAnalyzeDialog = true"
        >
          <el-icon><Search /></el-icon>
          快速分析
        </el-button>
        <el-select
          v-model="selectedModel"
          placeholder="自动选择模型（推荐）"
          clearable
          filterable
          style="width: 260px; margin-left: 12px"
        >
          <el-option-group
            v-for="group in availableModels"
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
      </div>
      <div class="action-right">
        <el-button :icon="Refresh" text @click="handleRefresh">刷新</el-button>
      </div>
    </div>

    <!-- 批量分析进度条 -->
    <el-card v-if="store.isBatchAnalyzing || store.currentTaskId" shadow="never" class="progress-card">
      <div class="progress-content">
        <span class="progress-label">{{ store.currentStep || '⏳ 任务排队中，等待处理...' }}</span>
        <el-progress
          :percentage="store.batchProgress"
          :striped="true"
          :striped-flow="true"
          :stroke-width="16"
          :duration="10"
        />
      </div>
    </el-card>

    <!-- 错误提示 -->
    <el-alert
      v-if="store.errorMessage"
      :title="store.errorMessage"
      type="error"
      show-icon
      closable
      style="margin-bottom: 16px"
      @close="store.errorMessage = null"
    />

    <!-- 分析结果卡片网格 -->
    <div v-if="store.hasAnalyses" class="analysis-grid">
      <el-card
        v-for="(item, index) in store.analyses"
        :key="item.position_code || index"
        shadow="hover"
        class="analysis-card"
        @click="openDetail(item)"
      >
        <!-- 卡片头部：股票代码 + 评级 -->
        <div class="card-top">
          <span class="stock-code">{{ item.position_code || '-' }}</span>
          <el-tag
            :type="getRatingTagType(item.rating)"
            :color="getRatingColor(item.rating)"
            effect="dark"
            size="small"
            class="rating-tag"
          >
            {{ item.rating }}
          </el-tag>
        </div>

        <!-- 信心度进度条 -->
        <div class="confidence-row">
          <span class="label">信心度</span>
          <el-progress
            :percentage="item.confidence"
            :stroke-width="10"
            :color="getConfidenceColor(item.confidence)"
            :format="(pct: number) => `${pct}%`"
            style="flex: 1; margin-left: 8px"
          />
        </div>

        <!-- 价格信息 -->
        <div class="price-info">
          <div class="price-row">
            <span class="label">当前价</span>
            <span class="value">{{ formatPrice(item.current_price) }}</span>
          </div>
          <div class="price-row">
            <span class="label">目标价</span>
            <span class="value target">{{ formatPrice(item.target_price) }}</span>
          </div>
        </div>

        <!-- 止盈止损 -->
        <div class="tp-sl-row">
          <el-tag type="success" size="small" effect="plain">
            止盈 {{ formatPrice(item.take_profit) }}
          </el-tag>
          <el-tag type="danger" size="small" effect="plain">
            止损 {{ formatPrice(item.stop_loss) }}
          </el-tag>
        </div>

        <!-- 分析摘要 -->
        <div class="summary">{{ truncateSummary(item.summary) }}</div>

        <!-- 卡片底部操作 -->
        <div class="card-actions">
          <el-button
            type="primary"
            size="small"
            link
            @click.stop="openDetail(item)"
          >
            查看详情
          </el-button>
          <el-button
            type="success"
            size="small"
            link
            :loading="syncingCode === item.position_code"
            @click.stop="handleSyncToMonitor(item)"
          >
            <el-icon><Connection /></el-icon>
            同步到监测
          </el-button>
        </div>
      </el-card>
    </div>

    <!-- 空状态 -->
    <el-empty
      v-if="!store.hasAnalyses && !store.loading"
      description="暂无分析结果，点击「批量分析全部持仓」开始 AI 分析"
      :image-size="160"
    />

    <!-- 加载状态 -->
    <div v-if="store.loading && !store.isBatchAnalyzing" v-loading="true" class="loading-placeholder" />

    <!-- 详情抽屉 -->
    <el-drawer
      v-model="drawerVisible"
      :title="drawerTitle"
      size="520px"
      direction="rtl"
    >
      <template v-if="selectedAnalysis">
        <!-- 评级与信心度 -->
        <div class="drawer-section">
          <h4>评级与信心度</h4>
          <div class="drawer-rating-row">
            <el-tag
              :type="getRatingTagType(selectedAnalysis.rating)"
              :color="getRatingColor(selectedAnalysis.rating)"
              effect="dark"
              size="large"
            >
              {{ selectedAnalysis.rating }}
            </el-tag>
            <el-progress
              :percentage="selectedAnalysis.confidence"
              :stroke-width="14"
              :color="getConfidenceColor(selectedAnalysis.confidence)"
              style="flex: 1; margin-left: 16px"
            />
          </div>
        </div>

        <!-- 价格信息 -->
        <div class="drawer-section">
          <h4>价格信息</h4>
          <el-descriptions :column="2" border size="small">
            <el-descriptions-item label="当前价">{{ formatPrice(selectedAnalysis.current_price) }}</el-descriptions-item>
            <el-descriptions-item label="目标价">{{ formatPrice(selectedAnalysis.target_price) }}</el-descriptions-item>
            <el-descriptions-item label="进场区间">
              {{ formatPrice(selectedAnalysis.entry_min ?? selectedAnalysis.entry_range?.min) }}
              -
              {{ formatPrice(selectedAnalysis.entry_max ?? selectedAnalysis.entry_range?.max) }}
            </el-descriptions-item>
            <el-descriptions-item label="市场">{{ selectedAnalysis.market || 'CN' }}</el-descriptions-item>
            <el-descriptions-item label="止盈位">
              <span style="color: #67C23A">{{ formatPrice(selectedAnalysis.take_profit) }}</span>
            </el-descriptions-item>
            <el-descriptions-item label="止损位">
              <span style="color: #F56C6C">{{ formatPrice(selectedAnalysis.stop_loss) }}</span>
            </el-descriptions-item>
          </el-descriptions>
        </div>

        <!-- 技术指标快照 -->
        <div v-if="selectedAnalysis.indicators_snapshot" class="drawer-section">
          <h4>技术指标快照</h4>
          <el-descriptions :column="2" border size="small">
            <el-descriptions-item label="MA5">{{ formatPrice(selectedAnalysis.indicators_snapshot.ma5) }}</el-descriptions-item>
            <el-descriptions-item label="MA20">{{ formatPrice(selectedAnalysis.indicators_snapshot.ma20) }}</el-descriptions-item>
            <el-descriptions-item label="MA60">{{ formatPrice(selectedAnalysis.indicators_snapshot.ma60) }}</el-descriptions-item>
            <el-descriptions-item label="RSI">{{ formatPrice(selectedAnalysis.indicators_snapshot.rsi) }}</el-descriptions-item>
          </el-descriptions>
        </div>

        <!-- 分析摘要 -->
        <div class="drawer-section">
          <h4>分析摘要</h4>
          <div class="drawer-summary markdown-body" v-html="renderMarkdown(selectedAnalysis.summary)"></div>
        </div>

        <!-- 完整分析报告 -->
        <div v-if="selectedAnalysis.full_report || selectedAnalysis.raw_response" class="drawer-section">
          <h4>完整分析报告</h4>
          <div class="drawer-raw-response markdown-body" v-html="renderMarkdown(selectedAnalysis.full_report || selectedAnalysis.raw_response)"></div>
        </div>

        <!-- 分析元信息 -->
        <div class="drawer-section">
          <h4>分析信息</h4>
          <el-descriptions :column="1" border size="small">
            <el-descriptions-item label="分析模型">{{ selectedAnalysis.model_name || '默认模型' }}</el-descriptions-item>
            <el-descriptions-item label="分析时间">{{ selectedAnalysis.analysis_time || selectedAnalysis.created_at || '-' }}</el-descriptions-item>
          </el-descriptions>
        </div>

        <!-- 抽屉底部操作 -->
        <div class="drawer-footer">
          <el-button
            type="success"
            :loading="syncingCode === selectedAnalysis.position_code"
            @click="handleSyncToMonitor(selectedAnalysis)"
          >
            <el-icon><Connection /></el-icon>
            同步到监测
          </el-button>
          <el-button @click="drawerVisible = false">关闭</el-button>
        </div>
      </template>
    </el-drawer>

    <!-- 快速分析对话框 -->
    <el-dialog
      v-model="showQuickAnalyzeDialog"
      title="快速实时分析"
      width="520px"
      :close-on-click-modal="false"
    >
      <el-form label-width="90px">
        <el-form-item label="股票代码" required>
          <el-input
            v-model="quickAnalyzeCodes"
            placeholder="输入股票代码，多只用逗号分隔（如 600519,000001,300750）"
            :rows="2"
            type="textarea"
          />
          <div style="font-size: 12px; color: #909399; margin-top: 4px">
            支持 1-10 只股票，逗号或空格分隔
          </div>
        </el-form-item>
        <el-form-item label="市场">
          <el-radio-group v-model="quickAnalyzeMarket">
            <el-radio-button label="CN">A股</el-radio-button>
            <el-radio-button label="HK">港股</el-radio-button>
            <el-radio-button label="US">美股</el-radio-button>
          </el-radio-group>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showQuickAnalyzeDialog = false">取消</el-button>
        <el-button type="primary" :loading="quickAnalyzing" @click="handleQuickAnalyze">
          开始分析
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { DataAnalysis, Refresh, Connection, Search } from '@element-plus/icons-vue'
import { marked } from 'marked'
import { usePortfolioStore } from '@/stores/portfolio'
import { portfolioApi, type AnalysisResult } from '@/api/portfolio'
import { configApi } from '@/api/config'

// 定义事件：同步成功后可跳转到实时监测 Tab
const emit = defineEmits<{
  (e: 'switch-tab', tabName: string): void
}>()

// ============================================================
// Store
// ============================================================
const store = usePortfolioStore()

// ============================================================
// 本地状态
// ============================================================

/** 选中的 AI 模型 */
const selectedModel = ref('')

/** 可用模型列表（按厂家分组） */
const availableModels = ref<Array<{
  provider: string
  provider_name: string
  models: Array<{ name: string; display_name: string }>
}>>([])

/** 详情抽屉是否可见 */
const drawerVisible = ref(false)

/** 当前选中的分析结果 */
const selectedAnalysis = ref<AnalysisResult | null>(null)

/** 正在同步到监测的股票代码 */
const syncingCode = ref<string | null>(null)

/** 快速分析相关状态 */
const showQuickAnalyzeDialog = ref(false)
const quickAnalyzeCodes = ref('')
const quickAnalyzeMarket = ref('CN')
const quickAnalyzing = ref(false)

// ============================================================
// 计算属性
// ============================================================

/** 抽屉标题 */
const drawerTitle = ref('分析详情')

// ============================================================
// 评级颜色映射
// ============================================================

/**
 * 根据评级返回对应的背景颜色
 * 强烈买入/买入=绿色，增持=浅绿，持有/观望=灰色，减持=橙色，卖出/强烈卖出=红色
 */
function getRatingColor(rating: string): string {
  if (!rating) return ''
  const r = rating.trim()
  if (r === '强烈买入' || r === '买入') return '#67C23A'
  if (r === '增持') return '#85CE61'
  if (r === '持有' || r === '观望') return '#909399'
  if (r === '减持') return '#E6A23C'
  if (r === '卖出' || r === '强烈卖出') return '#F56C6C'
  return ''
}

/**
 * 根据评级返回 el-tag 的 type
 */
function getRatingTagType(rating: string): 'success' | 'warning' | 'danger' | 'info' | 'primary' {
  if (!rating) return 'info'
  const r = rating.trim()
  if (r === '强烈买入' || r === '买入' || r === '增持') return 'success'
  if (r === '持有' || r === '观望') return 'info'
  if (r === '减持') return 'warning'
  if (r === '卖出' || r === '强烈卖出') return 'danger'
  return 'info'
}

/**
 * 根据信心度返回进度条颜色
 */
function getConfidenceColor(confidence: number): string {
  if (confidence >= 80) return '#67C23A'
  if (confidence >= 60) return '#409EFF'
  if (confidence >= 40) return '#E6A23C'
  return '#F56C6C'
}

// ============================================================
// 格式化工具
// ============================================================

// 配置 marked
marked.setOptions({ breaks: true, gfm: true })

function renderMarkdown(content: string | null | undefined): string {
  if (!content) return '<p>暂无内容</p>'
  try {
    return String(marked.parse(content))
  } catch (e) {
    console.error('Markdown 渲染失败:', e)
    return `<pre>${content}</pre>`
  }
}

function formatPrice(n: number | null | undefined): string {
  if (n == null || Number.isNaN(Number(n))) return '-'
  return Number(n).toFixed(2)
}

function truncateSummary(text: string | null | undefined, maxLen = 80): string {
  if (!text) return '暂无摘要'
  return text.length > maxLen ? text.slice(0, maxLen) + '...' : text
}

// ============================================================
// 操作方法
// ============================================================

/** 获取可用模型列表 */
async function fetchAvailableModels() {
  try {
    const models = await configApi.getAvailableModels()
    if (models) {
      availableModels.value = models
    }
  } catch {
    availableModels.value = []
  }
}

/** 刷新分析结果 */
async function handleRefresh() {
  await store.fetchLatestAnalyses()
  ElMessage.success('已刷新')
}

/** 批量分析 */
async function handleBatchAnalyze() {
  const params: { model_name?: string } = {}
  if (selectedModel.value) {
    params.model_name = selectedModel.value
  }
  const taskId = await store.analyzeBatch(params)
  if (taskId) {
    ElMessage.info('批量分析任务已提交，请等待完成')
  }
}

/** 打开详情抽屉 */
function openDetail(item: AnalysisResult) {
  selectedAnalysis.value = item
  drawerTitle.value = `${item.position_code || '未知'} 分析详情`
  drawerVisible.value = true
}

/** 同步到监测 */
async function handleSyncToMonitor(item: AnalysisResult) {
  if (!item.position_code) return
  syncingCode.value = item.position_code
  try {
    const res = await portfolioApi.syncToMonitor(item.position_code)
    if (res.success) {
      const actionText = res.data?.action === 'created' ? '已创建' : '已更新'
      // 使用 MessageBox 提示用户，提供跳转到实时监测的选项
      ElMessageBox.confirm(
        `${item.position_code} 监测配置${actionText}，是否跳转到实时监测查看？`,
        '同步成功',
        {
          confirmButtonText: '跳转到实时监测',
          cancelButtonText: '留在当前页',
          type: 'success',
        }
      ).then(() => {
        emit('switch-tab', 'monitor')
      }).catch(() => {
        // 用户选择留在当前页，不做任何操作
      })
    } else {
      ElMessage.error(res.message || '同步失败')
    }
  } catch (err: any) {
    ElMessage.error(err?.message || '同步到监测失败')
  } finally {
    syncingCode.value = null
  }
}

/** 快速分析 */
async function handleQuickAnalyze() {
  const input = quickAnalyzeCodes.value.trim()
  if (!input) {
    ElMessage.warning('请输入股票代码')
    return
  }

  // 解析代码列表（支持逗号、空格、换行分隔）
  const codes = input.split(/[,，\s\n]+/).filter(c => c.trim()).map(c => c.trim())
  if (codes.length === 0) {
    ElMessage.warning('请输入有效的股票代码')
    return
  }
  if (codes.length > 10) {
    ElMessage.warning('最多支持 10 只股票')
    return
  }

  quickAnalyzing.value = true
  showQuickAnalyzeDialog.value = false

  try {
    const params: { market?: string; model_name?: string } = {
      market: quickAnalyzeMarket.value,
    }
    if (selectedModel.value) {
      params.model_name = selectedModel.value
    }

    const res = await portfolioApi.quickAnalyze(codes, params)
    if (res.success && res.data) {
      const { results, success: successCount, failed } = res.data

      // 将成功的结果添加到本地列表
      for (const r of results) {
        if (r.success && r.data) {
          const idx = store.analyses.findIndex(a => a.position_code === r.code)
          if (idx >= 0) {
            store.analyses[idx] = r.data
          } else {
            store.analyses.push(r.data)
          }
        }
      }

      if (failed > 0) {
        const failedCodes = results.filter(r => !r.success).map(r => `${r.code}(${r.error})`).join(', ')
        ElMessage.warning(`分析完成: ${successCount} 成功, ${failed} 失败 [${failedCodes}]`)
      } else {
        ElMessage.success(`${successCount} 只股票分析完成`)
      }
    } else {
      ElMessage.error(res.message || '快速分析失败')
    }
  } catch (err: any) {
    ElMessage.error(err?.message || '快速分析请求失败')
  } finally {
    quickAnalyzing.value = false
    quickAnalyzeCodes.value = ''
  }
}

// ============================================================
// 生命周期
// ============================================================

onMounted(async () => {
  await Promise.all([
    store.fetchLatestAnalyses(),
    fetchAvailableModels(),
  ])
})
</script>

<style scoped>
.portfolio-analysis {
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

/* 进度卡片 */
.progress-card {
  margin-bottom: 16px;
}

.progress-content {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.progress-label {
  font-size: 13px;
  color: #606266;
}

/* 分析结果卡片网格 */
.analysis-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
  gap: 16px;
}

.analysis-card {
  cursor: pointer;
  transition: transform 0.2s, box-shadow 0.2s;
}

.analysis-card:hover {
  transform: translateY(-2px);
}

.card-top {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 12px;
}

.stock-code {
  font-size: 18px;
  font-weight: 600;
  color: #303133;
}

.rating-tag {
  border: none;
}

.confidence-row {
  display: flex;
  align-items: center;
  margin-bottom: 12px;
}

.confidence-row .label {
  font-size: 13px;
  color: #909399;
  white-space: nowrap;
}

.price-info {
  display: flex;
  gap: 24px;
  margin-bottom: 12px;
}

.price-row {
  display: flex;
  flex-direction: column;
}

.price-row .label {
  font-size: 12px;
  color: #909399;
}

.price-row .value {
  font-size: 16px;
  font-weight: 600;
  color: #303133;
}

.price-row .value.target {
  color: #409EFF;
}

.tp-sl-row {
  display: flex;
  gap: 8px;
  margin-bottom: 12px;
}

.summary {
  font-size: 13px;
  color: #606266;
  line-height: 1.6;
  margin-bottom: 12px;
  min-height: 42px;
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

/* 抽屉样式 */
.drawer-section {
  margin-bottom: 24px;
}

.drawer-section h4 {
  font-size: 14px;
  font-weight: 600;
  color: #303133;
  margin-bottom: 12px;
  padding-bottom: 8px;
  border-bottom: 1px solid #EBEEF5;
}

.drawer-rating-row {
  display: flex;
  align-items: center;
}

.drawer-summary {
  font-size: 14px;
  color: #606266;
  line-height: 1.8;
  white-space: pre-wrap;
}

.drawer-raw-response {
  font-size: 14px;
  color: #303133;
  line-height: 1.8;
  background: #F5F7FA;
  padding: 16px;
  border-radius: 6px;
  max-height: 500px;
  overflow-y: auto;
}

.drawer-summary {
  font-size: 14px;
  color: #606266;
  line-height: 1.8;
}

/* Markdown 渲染样式 */
.markdown-body h1,
.markdown-body h2,
.markdown-body h3,
.markdown-body h4 {
  margin-top: 16px;
  margin-bottom: 8px;
  font-weight: 600;
  color: #303133;
}

.markdown-body h1 { font-size: 20px; }
.markdown-body h2 { font-size: 17px; }
.markdown-body h3 { font-size: 15px; }
.markdown-body h4 { font-size: 14px; }

.markdown-body p {
  margin: 8px 0;
}

.markdown-body ul,
.markdown-body ol {
  margin: 8px 0;
  padding-left: 24px;
}

.markdown-body li {
  margin: 4px 0;
}

.markdown-body strong {
  font-weight: 600;
  color: #303133;
}

.markdown-body code {
  background: #E8EAED;
  padding: 2px 6px;
  border-radius: 3px;
  font-size: 12px;
}

.markdown-body blockquote {
  border-left: 4px solid #409EFF;
  padding-left: 12px;
  margin: 8px 0;
  color: #606266;
}

.markdown-body table {
  width: 100%;
  border-collapse: collapse;
  margin: 8px 0;
}

.markdown-body th,
.markdown-body td {
  border: 1px solid #EBEEF5;
  padding: 8px 12px;
  text-align: left;
}

.markdown-body th {
  background: #F5F7FA;
  font-weight: 600;
}

.drawer-footer {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 24px;
  padding-top: 16px;
  border-top: 1px solid #EBEEF5;
}
</style>
