<template>
  <el-drawer
    v-model="visible"
    :title="att?.name || '智能解析审核'"
    direction="rtl"
    size="78%"
    :close-on-click-modal="false"
    @open="onOpen"
  >
    <!-- 顶部文件信息 -->
    <template #header>
      <div class="drawer-head">
        <el-icon class="pdf-icon"><Document /></el-icon>
        <span class="drawer-title">{{ att?.name }}</span>
        <el-tag size="small" class="partition-tag">{{ att?.partition_id }}</el-tag>
        <el-tag size="small" :type="statusType(att?.parse_status)" class="status-tag">
          {{ statusLabel(att?.parse_status) }}
        </el-tag>
      </div>
    </template>

    <!-- 主体：左图 + 右表单 -->
    <div v-if="loading" class="loading-wrap">
      <el-skeleton :rows="6" animated />
    </div>
    <div v-else class="drawer-body">

      <!-- 左：页面图片预览 -->
      <div class="img-panel">
        <div class="panel-label">页面预览 <span class="page-count">{{ pageCount }} 页</span></div>
        <div class="img-scroll">
          <div
            v-for="n in pageCount"
            :key="n"
            class="page-thumb"
            :class="{ active: activePage === n }"
            @click="activePage = n"
          >
            <img
              :src="pageImgUrl(n)"
              :alt="`第${n}页`"
              loading="lazy"
              @error="e => e.target.style.display='none'"
            />
            <span class="page-num">{{ n }}</span>
          </div>
          <div v-if="!pageCount" class="no-img">
            <el-icon><Picture /></el-icon>
            <span>图片转换中…</span>
          </div>
        </div>
      </div>

      <!-- 右：结构化表单 -->
      <div class="form-panel">
        <div class="panel-label">审核内容 <span class="schema-hint">{{ schemaLabel }}</span></div>

        <!-- case_report 字段 -->
        <template v-if="schemaType === 'case_report'">
          <div class="field-group">
            <div class="field-label">客户问题</div>
            <el-input
              v-model="form.customer_question"
              type="textarea"
              :autosize="{ minRows: 3, maxRows: 8 }"
              placeholder="描述客户遇到的问题…"
            />
          </div>
          <div class="field-group">
            <div class="field-label">结论</div>
            <el-input
              v-model="form.conclusion"
              type="textarea"
              :autosize="{ minRows: 3, maxRows: 8 }"
              placeholder="分析结论及解决方案…"
            />
          </div>
          <div class="field-group">
            <div class="field-label">
              局限性 / 注意点
              <el-button text size="small" type="primary" @click="addLimitation">
                <el-icon><Plus /></el-icon> 添加
              </el-button>
            </div>
            <div
              v-for="(lim, i) in form.limitations"
              :key="i"
              class="limitation-row"
            >
              <span class="lim-idx">{{ i + 1 }}</span>
              <el-input v-model="form.limitations[i]" size="small" style="flex:1" />
              <el-button text size="small" type="danger" @click="removeLimitation(i)">
                <el-icon><Delete /></el-icon>
              </el-button>
            </div>
            <div v-if="!form.limitations.length" class="empty-hint">暂无局限性条目</div>
          </div>
        </template>

        <!-- reagent_manual 字段 -->
        <template v-else-if="schemaType === 'reagent_manual'">
          <div class="field-group">
            <div class="field-label">产品名称</div>
            <el-input v-model="form.product_name" placeholder="产品全称…" />
          </div>
          <div class="field-group">
            <div class="field-label">说明摘要</div>
            <el-input
              v-model="form.summary"
              type="textarea"
              :autosize="{ minRows: 4, maxRows: 10 }"
              placeholder="产品用途、使用方法要点…"
            />
          </div>
        </template>

        <!-- general_doc 字段 -->
        <template v-else>
          <div class="field-group">
            <div class="field-label">内容摘要</div>
            <el-input
              v-model="form.summary"
              type="textarea"
              :autosize="{ minRows: 5, maxRows: 12 }"
              placeholder="文档核心内容摘要…"
            />
          </div>
          <div class="field-group">
            <div class="field-label">
              关键要点
              <el-button text size="small" type="primary" @click="addKeyPoint">
                <el-icon><Plus /></el-icon> 添加
              </el-button>
            </div>
            <div v-for="(kp, i) in form.key_points" :key="i" class="limitation-row">
              <span class="lim-idx">{{ i + 1 }}</span>
              <el-input v-model="form.key_points[i]" size="small" style="flex:1" />
              <el-button text size="small" type="danger" @click="form.key_points.splice(i,1)">
                <el-icon><Delete /></el-icon>
              </el-button>
            </div>
            <div v-if="!form.key_points.length" class="empty-hint">暂无要点条目</div>
          </div>
        </template>

        <!-- 备注（所有类型共有） -->
        <div class="field-group">
          <div class="field-label">审核备注（可选）</div>
          <el-input v-model="reviewComment" type="textarea" :rows="2" placeholder="如有驳回或需修改，请填写原因…" />
        </div>
      </div>
    </div>

    <!-- 底部操作 -->
    <template #footer>
      <div class="drawer-footer">
        <el-button :loading="saving" @click="saveDraft">保存草稿</el-button>
        <div style="flex:1" />
        <el-button type="danger"   :loading="acting" @click="doAction('rejected')">驳回</el-button>
        <el-button type="warning"  :loading="acting" @click="doAction('needs_revision')">需修改</el-button>
        <el-button type="success"  :loading="acting" @click="doApprove">审核通过</el-button>
      </div>
    </template>
  </el-drawer>
</template>

<script setup>
import { ref, computed, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Delete, Document, Picture, Plus } from '@element-plus/icons-vue'

const props = defineProps({
  modelValue: Boolean,
  att:        { type: Object, default: null },  // attachment 对象
  recordId:   { type: Number, default: null },
  kanbanType: { type: String, default: 'rd' },  // 'rd' | 'cs'
})
const emit = defineEmits(['update:modelValue', 'done'])

const visible = ref(false)
watch(() => props.modelValue, v => { visible.value = v })
watch(visible, v => emit('update:modelValue', v))

// ── 状态 ──────────────────────────────────────────────────────────────────────
const loading       = ref(false)
const saving        = ref(false)
const acting        = ref(false)
const summaryData   = ref(null)   // KB 返回的摘要原始数据
const activePage    = ref(1)
const pageCount     = ref(0)
const reviewComment = ref('')

// 表单数据（按 schema 统一结构）
const form = ref({
  customer_question: '',
  conclusion:        '',
  limitations:       [],
  product_name:      '',
  summary:           '',
  key_points:        [],
})

// ── 计算属性 ──────────────────────────────────────────────────────────────────
const PARTITION_SCHEMA = {
  kanban_cs: 'case_report',
  cuttag: 'case_report', atac: 'case_report', hic: 'case_report',
  foodie: 'case_report', rnaseq: 'case_report', smallrna: 'case_report',
  dnaseq: 'case_report', capture: 'case_report', mngs: 'case_report', primer: 'case_report',
  reagent: 'reagent_manual',
  general: 'general_doc', reference: 'general_doc', kanban_rd: 'general_doc',
}

const schemaType = computed(() => PARTITION_SCHEMA[props.att?.partition_id] || 'general_doc')
const schemaLabel = computed(() => ({
  case_report: '案例报告',
  reagent_manual: '试剂说明书',
  general_doc: '通用文档',
}[schemaType.value] || ''))

function pageImgUrl(n) {
  const pad = String(n).padStart(4, '0')
  return `/api/kanban/${props.kanbanType}/parser-image/${props.att?.doc_id}/page_${pad}.png`
}

// ── 状态徽章 ──────────────────────────────────────────────────────────────────
const STATUS_MAP = {
  pending: ['info', '待转换'], converting: ['warning', '转图中'],
  converted: ['', '待解析'], summarizing: ['warning', '解析中'],
  pending_review: ['', '待审核'], summary_failed: ['danger', '解析失败'],
  approved: ['', '入库中'], indexed: ['success', '已入库'],
  needs_revision: ['warning', '需修改'], rejected: ['danger', '已驳回'],
}
function statusType(s)  { return STATUS_MAP[s]?.[0] ?? '' }
function statusLabel(s) { return STATUS_MAP[s]?.[1] ?? s  }

// ── 表单操作 ──────────────────────────────────────────────────────────────────
function addLimitation() { form.value.limitations.push('') }
function removeLimitation(i) { form.value.limitations.splice(i, 1) }
function addKeyPoint() { form.value.key_points.push('') }

function buildReviewedJson() {
  if (schemaType.value === 'case_report') {
    return {
      customer_question: { text: form.value.customer_question },
      conclusion:        { text: form.value.conclusion },
      limitations:       form.value.limitations.filter(Boolean),
    }
  }
  if (schemaType.value === 'reagent_manual') {
    return { product_name: form.value.product_name, summary: form.value.summary }
  }
  return {
    summary:    { text: form.value.summary },
    key_points: form.value.key_points.filter(Boolean),
  }
}

function fillFormFromJson(json) {
  if (!json) return
  if (schemaType.value === 'case_report') {
    form.value.customer_question = json.customer_question?.text || ''
    form.value.conclusion        = json.conclusion?.text        || ''
    form.value.limitations       = Array.isArray(json.limitations) ? [...json.limitations] : []
  } else if (schemaType.value === 'reagent_manual') {
    form.value.product_name = json.product_name || ''
    form.value.summary      = json.summary      || ''
  } else {
    form.value.summary     = json.summary?.text || ''
    form.value.key_points  = Array.isArray(json.key_points) ? [...json.key_points] : []
  }
}

// ── 加载摘要 ──────────────────────────────────────────────────────────────────
async function onOpen() {
  if (!props.att?.doc_id) return
  loading.value = true
  reviewComment.value = ''
  form.value = { customer_question:'', conclusion:'', limitations:[],
                 product_name:'', summary:'', key_points:[] }
  try {
    // 查页数
    const statusResp = await fetch(
      `/api/kanban/${props.kanbanType}/records/${props.recordId}/parser-status/${props.att.doc_id}`,
      { headers: authHeaders() }
    )
    const statusData = await statusResp.json()
    pageCount.value = statusData.page_count || 0

    // 查摘要
    const summResp = await fetch(
      `/api/kanban/${props.kanbanType}/records/${props.recordId}/parser-summary/${props.att.doc_id}`,
      { headers: authHeaders() }
    )
    if (summResp.ok) {
      summaryData.value = await summResp.json()
      const json = summaryData.value?.reviewed_json || summaryData.value?.draft_json
      fillFormFromJson(json)
      // 把 summary_id 写回 att（父组件传入的引用）
      if (summaryData.value?.summary_id && props.att) {
        props.att.summary_id = summaryData.value.summary_id
      }
    }
  } catch (e) {
    ElMessage.error('加载摘要失败：' + e.message)
  } finally {
    loading.value = false
  }
}

// ── 保存草稿 ──────────────────────────────────────────────────────────────────
async function saveDraft() {
  const sid = summaryData.value?.summary_id
  if (!sid) { ElMessage.warning('摘要尚未生成，无法保存'); return }
  saving.value = true
  try {
    await fetch(
      `/api/kanban/${props.kanbanType}/records/${props.recordId}/parser-summary/${sid}`,
      {
        method: 'PUT',
        headers: { ...authHeaders(), 'Content-Type': 'application/json' },
        body: JSON.stringify({ reviewed_json: buildReviewedJson() }),
      }
    )
    ElMessage.success('草稿已保存')
  } catch (e) {
    ElMessage.error('保存失败：' + e.message)
  } finally {
    saving.value = false
  }
}

// ── 审核操作 ──────────────────────────────────────────────────────────────────
async function doApprove() {
  const sid = summaryData.value?.summary_id
  if (!sid) { ElMessage.warning('摘要尚未生成'); return }
  // 先自动保存一次草稿
  acting.value = true
  try {
    await fetch(
      `/api/kanban/${props.kanbanType}/records/${props.recordId}/parser-summary/${sid}`,
      {
        method: 'PUT',
        headers: { ...authHeaders(), 'Content-Type': 'application/json' },
        body: JSON.stringify({ reviewed_json: buildReviewedJson() }),
      }
    )
    const resp = await fetch(
      `/api/kanban/${props.kanbanType}/records/${props.recordId}/parser-approve/${sid}`,
      { method: 'POST', headers: authHeaders() }
    )
    if (!resp.ok) throw new Error((await resp.json()).detail || resp.statusText)
    ElMessage.success('审核通过，后台正在入库')
    emit('done', { ...props.att, parse_status: 'approved', summary_id: sid })
    visible.value = false
  } catch (e) {
    ElMessage.error('审核失败：' + e.message)
  } finally {
    acting.value = false
  }
}

async function doAction(action) {
  const label = action === 'rejected' ? '驳回' : '标记需修改'
  try {
    await ElMessageBox.confirm(
      reviewComment.value
        ? `备注：${reviewComment.value}`
        : `确认${label}此文档？`,
      label, { type: 'warning' }
    )
  } catch { return }
  const sid = summaryData.value?.summary_id
  if (!sid) { ElMessage.warning('摘要尚未生成'); return }
  acting.value = true
  try {
    const resp = await fetch(
      `/api/kanban/${props.kanbanType}/records/${props.recordId}/parser-review-action/${sid}`,
      {
        method: 'POST',
        headers: { ...authHeaders(), 'Content-Type': 'application/json' },
        body: JSON.stringify({ action, comment: reviewComment.value }),
      }
    )
    if (!resp.ok) throw new Error((await resp.json()).detail || resp.statusText)
    ElMessage.success(label + '成功')
    emit('done', { ...props.att, parse_status: action, summary_id: sid })
    visible.value = false
  } catch (e) {
    ElMessage.error(label + '失败：' + e.message)
  } finally {
    acting.value = false
  }
}

function authHeaders() {
  const token = localStorage.getItem('kp_auth_token') || ''
  return token ? { Authorization: `Bearer ${token}` } : {}
}
</script>

<style scoped>
.drawer-head {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}
.pdf-icon { color: var(--el-color-danger); font-size: 18px; flex-shrink: 0; }
.drawer-title {
  font-size: 14px;
  font-weight: 600;
  color: #1e293b;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 300px;
}
.partition-tag { margin-left: 4px; }
.status-tag    { margin-left: 2px; }

/* 主体布局 */
.drawer-body {
  display: flex;
  gap: 0;
  height: calc(100vh - 140px);
  overflow: hidden;
}

/* 左侧图片面板 */
.img-panel {
  width: 220px;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  border-right: 1px solid #e2e8f0;
  padding: 10px 8px;
  gap: 8px;
}
.img-scroll {
  flex: 1;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.page-thumb {
  position: relative;
  border: 1.5px solid #e2e8f0;
  border-radius: 6px;
  overflow: hidden;
  cursor: pointer;
  background: #f8fafc;
  min-height: 120px;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: border-color .15s;
}
.page-thumb img { width: 100%; display: block; }
.page-thumb.active { border-color: #3b82f6; }
.page-thumb:hover  { border-color: #94a3b8; }
.page-num {
  position: absolute;
  bottom: 3px;
  right: 5px;
  font-size: 10px;
  color: #94a3b8;
  background: rgba(255,255,255,.8);
  padding: 0 3px;
  border-radius: 3px;
}
.no-img {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 8px;
  color: #94a3b8;
  font-size: 12px;
  padding: 24px 0;
}
.no-img .el-icon { font-size: 32px; }

/* 右侧表单面板 */
.form-panel {
  flex: 1;
  padding: 10px 16px;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.panel-label {
  font-size: 12px;
  font-weight: 600;
  color: #64748b;
  letter-spacing: .04em;
  margin-bottom: 2px;
  display: flex;
  align-items: center;
  gap: 6px;
}
.page-count  { font-weight: 400; color: #94a3b8; }
.schema-hint { font-weight: 400; color: #94a3b8; font-size: 11px; }

.field-group  { display: flex; flex-direction: column; gap: 5px; }
.field-label  {
  font-size: 12px;
  font-weight: 500;
  color: #475569;
  display: flex;
  align-items: center;
  gap: 6px;
}
.limitation-row {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 4px;
}
.lim-idx {
  font-size: 11px;
  color: #94a3b8;
  width: 16px;
  text-align: center;
  flex-shrink: 0;
}
.empty-hint { font-size: 12px; color: #94a3b8; padding: 4px 0; }

/* 底部 */
.drawer-footer {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
}

.loading-wrap {
  padding: 20px;
}
</style>
