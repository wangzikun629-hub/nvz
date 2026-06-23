<template>
  <div class="admin-layout">
    <!-- 侧边栏 -->
    <aside class="sidebar">
      <div class="brand">
        <svg viewBox="0 0 32 32" fill="none" xmlns="http://www.w3.org/2000/svg">
          <circle cx="16" cy="16" r="16" fill="#4f6ef7"/>
          <path d="M10 16l4 4 8-8" stroke="#fff" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
        </svg>
        <span>管理后台</span>
      </div>
      <nav>
        <a class="nav-item active">
          <svg viewBox="0 0 20 20" fill="currentColor"><path d="M3 4a1 1 0 011-1h12a1 1 0 011 1v2a1 1 0 01-1 1H4a1 1 0 01-1-1V4zm0 6a1 1 0 011-1h12a1 1 0 011 1v2a1 1 0 01-1 1H4a1 1 0 01-1-1v-2zm0 6a1 1 0 011-1h12a1 1 0 011 1v2a1 1 0 01-1 1H4a1 1 0 01-1-1v-2z"/></svg>
          成员管理
        </a>
      </nav>
      <div class="sidebar-footer">
        <button @click="handleLogout" class="logout-btn">退出登录</button>
      </div>
    </aside>

    <!-- 主内容 -->
    <main class="main-content">
      <!-- 页头 -->
      <header class="page-header">
        <h1>成员管理</h1>
        <button class="btn-refresh" @click="loadAll" :disabled="loadingStats || loadingUsers">
          <svg viewBox="0 0 20 20" fill="currentColor"><path fill-rule="evenodd" d="M4 2a1 1 0 011 1v2.101a7.002 7.002 0 0111.601 2.566 1 1 0 11-1.885.666A5.002 5.002 0 005.999 7H9a1 1 0 010 2H4a1 1 0 01-1-1V3a1 1 0 011-1zm.008 9.057a1 1 0 011.276.61A5.002 5.002 0 0014.001 13H11a1 1 0 110-2h5a1 1 0 011 1v5a1 1 0 11-2 0v-2.101a7.002 7.002 0 01-11.601-2.566 1 1 0 01.61-1.276z" clip-rule="evenodd"/></svg>
          刷新
        </button>
      </header>

      <!-- 统计卡片 -->
      <section class="stats-row">
        <div class="stat-card">
          <div class="stat-icon purple">
            <svg viewBox="0 0 20 20" fill="currentColor"><path d="M13 6a3 3 0 11-6 0 3 3 0 016 0zm5 2a2 2 0 11-4 0 2 2 0 014 0zM2 8a2 2 0 114 0 2 2 0 01-4 0zm8 6c-2.761 0-5-1.343-5-3v-.5c0-.277.045-.545.127-.8C6.058 11.26 7.917 12 10 12s3.942-.74 4.873-1.8c.082.255.127.523.127.8V17c0 1.657-2.239 3-5 3z"/></svg>
          </div>
          <div class="stat-body">
            <div class="stat-value">{{ loadingStats ? '…' : stats.total_users }}</div>
            <div class="stat-label">注册成员总数</div>
          </div>
        </div>
        <div class="stat-card">
          <div class="stat-icon green">
            <svg viewBox="0 0 20 20" fill="currentColor"><circle cx="10" cy="10" r="3"/><path d="M10 2a8 8 0 100 16A8 8 0 0010 2zm0 14a6 6 0 110-12 6 6 0 010 12z"/></svg>
          </div>
          <div class="stat-body">
            <div class="stat-value">{{ loadingStats ? '…' : stats.active_sessions }}</div>
            <div class="stat-label">当前在线会话数</div>
          </div>
        </div>
      </section>

      <!-- 成员列表 -->
      <section class="table-section">
        <div v-if="loadingUsers" class="loading-tip">加载中…</div>
        <div v-else-if="usersError" class="error-tip">{{ usersError }}</div>
        <table v-else class="user-table">
          <thead>
            <tr>
              <th>ID</th>
              <th>用户名</th>
              <th>注册时间</th>
              <th>在线会话</th>
              <th>对话数</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            <template v-for="user in users" :key="user.id">
              <!-- 用户行 -->
              <tr class="user-row" :class="{ expanded: expandedUserId === user.id }">
                <td class="td-id">{{ user.id }}</td>
                <td class="td-username">
                  <span class="avatar">{{ user.username.charAt(0).toUpperCase() }}</span>
                  {{ user.username }}
                </td>
                <td>{{ formatDate(user.created_at) }}</td>
                <td>
                  <span :class="['badge', user.active_sessions > 0 ? 'badge-green' : 'badge-gray']">
                    {{ user.active_sessions > 0 ? `在线 ${user.active_sessions}` : '离线' }}
                  </span>
                </td>
                <td>{{ user.conversation_count }}</td>
                <td class="td-actions">
                  <button class="btn-text" @click="toggleConversations(user)">
                    {{ expandedUserId === user.id ? '收起对话' : '查看对话' }}
                  </button>
                  <button class="btn-text accent" @click="openResetModal(user)">重置密码</button>
                </td>
              </tr>
              <!-- 对话展开行 -->
              <tr v-if="expandedUserId === user.id" class="conv-row">
                <td colspan="6">
                  <div v-if="convLoading" class="conv-loading">加载对话中…</div>
                  <div v-else-if="conversations.length === 0" class="conv-empty">暂无对话记录</div>
                  <ul v-else class="conv-list">
                    <li v-for="conv in conversations" :key="conv.session_id" class="conv-item">
                      <div class="conv-title">{{ conv.title }}</div>
                      <div class="conv-meta">
                        {{ conv.total_messages }} 条消息 · 最后活跃 {{ formatDate(conv.updated_at) }}
                      </div>
                    </li>
                  </ul>
                </td>
              </tr>
            </template>
          </tbody>
        </table>
      </section>
    </main>

    <!-- 重置密码弹窗 -->
    <div v-if="resetModal.visible" class="modal-overlay" @click.self="resetModal.visible = false">
      <div class="modal">
        <h3>重置密码</h3>
        <p class="modal-sub">为用户 <strong>{{ resetModal.user?.username }}</strong> 设置新密码</p>
        <div class="field">
          <label>新密码（≥6位）</label>
          <input v-model="resetModal.password" type="password" placeholder="输入新密码" @keyup.enter="confirmReset"/>
        </div>
        <div class="field">
          <label>确认密码</label>
          <input v-model="resetModal.confirm" type="password" placeholder="再次输入" @keyup.enter="confirmReset"/>
        </div>
        <p v-if="resetModal.error" class="error-msg">{{ resetModal.error }}</p>
        <div class="modal-actions">
          <button class="btn-cancel" @click="resetModal.visible = false">取消</button>
          <button class="btn-primary" :disabled="resetModal.loading" @click="confirmReset">
            {{ resetModal.loading ? '保存中…' : '确认重置' }}
          </button>
        </div>
      </div>
    </div>

    <!-- Toast 提示 -->
    <transition name="toast">
      <div v-if="toast.visible" class="toast" :class="toast.type">{{ toast.message }}</div>
    </transition>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { adminApi } from '@/api/admin.js'

const router = useRouter()

// ── 数据状态 ─────────────────────────────────────────────────────────────
const stats = ref({ total_users: 0, active_sessions: 0 })
const loadingStats = ref(false)
const users = ref([])
const loadingUsers = ref(false)
const usersError = ref('')

// 对话展开
const expandedUserId = ref(null)
const conversations = ref([])
const convLoading = ref(false)

// 重置密码弹窗
const resetModal = reactive({
  visible: false,
  user: null,
  password: '',
  confirm: '',
  loading: false,
  error: '',
})

// Toast
const toast = reactive({ visible: false, message: '', type: 'success' })

// ── 初始化 ────────────────────────────────────────────────────────────────
onMounted(() => loadAll())

async function loadAll() {
  loadStats()
  loadUsers()
}

async function loadStats() {
  loadingStats.value = true
  try {
    const data = await adminApi.getStats()
    stats.value = { total_users: data.total_users, active_sessions: data.active_sessions }
  } catch (e) {
    showToast(e.message, 'error')
  } finally {
    loadingStats.value = false
  }
}

async function loadUsers() {
  loadingUsers.value = true
  usersError.value = ''
  try {
    const data = await adminApi.listUsers()
    users.value = data.items
  } catch (e) {
    usersError.value = e.message
  } finally {
    loadingUsers.value = false
  }
}

// ── 对话展开 ──────────────────────────────────────────────────────────────
async function toggleConversations(user) {
  if (expandedUserId.value === user.id) {
    expandedUserId.value = null
    conversations.value = []
    return
  }
  expandedUserId.value = user.id
  conversations.value = []
  convLoading.value = true
  try {
    const data = await adminApi.getUserConversations(user.id)
    conversations.value = data.items
  } catch (e) {
    showToast(e.message, 'error')
  } finally {
    convLoading.value = false
  }
}

// ── 重置密码 ──────────────────────────────────────────────────────────────
function openResetModal(user) {
  Object.assign(resetModal, { visible: true, user, password: '', confirm: '', loading: false, error: '' })
}

async function confirmReset() {
  resetModal.error = ''
  if (resetModal.password.length < 6) {
    resetModal.error = '密码不能少于 6 位'
    return
  }
  if (resetModal.password !== resetModal.confirm) {
    resetModal.error = '两次输入的密码不一致'
    return
  }
  resetModal.loading = true
  try {
    await adminApi.resetPassword(resetModal.user.id, resetModal.password)
    resetModal.visible = false
    showToast(`已重置用户 ${resetModal.user.username} 的密码`, 'success')
  } catch (e) {
    resetModal.error = e.message
  } finally {
    resetModal.loading = false
  }
}

// ── 工具 ─────────────────────────────────────────────────────────────────
function formatDate(str) {
  if (!str) return '-'
  const d = new Date(str)
  if (isNaN(d)) return str
  return d.toLocaleString('zh-CN', { hour12: false }).replace(/\//g, '-')
}

function showToast(message, type = 'success') {
  toast.message = message
  toast.type = type
  toast.visible = true
  setTimeout(() => { toast.visible = false }, 3000)
}

function handleLogout() {
  localStorage.removeItem('adminToken')
  router.push('/admin/login')
}
</script>

<style scoped>
/* ── 布局 ─────────────────────────────────────────────────────────────────── */
.admin-layout {
  display: flex;
  min-height: 100vh;
  background: #f5f7fb;
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
  color: #1a1a2e;
}

/* ── 侧边栏 ──────────────────────────────────────────────────────────────── */
.sidebar {
  width: 220px;
  background: #fff;
  border-right: 1px solid #eef0f6;
  display: flex;
  flex-direction: column;
  padding: 24px 0;
  position: fixed;
  top: 0;
  bottom: 0;
  left: 0;
}
.brand {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 0 24px 28px;
  font-weight: 700;
  font-size: 16px;
  color: #1a1a2e;
}
.brand svg { width: 32px; height: 32px; }
.nav-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 11px 24px;
  font-size: 14px;
  font-weight: 500;
  color: #6b7280;
  cursor: pointer;
  text-decoration: none;
  transition: all .15s;
}
.nav-item svg { width: 18px; height: 18px; }
.nav-item:hover, .nav-item.active {
  color: #4f6ef7;
  background: #f0f3ff;
  border-right: 3px solid #4f6ef7;
}
.sidebar-footer {
  margin-top: auto;
  padding: 16px 24px;
}
.logout-btn {
  width: 100%;
  padding: 9px;
  border: 1.5px solid #e2e8f0;
  border-radius: 8px;
  background: #fff;
  color: #6b7280;
  font-size: 13px;
  cursor: pointer;
  transition: all .15s;
}
.logout-btn:hover {
  border-color: #e53e3e;
  color: #e53e3e;
}

/* ── 主内容 ──────────────────────────────────────────────────────────────── */
.main-content {
  flex: 1;
  margin-left: 220px;
  padding: 32px;
  max-width: 1100px;
}
.page-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 28px;
}
.page-header h1 {
  font-size: 22px;
  font-weight: 700;
  margin: 0;
}
.btn-refresh {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 8px 16px;
  border: 1.5px solid #e2e8f0;
  border-radius: 8px;
  background: #fff;
  color: #4a5568;
  font-size: 13px;
  cursor: pointer;
  transition: all .15s;
}
.btn-refresh svg { width: 15px; height: 15px; }
.btn-refresh:hover:not(:disabled) { border-color: #4f6ef7; color: #4f6ef7; }
.btn-refresh:disabled { opacity: .5; cursor: not-allowed; }

/* ── 统计卡片 ────────────────────────────────────────────────────────────── */
.stats-row {
  display: flex;
  gap: 20px;
  margin-bottom: 28px;
}
.stat-card {
  flex: 1;
  background: #fff;
  border-radius: 12px;
  padding: 20px 24px;
  display: flex;
  align-items: center;
  gap: 18px;
  box-shadow: 0 1px 4px rgba(0,0,0,.06);
}
.stat-icon {
  width: 48px;
  height: 48px;
  border-radius: 12px;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}
.stat-icon svg { width: 22px; height: 22px; color: #fff; }
.stat-icon.purple { background: linear-gradient(135deg, #667eea, #764ba2); }
.stat-icon.green  { background: linear-gradient(135deg, #43e97b, #38f9d7); }
.stat-value {
  font-size: 30px;
  font-weight: 800;
  color: #1a1a2e;
  line-height: 1;
}
.stat-label {
  font-size: 13px;
  color: #8b93a7;
  margin-top: 4px;
}

/* ── 表格 ────────────────────────────────────────────────────────────────── */
.table-section {
  background: #fff;
  border-radius: 12px;
  box-shadow: 0 1px 4px rgba(0,0,0,.06);
  overflow: hidden;
}
.user-table {
  width: 100%;
  border-collapse: collapse;
}
.user-table thead th {
  background: #f8f9fd;
  padding: 13px 16px;
  text-align: left;
  font-size: 12px;
  font-weight: 600;
  color: #8b93a7;
  text-transform: uppercase;
  letter-spacing: .03em;
  border-bottom: 1px solid #eef0f6;
}
.user-table tbody td {
  padding: 14px 16px;
  font-size: 14px;
  border-bottom: 1px solid #f3f4f8;
  vertical-align: middle;
}
.user-row:hover { background: #fafbff; }
.user-row.expanded { background: #f7f8ff; }

.td-id { color: #9ca3af; font-size: 13px; }
.td-username {
  display: flex;
  align-items: center;
  gap: 10px;
  font-weight: 600;
}
.avatar {
  width: 30px;
  height: 30px;
  border-radius: 50%;
  background: linear-gradient(135deg, #667eea, #764ba2);
  color: #fff;
  font-size: 13px;
  font-weight: 700;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}
.badge {
  display: inline-block;
  padding: 3px 10px;
  border-radius: 20px;
  font-size: 12px;
  font-weight: 600;
}
.badge-green { background: #dcfce7; color: #16a34a; }
.badge-gray  { background: #f1f5f9; color: #94a3b8; }

.td-actions { display: flex; gap: 8px; }
.btn-text {
  background: none;
  border: none;
  font-size: 13px;
  color: #6b7280;
  cursor: pointer;
  padding: 4px 8px;
  border-radius: 6px;
  transition: all .15s;
}
.btn-text:hover { background: #f0f3ff; color: #4f6ef7; }
.btn-text.accent { color: #4f6ef7; }
.btn-text.accent:hover { background: #f0f3ff; }

/* ── 对话展开 ────────────────────────────────────────────────────────────── */
.conv-row td {
  background: #f7f8ff;
  padding: 16px 24px !important;
}
.conv-loading, .conv-empty {
  font-size: 13px;
  color: #9ca3af;
  text-align: center;
  padding: 12px 0;
}
.conv-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.conv-item {
  background: #fff;
  border: 1px solid #e8ecf9;
  border-radius: 8px;
  padding: 12px 16px;
}
.conv-title {
  font-size: 13px;
  font-weight: 600;
  color: #1a1a2e;
  margin-bottom: 4px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.conv-meta {
  font-size: 12px;
  color: #9ca3af;
}

/* ── 加载/错误 ───────────────────────────────────────────────────────────── */
.loading-tip, .error-tip {
  padding: 40px;
  text-align: center;
  font-size: 14px;
  color: #9ca3af;
}
.error-tip { color: #e53e3e; }

/* ── 弹窗 ────────────────────────────────────────────────────────────────── */
.modal-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0,0,0,.35);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 999;
}
.modal {
  background: #fff;
  border-radius: 14px;
  padding: 32px;
  width: 380px;
  box-shadow: 0 20px 60px rgba(0,0,0,.18);
}
.modal h3 { margin: 0 0 6px; font-size: 18px; font-weight: 700; }
.modal-sub { font-size: 13px; color: #6b7280; margin: 0 0 20px; }
.field { margin-bottom: 14px; }
.field label { display: block; font-size: 13px; font-weight: 600; color: #4a5568; margin-bottom: 6px; }
.field input {
  width: 100%;
  padding: 10px 14px;
  border: 1.5px solid #e2e8f0;
  border-radius: 8px;
  font-size: 14px;
  outline: none;
  box-sizing: border-box;
}
.field input:focus { border-color: #4f6ef7; }
.error-msg { font-size: 13px; color: #e53e3e; margin: 0 0 12px; }
.modal-actions { display: flex; gap: 10px; justify-content: flex-end; margin-top: 4px; }
.btn-cancel {
  padding: 10px 20px;
  border: 1.5px solid #e2e8f0;
  border-radius: 8px;
  background: #fff;
  font-size: 14px;
  cursor: pointer;
}
.btn-primary {
  padding: 10px 20px;
  background: #4f6ef7;
  color: #fff;
  border: none;
  border-radius: 8px;
  font-size: 14px;
  font-weight: 600;
  cursor: pointer;
}
.btn-primary:hover:not(:disabled) { background: #3a57e8; }
.btn-primary:disabled { opacity: .6; cursor: not-allowed; }

/* ── Toast ───────────────────────────────────────────────────────────────── */
.toast {
  position: fixed;
  bottom: 32px;
  left: 50%;
  transform: translateX(-50%);
  padding: 12px 24px;
  border-radius: 10px;
  font-size: 14px;
  font-weight: 500;
  z-index: 1000;
  box-shadow: 0 4px 20px rgba(0,0,0,.15);
}
.toast.success { background: #1e293b; color: #fff; }
.toast.error   { background: #fee2e2; color: #dc2626; }
.toast-enter-active, .toast-leave-active { transition: all .3s ease; }
.toast-enter-from, .toast-leave-to { opacity: 0; transform: translateX(-50%) translateY(10px); }
</style>
