<template>
  <div class="admin-dashboard">

    <!-- 页头 -->
    <div class="page-header">
      <div>
        <h2 class="page-title">{{ activeTab === 'users' ? '成员管理' : '候选指标审核' }}</h2>
        <p class="page-sub" v-if="activeTab === 'users'">共 {{ stats.total_users }} 名成员，当前在线会话 {{ stats.active_sessions }} 个</p>
        <p class="page-sub" v-else>系统自动探测到的候选指标，需人工确认后才会正式生效</p>
      </div>
      <el-button
        :loading="activeTab === 'users' ? loading : candidateLoading"
        @click="activeTab === 'users' ? loadAll() : loadCandidates()"
        :icon="Refresh"
        circle
      />
    </div>

    <el-tabs v-model="activeTab" class="admin-tabs">
      <el-tab-pane label="成员管理" name="users" />
      <el-tab-pane label="候选指标审核" name="candidates" />
    </el-tabs>

    <!-- 统计卡片 -->
    <div class="stat-row" v-if="activeTab === 'users'">
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
    <div class="table-card" v-if="activeTab === 'users'">
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

        <el-table-column label="用户名" min-width="160">
          <template #default="{ row }">
            <div class="username-cell">
              <div class="avatar" :class="{ 'avatar-admin': row.is_admin }">
                {{ row.username.charAt(0).toUpperCase() }}
              </div>
              <span>{{ row.username }}</span>
              <el-tag v-if="row.is_admin" size="small" type="warning" effect="dark">管理员</el-tag>
            </div>
          </template>
        </el-table-column>

        <el-table-column label="注册时间" width="180">
          <template #default="{ row }">{{ formatDate(row.created_at) }}</template>
        </el-table-column>

        <el-table-column label="在线状态" width="120">
          <template #default="{ row }">
            <el-tag :type="row.active_sessions > 0 ? 'success' : 'info'" size="small">
              {{ row.active_sessions > 0 ? `在线 ${row.active_sessions}` : '离线' }}
            </el-tag>
          </template>
        </el-table-column>

        <el-table-column label="对话数" width="80" align="center">
          <template #default="{ row }">
            <span class="conv-count">{{ row.conversation_count }}</span>
          </template>
        </el-table-column>

        <el-table-column label="操作" width="220" align="right">
          <template #default="{ row }">
            <el-button
              text
              :type="row.is_admin ? 'warning' : 'primary'"
              size="small"
              :disabled="row.id === currentUserId"
              @click="toggleAdmin(row)"
            >
              {{ row.is_admin ? '取消管理员' : '设为管理员' }}
            </el-button>
            <el-button text type="primary" size="small" @click="openReset(row)">
              重置密码
            </el-button>
            <el-button
              text type="danger" size="small"
              :disabled="row.id === currentUserId"
              @click="handleDelete(row)"
            >
              删除
            </el-button>
          </template>
        </el-table-column>
      </el-table>
    </div>

    <!-- 候选指标表格 -->
    <div class="table-card" v-if="activeTab === 'candidates'">
      <el-table v-loading="candidateLoading" :data="candidates" row-key="candidate_key" style="width: 100%">
        <el-table-column label="候选指标" min-width="160">
          <template #default="{ row }">
            <div>{{ row.label || row.metric_guess || row.candidate_key }}</div>
            <div class="candidate-key">{{ row.candidate_key }}</div>
          </template>
        </el-table-column>
        <el-table-column label="猜测单位" width="100">
          <template #default="{ row }">{{ row.unit_guess || '-' }}</template>
        </el-table-column>
        <el-table-column label="出现项目数" width="110" align="center">
          <template #default="{ row }">{{ row.distinct_project_count || 0 }}</template>
        </el-table-column>
        <el-table-column label="观测次数" width="100" align="center">
          <template #default="{ row }">{{ row.occurrence_count || (row.occurrences || []).length }}</template>
        </el-table-column>
        <el-table-column label="状态" width="150">
          <template #default="{ row }">
            <el-tag size="small" :type="statusTagType(row.status)">{{ statusLabel(row.status) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="更新时间" width="180">
          <template #default="{ row }">{{ formatDate(row.updated_at || row.created_at) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="180" align="right">
          <template #default="{ row }">
            <el-button
              text type="primary" size="small"
              :disabled="['approved', 'approved_auto', 'rejected'].includes(row.status)"
              @click="openApprove(row)"
            >通过</el-button>
            <el-button
              text type="danger" size="small"
              :disabled="['approved', 'approved_auto', 'rejected'].includes(row.status)"
              @click="handleReject(row)"
            >驳回</el-button>
          </template>
        </el-table-column>
      </el-table>
      <div v-if="!candidateLoading && !candidates.length" class="conv-empty" style="padding: 24px;">
        暂无候选指标
      </div>
    </div>

    <!-- 候选指标审核弹窗 -->
    <el-dialog v-model="approveDialog.visible" title="通过候选指标" width="420px">
      <p class="dialog-hint">
        补全正式指标信息后注册为正式指标：<strong>{{ approveDialog.candidate?.label || approveDialog.candidate?.candidate_key }}</strong>
      </p>
      <el-form :model="approveDialog" label-width="110px" style="margin-top: 8px">
        <el-form-item label="正式指标 ID">
          <el-input v-model="approveDialog.metric_id" placeholder="如 hic_cis_trans_ratio" />
        </el-form-item>
        <el-form-item label="显示名称">
          <el-input v-model="approveDialog.label" placeholder="留空则沿用候选名称" />
        </el-form-item>
        <el-form-item label="单位">
          <el-input v-model="approveDialog.unit" placeholder="如 % / ratio / count" />
        </el-form-item>
        <el-form-item label="校验合约">
          <el-select v-model="approveDialog.verifier_contract" style="width: 100%">
            <el-option label="strict_formula_recalculation（严格重算）" value="strict_formula_recalculation" />
            <el-option label="citation_only（只引用）" value="citation_only" />
            <el-option label="display_value_only（只展示）" value="display_value_only" />
            <el-option label="non_numeric_design_status（定性）" value="non_numeric_design_status" />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="approveDialog.visible = false">取消</el-button>
        <el-button type="primary" :loading="approveDialog.loading" @click="confirmApprove">确认通过</el-button>
      </template>
    </el-dialog>

    <!-- 重置密码弹窗 -->
    <el-dialog v-model="resetDialog.visible" title="重置密码" width="380px">
      <p class="dialog-hint">
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
import { ref, reactive, onMounted, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Refresh, User, Connection, Loading } from '@element-plus/icons-vue'
import { adminApi } from '@/api/admin.js'

// 当前登录用户 ID（用于禁用自身操作按钮）
const currentUserId = localStorage.getItem('kp_user_id') || ''

// ── 数据 ────────────────────────────────────────────────────────────────────
const loading = ref(false)
const stats = ref({ total_users: 0, active_sessions: 0 })
const users = ref([])
const activeTab = ref('users')

// 对话展开
const conversations = reactive({})
const convLoading = reactive({})

// ── 初始化 ───────────────────────────────────────────────────────────────────
onMounted(() => loadAll())

watch(activeTab, (val) => {
  if (val === 'candidates' && !candidates.value.length) loadCandidates()
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
    ElMessage.error(e.message || '加载失败，请确认当前账号是管理员')
  } finally {
    loading.value = false
  }
}

// ── 对话展开 ─────────────────────────────────────────────────────────────────
async function handleExpand(row, expanded) {
  const isExpanding = expanded.some(r => r.id === row.id)
  if (!isExpanding) return
  const uid = row.id
  if (conversations[uid]) return
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

// ── 切换管理员 ────────────────────────────────────────────────────────────────
async function toggleAdmin(row) {
  const action = row.is_admin ? '取消' : '设置'
  try {
    await ElMessageBox.confirm(
      `确定${action} "${row.username}" 的管理员权限？`,
      `${action}管理员`,
      { type: 'warning', confirmButtonText: '确认', cancelButtonText: '取消' }
    )
  } catch { return }
  try {
    await adminApi.setAdminStatus(row.id, !row.is_admin)
    ElMessage.success(`已${action} ${row.username} 的管理员权限`)
    loadAll()
  } catch (e) {
    ElMessage.error(e.message || '操作失败')
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

// ── 候选指标审核（Phase 1.5）───────────────────────────────────────────────────
const candidateLoading = ref(false)
const candidates = ref([])

async function loadCandidates() {
  candidateLoading.value = true
  try {
    const data = await adminApi.listCandidateMetrics()
    candidates.value = data.items || []
  } catch (e) {
    ElMessage.error(e.message || '加载候选指标失败')
  } finally {
    candidateLoading.value = false
  }
}

function statusLabel(status) {
  const map = {
    shadow: '影子层观察中',
    pending_review: '待人工复核',
    eligible_for_auto_promotion: '待自动转正',
    approved_auto: '已自动转正',
    approved: '已人工通过',
    rejected: '已驳回',
  }
  return map[status] || status || '-'
}

function statusTagType(status) {
  if (status === 'approved' || status === 'approved_auto') return 'success'
  if (status === 'rejected') return 'info'
  if (status === 'pending_review' || status === 'eligible_for_auto_promotion') return 'warning'
  return ''
}

const approveDialog = reactive({
  visible: false,
  candidate: null,
  metric_id: '',
  label: '',
  unit: '',
  verifier_contract: 'display_value_only',
  loading: false,
})

function openApprove(row) {
  Object.assign(approveDialog, {
    visible: true,
    candidate: row,
    metric_id: row.candidate_key || '',
    label: row.label || '',
    unit: row.unit_guess || '',
    verifier_contract: 'display_value_only',
    loading: false,
  })
}

async function confirmApprove() {
  if (!approveDialog.metric_id.trim()) { ElMessage.warning('请填写正式指标 ID'); return }
  if (!approveDialog.unit.trim()) { ElMessage.warning('请填写单位'); return }
  approveDialog.loading = true
  try {
    await adminApi.approveCandidateMetric(approveDialog.candidate.candidate_key, {
      metric_id: approveDialog.metric_id.trim(),
      unit: approveDialog.unit.trim(),
      verifier_contract: approveDialog.verifier_contract,
      label: approveDialog.label.trim() || undefined,
    })
    ElMessage.success('候选指标已通过并注册')
    approveDialog.visible = false
    loadCandidates()
  } catch (e) {
    ElMessage.error(e.message || '审核失败')
  } finally {
    approveDialog.loading = false
  }
}

async function handleReject(row) {
  try {
    await ElMessageBox.confirm(
      `确定驳回候选指标 "${row.label || row.candidate_key}"？驳回后将加入黑名单，不再重复上报。`,
      '驳回确认',
      { type: 'warning', confirmButtonText: '确认驳回', cancelButtonText: '取消' }
    )
  } catch { return }
  try {
    await adminApi.rejectCandidateMetric(row.candidate_key)
    ElMessage.success('已驳回')
    loadCandidates()
  } catch (e) {
    ElMessage.error(e.message || '驳回失败')
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
  color: #1e293b;
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
  color: #1e293b;
  margin: 0 0 4px;
}

.page-sub {
  font-size: 13px;
  color: #94a3b8;
  margin: 0;
}

.stat-row {
  display: flex;
  gap: 16px;
  margin-bottom: 24px;
}

.stat-card {
  flex: 1;
  background: #ffffff;
  border: 1px solid #e2e8f0;
  border-radius: 12px;
  padding: 20px 24px;
  display: flex;
  align-items: center;
  gap: 16px;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.04);
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
  color: #1e293b;
  line-height: 1;
}

.stat-label {
  font-size: 13px;
  color: #94a3b8;
  margin-top: 4px;
}

.table-card {
  background: #ffffff;
  border: 1px solid #e2e8f0;
  border-radius: 12px;
  overflow: hidden;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.04);

  :deep(.el-table) {
    background: transparent;
    color: #334155;

    th.el-table__cell {
      background: #fafbfc;
      color: #94a3b8;
      border-bottom: 1px solid #f1f5f9;
      font-size: 12px;
      text-transform: uppercase;
      letter-spacing: .04em;
    }

    td.el-table__cell {
      background: transparent;
      border-bottom: 1px solid #f8fafc;
      color: #334155;
    }

    tr:hover td.el-table__cell { background: #f8fafc; }
    .el-table__expand-icon { color: #94a3b8; }
    .el-table__expand-icon--expanded { color: #16a34a; }
  }
}

.username-cell {
  display: flex;
  align-items: center;
  gap: 8px;
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

  &.avatar-admin {
    background: linear-gradient(135deg, #f59e0b, #d97706);
  }
}

.conv-count { color: #94a3b8; font-size: 14px; }

.expand-wrap {
  padding: 16px 32px 16px 60px;
  background: #f8fafc;
}

.conv-loading, .conv-empty {
  font-size: 13px;
  color: #94a3b8;
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
  background: #ffffff;
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  padding: 10px 14px;
}

.conv-title {
  font-size: 13px;
  font-weight: 500;
  color: #334155;
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
  color: #94a3b8;
}

.dialog-hint {
  font-size: 13px;
  color: #64748b;
  margin: 0 0 16px;
}

.admin-tabs {
  margin-bottom: 16px;

  :deep(.el-tabs__nav-wrap::after) { background-color: #e2e8f0; }
}

.candidate-key {
  font-size: 12px;
  color: #94a3b8;
  margin-top: 2px;
}
</style>
