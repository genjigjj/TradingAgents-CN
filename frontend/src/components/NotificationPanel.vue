<template>
  <div class="notification-panel">
    <el-popover
      placement="bottom-end"
      :width="380"
      trigger="click"
      :visible="popoverVisible"
      @update:visible="popoverVisible = $event"
    >
      <template #reference>
        <el-badge :value="unreadCount" :hidden="unreadCount === 0" :max="99">
          <el-button :icon="Bell" circle text @click="handleOpen" />
        </el-badge>
      </template>

      <!-- 弹出面板内容 -->
      <div class="notif-popover">
        <!-- 面板头部 -->
        <div class="notif-header">
          <span class="notif-title">通知</span>
          <div class="notif-actions">
            <el-button
              v-if="unreadCount > 0"
              type="primary"
              link
              size="small"
              @click="handleMarkAllRead"
            >
              全部已读
            </el-button>
            <el-button
              v-if="notifications.length > 0"
              type="info"
              link
              size="small"
              @click="handleClearAll"
            >
              清空
            </el-button>
          </div>
        </div>

        <!-- 通知列表 -->
        <div class="notif-list" v-loading="loading">
          <div
            v-for="item in notifications"
            :key="item.id"
            class="notif-item"
            :class="{ 'notif-unread': item.status === 'unread' }"
            @click="handleClickNotification(item)"
          >
            <div class="notif-item-icon">
              <el-icon :size="18" :color="getSeverityColor(item)">
                <component :is="getSeverityIcon(item)" />
              </el-icon>
            </div>
            <div class="notif-item-body">
              <div class="notif-item-title">{{ item.title }}</div>
              <div v-if="item.content" class="notif-item-content">
                {{ truncate(item.content, 80) }}
              </div>
              <div class="notif-item-time">{{ formatRelativeTime(item.created_at) }}</div>
            </div>
            <div v-if="item.status === 'unread'" class="notif-dot" />
          </div>

          <!-- 空状态 -->
          <div v-if="notifications.length === 0 && !loading" class="notif-empty">
            <el-icon :size="40" color="#C0C4CC"><BellFilled /></el-icon>
            <span>暂无通知</span>
          </div>
        </div>
      </div>
    </el-popover>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, onUnmounted } from 'vue'
import { ElMessage } from 'element-plus'
import {
  Bell,
  BellFilled,
  SuccessFilled,
  WarningFilled,
  CircleCloseFilled,
  InfoFilled,
} from '@element-plus/icons-vue'
import { notificationsApi, type NotificationItem } from '@/api/notifications'
import { useAuthStore } from '@/stores/auth'

// ============================================================
// 状态
// ============================================================

const popoverVisible = ref(false)
const loading = ref(false)
const unreadCount = ref(0)
const notifications = ref<NotificationItem[]>([])

/** WebSocket 连接 */
let ws: WebSocket | null = null
let reconnectTimer: ReturnType<typeof setTimeout> | null = null
let pollTimer: ReturnType<typeof setInterval> | null = null

// ============================================================
// 工具方法
// ============================================================

function truncate(text: string, maxLen: number): string {
  if (!text) return ''
  return text.length > maxLen ? text.slice(0, maxLen) + '...' : text
}

function formatRelativeTime(isoStr: string): string {
  if (!isoStr) return ''
  try {
    const d = new Date(isoStr)
    const now = new Date()
    const diffMs = now.getTime() - d.getTime()
    const diffMin = Math.floor(diffMs / 60000)
    if (diffMin < 1) return '刚刚'
    if (diffMin < 60) return `${diffMin} 分钟前`
    const diffHour = Math.floor(diffMin / 60)
    if (diffHour < 24) return `${diffHour} 小时前`
    const diffDay = Math.floor(diffHour / 24)
    if (diffDay < 7) return `${diffDay} 天前`
    return d.toLocaleDateString('zh-CN', { month: '2-digit', day: '2-digit' })
  } catch {
    return isoStr
  }
}

function getSeverityColor(item: NotificationItem): string {
  // 根据 source 或 type 判断颜色
  if (item.source === 'smart_monitor') return '#E6A23C'
  if (item.source === 'stock_monitor') return '#F56C6C'
  if (item.type === 'alert') return '#E6A23C'
  if (item.type === 'system') return '#909399'
  return '#409EFF'
}

function getSeverityIcon(item: NotificationItem) {
  if (item.source === 'smart_monitor') return WarningFilled
  if (item.source === 'stock_monitor') return CircleCloseFilled
  if (item.type === 'alert') return WarningFilled
  if (item.type === 'system') return InfoFilled
  return SuccessFilled
}

// ============================================================
// 数据加载
// ============================================================

async function fetchUnreadCount() {
  try {
    const res = await notificationsApi.getUnreadCount()
    if (res.success) {
      unreadCount.value = res.data?.count ?? 0
    }
  } catch {
    // 静默失败
  }
}

async function fetchNotifications() {
  loading.value = true
  try {
    const res = await notificationsApi.getList({ page: 1, page_size: 30 })
    if (res.success) {
      notifications.value = res.data?.items ?? []
    }
  } catch {
    // 静默失败
  } finally {
    loading.value = false
  }
}

// ============================================================
// 操作方法
// ============================================================

function handleOpen() {
  popoverVisible.value = !popoverVisible.value
  if (popoverVisible.value) {
    fetchNotifications()
  }
}

async function handleClickNotification(item: NotificationItem) {
  if (item.status === 'unread') {
    try {
      await notificationsApi.markRead(item.id)
      item.status = 'read'
      unreadCount.value = Math.max(0, unreadCount.value - 1)
    } catch {
      // 静默失败
    }
  }
}

async function handleMarkAllRead() {
  try {
    await notificationsApi.markAllRead()
    notifications.value.forEach(n => { n.status = 'read' })
    unreadCount.value = 0
    ElMessage.success('已全部标记为已读')
  } catch {
    ElMessage.error('操作失败')
  }
}

async function handleClearAll() {
  // 标记全部已读并清空本地列表
  try {
    await notificationsApi.markAllRead()
    notifications.value = []
    unreadCount.value = 0
  } catch {
    ElMessage.error('操作失败')
  }
}

// ============================================================
// WebSocket 连接
// ============================================================

function connectWebSocket() {
  const authStore = useAuthStore()
  const token = authStore.token
  if (!token) return

  // 构建 WebSocket URL
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  const host = window.location.host
  const wsUrl = `${protocol}//${host}/api/ws/notifications?token=${encodeURIComponent(token)}`

  try {
    ws = new WebSocket(wsUrl)

    ws.onopen = () => {
      console.log('🔔 通知 WebSocket 已连接')
    }

    ws.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data)
        if (msg.type === 'notification') {
          // 收到新通知，更新未读数量
          unreadCount.value += 1
          // 如果面板打开，刷新列表
          if (popoverVisible.value) {
            fetchNotifications()
          }
        }
        // 心跳消息忽略
      } catch {
        // 解析失败忽略
      }
    }

    ws.onclose = () => {
      console.log('🔌 通知 WebSocket 已断开')
      ws = null
      // 断开后重连
      scheduleReconnect()
    }

    ws.onerror = () => {
      console.warn('⚠️ 通知 WebSocket 连接错误')
      ws?.close()
    }
  } catch (e) {
    console.warn('⚠️ 创建 WebSocket 连接失败:', e)
    scheduleReconnect()
  }
}

function scheduleReconnect() {
  if (reconnectTimer) return
  // 5 秒后重连
  reconnectTimer = setTimeout(() => {
    reconnectTimer = null
    const authStore = useAuthStore()
    if (authStore.isAuthenticated) {
      connectWebSocket()
      // 重连后拉取未读通知补偿
      fetchUnreadCount()
    }
  }, 5000)
}

function disconnectWebSocket() {
  if (reconnectTimer) {
    clearTimeout(reconnectTimer)
    reconnectTimer = null
  }
  if (ws) {
    ws.onclose = null // 防止触发重连
    ws.close()
    ws = null
  }
}

// ============================================================
// 生命周期
// ============================================================

onMounted(() => {
  fetchUnreadCount()
  connectWebSocket()

  // 定时轮询未读数量作为 WebSocket 的补充（每 60 秒）
  pollTimer = setInterval(fetchUnreadCount, 60000)
})

onUnmounted(() => {
  disconnectWebSocket()
  if (pollTimer) {
    clearInterval(pollTimer)
    pollTimer = null
  }
})
</script>

<style scoped>
.notification-panel {
  display: inline-flex;
  align-items: center;
}

.notif-popover {
  max-height: 480px;
  display: flex;
  flex-direction: column;
}

.notif-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding-bottom: 10px;
  border-bottom: 1px solid #EBEEF5;
  margin-bottom: 8px;
}

.notif-title {
  font-size: 15px;
  font-weight: 600;
  color: #303133;
}

.notif-actions {
  display: flex;
  gap: 4px;
}

.notif-list {
  max-height: 400px;
  overflow-y: auto;
  min-height: 100px;
}

.notif-item {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  padding: 10px 4px;
  border-bottom: 1px solid #F2F6FC;
  cursor: pointer;
  transition: background 0.15s;
  position: relative;
}

.notif-item:hover {
  background: #F5F7FA;
}

.notif-item:last-child {
  border-bottom: none;
}

.notif-unread {
  background: #ECF5FF;
}

.notif-unread:hover {
  background: #D9ECFF;
}

.notif-item-icon {
  flex-shrink: 0;
  margin-top: 2px;
}

.notif-item-body {
  flex: 1;
  min-width: 0;
}

.notif-item-title {
  font-size: 13px;
  font-weight: 500;
  color: #303133;
  line-height: 1.4;
  word-break: break-all;
}

.notif-item-content {
  font-size: 12px;
  color: #909399;
  line-height: 1.5;
  margin-top: 4px;
  word-break: break-all;
}

.notif-item-time {
  font-size: 11px;
  color: #C0C4CC;
  margin-top: 4px;
}

.notif-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #409EFF;
  flex-shrink: 0;
  margin-top: 6px;
}

.notif-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 32px 0;
  gap: 8px;
  color: #C0C4CC;
  font-size: 13px;
}
</style>
