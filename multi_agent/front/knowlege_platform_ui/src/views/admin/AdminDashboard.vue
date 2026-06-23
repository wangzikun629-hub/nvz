<template>
  <div class="admin-dashboard">

    <!-- 首次进入需要输入管理员令牌 -->
    <el-dialog
      v-model="tokenDialogVisible"
      title="管理员验证"
      width="400px"
      :close-on-click-modal="false"
      :show-close="false"
    >
      <p class="token-hint">请输入 APP_API_KEY 以访问管理后台</p>
      <el-input
        v-model="tokenInput"
        type="password"
        placeholder="管理员令牌"
        show-password
        @keyup.enter="saveToken"
      />
      <template #footer>
        <el-button type="primary" :loading="tokenChecking" @click="saveToken">确认</el-button>
      </template>
    </el-dialog>

    <!-- 主内容（令牌验证通过后显示） -->
    <template v-if="!tokenDialogVisible">

      <!-- 页头 -->
      <div class="page-header">
        <div>
          <h2 class="page-title">成员管理</h2>
          <p class="page-sub">共 {{ stats.total_users }} 名成员，当前在线会话 {{ stats.active_sessions }} 个</p>
        </div>
        <el-button :loading="loading" @click="loadAll" :icon="Refresh" circle />
      </div>

      <!-- 统计卡片 -->
      <div class="stat-row">
        <div class="stat-card">
          <div class="stat-icon purple">
            <el-icon :size="22"><User /></el-icon>
          </div>
          <div>
            <div class="stat-num">{{ stats.total_users }}</div>
            <div class="stat-label">注册成员总数</div>
          </div>
        </div>
        <div class="stat-card">
          <div class="stat-icon green">
            <el-icon :size="22"><Connection /></el-icon>
          </div>
          <div>
            <div class="stat-num">{{ stats.active_sessions }}</div>
            <div class="stat-label">当前在线会话</div>
          </div>
        </div>
      </div>

      <!-- 成员表格 -->
      <div class="table-card">
        <el-table
          v-loading="loading"
          :data="users"
          row-key="id"
          style="width: 100%"
          @expand-change="handleExpand"
        >
          <!-- 展开列 -->
          <el-table-column type="expand">
            <template #default="{ row }">
              <div class="expand-wrap">
                <div v-if="convLoading[row.id]" class="conv-loading">
                  <el-icon class="is-loading"><Loading /></el-icon> 加载对话中…
                </div>
                <div v-else-if="!conversations[row.id]?.length" class="conv-empty">
                  暂无对话记录
                </div>
                <div v-else class="conv-grid">
                  <div
                    v-for="conv in conversations[row.id]"
                    :key="conv.session_id"
                    class="conv-item"
                  >
                    <div class="conv-title">{{ conv.title || '（无内容）' }}</div>
                    <div class="conv-meta">
                      <el-tag size="small" type="info">{{ conv.total_messages }} 条消息</el-tag>
                      <span>{{ formatDate(conv.updated_at) }}</span>
                    </div>
                  </div>
                </div>
              </div>
            </template>
          </el-table-column>

          <el-table-column label="ID" prop="id" width="70" />

          <el-table-column label="用户名" min-width="140">
            <template #default="{ row }">
              <div class="username-cell">
                <div class="avatar">{{ row.username.charAt(0).toUpperCase() }}</div>
                <span>{{ row.username }}</span>
              </div>
            </template>
          </el-table-column>

          <el-table-column label="注册时间" width="180">
            <template #default="{ row }">{{ formatDate(row.created_at) }}</template>
          </el-table-column>

          <el-table-column label="在线状态" width="120">
            <template #default="{ row }">
              <el-tag
                :type="row.active_sessions > 0 ? 'success' : 'info'"
                size="small"
              >
                {{ row.active_sessions > 0 ? `在线 ${row.active_sessions}` : '离线' }}
              </el-tag>
            </template>
          </el-table-column>

          <el-table-column label="对话数" width="90" align="center">
            <template #default="{ row }">
              <span class="conv-count">{{ row.conversation_count }}</span>
            </template>
          </el-table-column>

          <el-table-column label="操作" width="180" align="right">
            <template #default="{ row }">
              <el-button text type="primary" size="small" @click="openReset(row)">
                重置密码
              </el-button>
              <el-button text type="danger" size="small" @click="handleDelete(row)">
                删除
              </el-button>
            </template>
          </el-table-column>
        </el-table>
      </div>

    </template>

    <!-- 重置密码弹窗 -->
    <el-dialog v-model="resetDialog.visible" title="重置密码" width="380px">
      <p class="token-hint">
        为 <strong>{{ resetDialog.user?.username }}</strong> 设置新密码
      </p>
      <el-form :model="resetDialog" label-width="80px" style="margin-top: 8px">
        <el-form-item label="新密码">
          <el-input
            v-model="resetDialog.password"
            type="password"
            show-password
            placeholder="至少 6 位"
          />
        </el-form-item>
        <el-form-item label="确认密码">
          <el-input
            v-model="resetDialog.confirm"
            type="password"
            show-password
            placeholder="再次输入"
            @keyup.enter="confirmReset"
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="resetDialog.visible = false">取消</el-button>
        <el-button type="primary" :loading="resetDialog.loading" @click="confirmReset">
          确认重置
        </el-button>
      </template>
    </el-dialog>

  </div>
</template>

<script setup>
import { ref, reactive, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Refresh, User, Connection, Loading } from '@element-plus/icons-vue'
import { adminApi } from '@/api/admin.js'

// ── 令牌验证 ────────────────────────────────────────────────────────────────
const tokenDialogVisible = ref(false)
const tokenInput = ref('')
const tokenChecking = ref(false)

function checkToken() {
  const saved = localStorage.getItem('kp_admin_token') || ''
  if (!saved) tokenDialogVisible.value = true
}

async function saveToken() {
  const t = tokenInput.value.trim()
  if (!t) { ElMessage.warning('请输入令牌'); return }
  tokenChecking.value = true
  localStorage.setItem('kp_admin_token', t)
  try {
    await adminApi.getStats()
    tokenDialogVisible.value = false
    loadAll()
  } catch {
    localStorage.removeItem('kp_admin_token')
    ElMessage.error('令牌错误，请重试')
  } finally {
    tokenChecking.value = false
  }
}

// ── 数据 ────────────────────────────────────────────────────────────────────
const loading = ref(false)
const stats = ref({ total_users: 0, active_sessions: 0 })
const users = ref([])

// 对话展开
const conversations = reactive({})
const convLoading = reactive({})

// ── 初始化 ───────────────────────────────────────────────────────────────────
onMounted(() => {
  checkToken()
  if (localStorage.getItem('kp_admin_token')) loadAll()
})

async function loadAll() {
  // 清空对话缓存，防止刷新后展示过期数据
  Object.keys(conversations).forEach(k => delete conversations[k])
  Object.keys(convLoading).forEach(k => delete convLoading[k])
  loading.value = true
  try {
    const [statsRes, usersRes] = await Promise.all([
      adminApi.getStats(),
      adminApi.listUsers(),
    ])
    stats.value = { total_users: statsRes.total_users, active_sessions: statsRes.active_sessions }
    users.value = usersRes.items
  } catch (e) {
    ElMessage.error(e.message || '加载失败')
  } finally {
    loading.value = false
  }
}

// ── 对话展开 ─────────────────────────────────────────────────────────────────
// expanded 是当前所有已展开行的数组，需判断 row 是否在其中来区分展开/收起
async function handleExpand(row, expanded) {
  const isExpanding = expanded.some(r => r.id === row.id)
  if (!isExpanding) return  // 收起行时不请求
  const uid = row.id
  if (conversations[uid]) return  // 已加载过，直接复用缓存
  convLoading[uid] = true
  try {
    const data = await adminApi.getUserConversations(uid)
    conversations[uid] = data.items
  } catch (e) {
    ElMessage.error(e.message || '加载对话失败')
    conversations[uid] = []
  } finally {
    convLoading[uid] = false
  }
}

// ── 重置密码 ──────────────────────────────────────────────────────────────────
const resetDialog = reactive({
  visible: false,
  user: null,
  password: '',
  confirm: '',
  loading: false,
})

function openReset(user) {
  Object.assign(resetDialog, { visible: true, user, password: '', confirm: '', loading: false })
}

async function confirmReset() {
  if (resetDialog.password.length < 6) { ElMessage.warning('密码不能少于 6 位'); return }
  if (resetDialog.password !== resetDialog.confirm) { ElMessage.warning('两次密码不一致'); return }
  resetDialog.loading = true
  try {
    await adminApi.resetPassword(resetDialog.user.id, resetDialog.password)
    resetDialog.visible = false
    ElMessage.success(`已重置 ${resetDialog.user.username} 的密码`)
  } catch (e) {
    ElMessage.error(e.message || '重置失败')
  } finally {
    resetDialog.loading = false
  }
}

// ── 删除成员 ──────────────────────────────────────────────────────────────────
async function handleDelete(row) {
  try {
    await ElMessageBox.confirm(
      `确定删除用户 "${row.username}"？此操作不可恢复。`,
      '删除确认',
      { type: 'warning', confirmButtonText: '确认删除', cancelButtonText: '取消' }
    )
  } catch { return }
  try {
    await adminApi.deleteUser(row.id)
    ElMessage.success('已删除')
    loadAll()
  } catch (e) {
    ElMessage.error(e.message || '删除失败')
  }
}

// ── 工具 ──────────────────────────────────────────────────────────────────────
function formatDate(str) {
  if (!str) return '-'
  const d = new Date(str)
  if (isNaN(d)) return str
  return d.toLocaleString('zh-CN', { hour12: false })
}
</script>

<style lang="scss" scoped>
.admin-dashboard {
  padding: 0;
  color: #c9d1d9;
  min-height: 100%;
}

.page-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  margin-bottom: 24px;
}

.page-title {
  font-size: 20px;
  font-weight: 700;
  color: #e2e8f0;
  margin: 0 0 4px;
}

.page-sub {
  font-size: 13px;
  color: #475569;
  margin: 0;
}

// 统计卡片
.stat-row {
  display: flex;
  gap: 16px;
  margin-bottom: 24px;
}

.stat-card {
  flex: 1;
  background: #0b1320;
  border: 1px solid #1a2638;
  border-radius: 12px;
  padding: 20px 24px;
  display: flex;
  align-items: center;
  gap: 16px;
}

.stat-icon {
  width: 48px;
  height: 48px;
  border-radius: 12px;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;

  &.purple { background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); }
  &.green  { background: linear-gradient(135deg, #22c55e 0%, #059669 100%); }

  :deep(.el-icon) { color: #fff; }
}

.stat-num {
  font-size: 32px;
  font-weight: 800;
  color: #e2e8f0;
  line-height: 1;
}

.stat-label {
  font-size: 13px;
  color: #475569;
  margin-top: 4px;
}

// 表格卡片
.table-card {
  background: #0b1320;
  border: 1px solid #1a2638;
  border-radius: 12px;
  overflow: hidden;

  :deep(.el-table) {
    background: transparent;
    color: #c9d1d9;

    th.el-table__cell {
      background: #060d16;
      color: #475569;
      border-bottom: 1px solid #1a2638;
      font-size: 12px;
      text-transform: uppercase;
      letter-spacing: .04em;
    }

    td.el-table__cell {
      background: transparent;
      border-bottom: 1px solid #0f1e2e;
      color: #c9d1d9;
    }

    tr:hover td.el-table__cell { background: rgba(255,255,255,.03); }

    .el-table__expand-icon { color: #475569; }
    .el-table__expand-icon--expanded { color: #22c55e; }
  }
}

.username-cell {
  display: flex;
  align-items: center;
  gap: 10px;
}

.avatar {
  width: 30px;
  height: 30px;
  border-radius: 50%;
  background: linear-gradient(135deg, #22c55e, #059669);
  color: #fff;
  font-size: 12px;
  font-weight: 700;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}

.conv-count {
  color: #64748b;
  font-size: 14px;
}

// 展开区
.expand-wrap {
  padding: 16px 32px 16px 60px;
  background: #060d16;
}

.conv-loading, .conv-empty {
  font-size: 13px;
  color: #475569;
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 8px 0;
}

.conv-grid {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.conv-item {
  background: #0b1320;
  border: 1px solid #1a2638;
  border-radius: 8px;
  padding: 10px 14px;
}

.conv-title {
  font-size: 13px;
  font-weight: 500;
  color: #e2e8f0;
  margin-bottom: 6px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.conv-meta {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 12px;
  color: #475569;
}

// 令牌提示
.token-hint {
  font-size: 13px;
  color: #64748b;
  margin: 0 0 16px;
}

// Element Plus 弹窗深色适配
:deep(.el-dialog) {
  background: #0b1320;
  border: 1px solid #1a2638;

  .el-dialog__title { color: #e2e8f0; }
  .el-dialog__headerbtn .el-dialog__close { color: #475569; }
}
</style>
