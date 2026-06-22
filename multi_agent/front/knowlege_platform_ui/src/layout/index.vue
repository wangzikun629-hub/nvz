<template>
  <div class="app-wrapper">
    <!-- ── 侧边栏 ── -->
    <div class="sidebar">
      <div class="logo">
        <img src="/vazyme-mark.png" alt="Vazyme" />
        <div class="logo-text">
          <span class="logo-name">知识库</span>
          <span class="logo-sub">控制台</span>
        </div>
      </div>

      <el-menu
        :default-active="activeMenu"
        background-color="transparent"
        text-color="#64748b"
        active-text-color="#22c55e"
        router
        class="el-menu-vertical"
      >
        <el-menu-item index="/knowledge">
          <el-icon><Files /></el-icon>
          <span>知识库</span>
        </el-menu-item>
        <el-menu-item index="/kb-chat">
          <el-icon><ChatLineRound /></el-icon>
          <span>知识库问答</span>
        </el-menu-item>
      </el-menu>

      <div class="sidebar-footer">
        <!-- 用户信息行 -->
        <div class="user-row">
          <div class="user-avatar">{{ userInitial }}</div>
          <div class="user-info">
            <span class="user-name">{{ currentUser }}</span>
            <span class="user-role">内部用户</span>
          </div>
        </div>

        <!-- 操作按钮 -->
        <div class="footer-btns">
          <button class="footer-btn" @click="openSessionDialog">
            <el-icon><Monitor /></el-icon>
            会话管理
          </button>
          <button class="footer-btn logout" @click="handleLogout">
            <el-icon><SwitchButton /></el-icon>
            退出登录
          </button>
        </div>
      </div>
    </div>

    <!-- ── 内容区 ── -->
    <div class="main-container">
      <router-view v-slot="{ Component }">
        <transition name="fade-transform" mode="out-in">
          <component :is="Component" />
        </transition>
      </router-view>
    </div>

    <!-- ── 会话管理 Dialog ── -->
    <el-dialog v-model="sessionDialogVisible" title="登录会话管理" width="760px">
      <div class="session-toolbar">
        <span class="session-hint">当前账号的有效登录会话，可单独注销其他设备。</span>
        <el-button size="small" :loading="sessionsLoading" @click="loadSessions">刷新</el-button>
      </div>

      <el-table v-loading="sessionsLoading" :data="sessions" empty-text="暂无有效会话">
        <el-table-column label="会话" min-width="180">
          <template #default="{ row }">
            <div class="session-main">
              <span>{{ row.current ? '当前设备' : '其他设备' }}</span>
              <el-tag v-if="row.current" size="small" type="success">当前</el-tag>
            </div>
            <div class="session-id">{{ row.sessionId }}</div>
          </template>
        </el-table-column>
        <el-table-column prop="lastUsedAtLabel" label="最近活跃" width="180" />
        <el-table-column prop="expiresAtLabel" label="到期时间" width="180" />
        <el-table-column label="操作" width="120" align="right">
          <template #default="{ row }">
            <el-button
              text
              type="danger"
              :disabled="row.current || revokeLoadingId === row.sessionId"
              :loading="revokeLoadingId === row.sessionId"
              @click="handleRevokeSession(row)"
            >
              注销
            </el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { ChatLineRound, Files, Monitor, SwitchButton } from '@element-plus/icons-vue'
import { listSessions, logout, revokeSession } from '@/api/auth'

const route = useRoute()
const router = useRouter()
const activeMenu = computed(() => route.path)
const currentUser = computed(() => localStorage.getItem('kp_user') || 'User')
const userInitial = computed(() => (currentUser.value || 'U').charAt(0).toUpperCase())

const sessionDialogVisible = ref(false)
const sessionsLoading = ref(false)
const revokeLoadingId = ref('')
const sessions = ref([])

const clearLocalAuth = () => {
  localStorage.removeItem('kp_user')
  localStorage.removeItem('kp_user_id')
  localStorage.removeItem('kp_auth_token')
}

const formatDateTime = value => {
  if (!value) return '-'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return date.toLocaleString('zh-CN', { hour12: false })
}

const normalizeSessions = items =>
  (items || []).map(item => ({
    ...item,
    lastUsedAtLabel: formatDateTime(item.lastUsedAt),
    expiresAtLabel: formatDateTime(item.expiresAt)
  }))

const loadSessions = async () => {
  sessionsLoading.value = true
  try {
    const data = await listSessions()
    sessions.value = normalizeSessions(data?.items)
  } catch (error) {
    ElMessage.error(error.message || '加载会话失败')
  } finally {
    sessionsLoading.value = false
  }
}

const openSessionDialog = async () => {
  sessionDialogVisible.value = true
  await loadSessions()
}

const handleRevokeSession = async row => {
  try {
    await ElMessageBox.confirm(
      '注销后，该设备将需要重新登录才能继续使用。',
      '确认注销会话',
      { type: 'warning', confirmButtonText: '确认', cancelButtonText: '取消' }
    )
  } catch {
    return
  }

  revokeLoadingId.value = row.sessionId
  try {
    await revokeSession(row.sessionId)
    ElMessage.success('会话已注销')
    await loadSessions()
  } catch (error) {
    ElMessage.error(error.message || '注销会话失败')
  } finally {
    revokeLoadingId.value = ''
  }
}

const handleLogout = async () => {
  try {
    await logout()
  } catch (error) {
    console.error(error)
  } finally {
    clearLocalAuth()
  }
  router.replace('/login')
}
</script>

<style lang="scss" scoped>
.app-wrapper {
  display: flex;
  height: 100vh;
  width: 100%;
  background-color: #060d16;
  color: #c9d1d9;
}

/* ── 侧边栏 ── */
.sidebar {
  width: 220px;
  flex-shrink: 0;
  background: #0b1320;
  border-right: 1px solid #1a2638;
  display: flex;
  flex-direction: column;
  box-shadow: 2px 0 16px rgba(0, 0, 0, 0.4);
  z-index: 10;
}

.logo {
  height: 72px;
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 0 18px;
  border-bottom: 1px solid #1a2638;
  flex-shrink: 0;

  img {
    width: 36px;
    height: 36px;
    object-fit: contain;
    flex-shrink: 0;
  }
}

.logo-text {
  display: flex;
  flex-direction: column;
  line-height: 1;
}

.logo-name {
  font-size: 16px;
  font-weight: 700;
  color: #e2e8f0;
  letter-spacing: 0.02em;
}

.logo-sub {
  font-size: 10px;
  color: #475569;
  margin-top: 3px;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}

/* 菜单 */
.el-menu-vertical {
  border-right: none !important;
  flex: 1;
  padding: 10px 8px;
  background: transparent !important;

  :deep(.el-menu-item) {
    border-radius: 10px;
    margin-bottom: 4px;
    height: 44px;
    font-size: 13px;
    font-weight: 500;
    color: #64748b;
    transition: background 0.18s, color 0.18s;

    &:hover {
      background: rgba(255, 255, 255, 0.05) !important;
      color: #94a3b8 !important;
    }

    &.is-active {
      background: rgba(34, 197, 94, 0.12) !important;
      color: #22c55e !important;

      .el-icon { color: #22c55e; }
    }

    .el-icon {
      font-size: 16px;
      margin-right: 10px;
      color: inherit;
    }
  }
}

/* 底部用户区 */
.sidebar-footer {
  padding: 14px 12px;
  border-top: 1px solid #1a2638;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.user-row {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 8px;
  border-radius: 10px;
  background: rgba(255, 255, 255, 0.03);
}

.user-avatar {
  width: 32px;
  height: 32px;
  border-radius: 50%;
  background: linear-gradient(135deg, #22c55e, #059669);
  color: #fff;
  font-size: 13px;
  font-weight: 700;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}

.user-info {
  display: flex;
  flex-direction: column;
  min-width: 0;
}

.user-name {
  font-size: 12px;
  font-weight: 600;
  color: #cbd5e1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.user-role {
  font-size: 10px;
  color: #475569;
  margin-top: 1px;
}

.footer-btns {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.footer-btn {
  display: flex;
  align-items: center;
  gap: 7px;
  width: 100%;
  padding: 8px 10px;
  border-radius: 8px;
  border: none;
  background: transparent;
  color: #64748b;
  font-size: 12px;
  cursor: pointer;
  transition: background 0.15s, color 0.15s;
  text-align: left;

  .el-icon { font-size: 14px; }

  &:hover {
    background: rgba(255, 255, 255, 0.06);
    color: #94a3b8;
  }

  &.logout:hover {
    background: rgba(239, 68, 68, 0.1);
    color: #f87171;
  }
}

/* ── 内容区 ── */
.main-container {
  flex: 1;
  padding: 20px;
  overflow-y: auto;
  background-color: #060d16;
  background-image: radial-gradient(rgba(30, 45, 64, 0.5) 1px, transparent 1px);
  background-size: 28px 28px;
}

/* ── 会话 Dialog 内部样式 ── */
.session-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 14px;
}

.session-hint {
  color: #64748b;
  font-size: 13px;
}

.session-main {
  display: flex;
  align-items: center;
  gap: 8px;
  font-weight: 600;
  color: #e2e8f0;
}

.session-id {
  margin-top: 4px;
  color: #475569;
  font-size: 11px;
  word-break: break-all;
}

/* ── 页面切换动画 ── */
.fade-transform-leave-active,
.fade-transform-enter-active {
  transition: all 0.3s ease;
}

.fade-transform-enter-from {
  opacity: 0;
  transform: translateX(-16px);
}

.fade-transform-leave-to {
  opacity: 0;
  transform: translateX(16px);
}
</style>
