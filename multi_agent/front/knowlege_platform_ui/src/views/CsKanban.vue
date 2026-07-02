<template>
  <div class="kanban-page">
    <div class="toolbar">
      <el-input v-model="search" placeholder="搜索客户/问题" clearable size="small" class="search-input">
        <template #prefix><el-icon><Search /></el-icon></template>
      </el-input>
      <el-popover placement="bottom-start" :width="320" trigger="click">
        <template #reference>
          <el-badge :value="activeFilterCount" :hidden="!activeFilterCount" :max="9" class="filter-badge">
            <el-button size="small">筛选</el-button>
          </el-badge>
        </template>
        <div class="filter-panel">
          <div class="filter-panel-row">
            <span class="filter-panel-label">客户</span>
            <el-select v-model="filterCustomer" placeholder="不限" filterable clearable size="small" style="width:190px">
              <el-option v-for="c in customers" :key="c" :label="c" :value="c" />
            </el-select>
          </div>
          <div class="filter-panel-row">
            <span class="filter-panel-label">货号</span>
            <el-input v-model="filterProductNo" placeholder="不限" clearable size="small" style="width:190px" />
          </div>
          <div class="filter-panel-row">
            <span class="filter-panel-label">类型</span>
            <el-select v-model="filterCaseType" placeholder="不限" filterable clearable size="small" style="width:190px">
              <el-option v-for="c in caseTypes" :key="c" :label="c" :value="c" />
            </el-select>
          </div>
          <div class="filter-panel-row">
            <span class="filter-panel-label">售前/售后</span>
            <el-select v-model="filterType" placeholder="不限" clearable size="small" style="width:190px">
              <el-option label="售前" value="售前" />
              <el-option label="售后" value="售后" />
            </el-select>
          </div>
          <div class="filter-panel-row">
            <span class="filter-panel-label">结题</span>
            <el-select v-model="filterClosed" placeholder="不限" clearable size="small" style="width:190px">
              <el-option label="进行中" :value="0" />
              <el-option label="已结题" :value="1" />
            </el-select>
          </div>
          <div class="filter-panel-row">
            <span class="filter-panel-label">日期</span>
            <el-date-picker
              v-model="filterDateRange"
              type="daterange"
              range-separator="—"
              start-placeholder="开始"
              end-placeholder="结束"
              value-format="YYYY-MM-DD"
              size="small"
              style="width:190px"
            />
          </div>
          <div class="filter-panel-actions">
            <el-button size="small" @click="resetFilters">重置筛选</el-button>
          </div>
        </div>
      </el-popover>
      <div class="toolbar-spacer" />
      <el-button size="small" @click="columnSettingsVisible = true">列设置</el-button>
      <el-button type="primary" size="small" :icon="Plus" @click="openCreateDialog">新建客户</el-button>
    </div>

    <div class="board">
      <div class="board-head" :style="gridStyle">
        <div>客户</div>
        <div v-for="col in visibleBaseColumns" :key="col.key">{{ col.label }}</div>
        <div>状态</div>
        <div v-for="col in visibleCustomColumns" :key="col.field_key">{{ col.label }}</div>
        <div>文件</div>
        <div>知识库</div>
        <div>操作</div>
      </div>

      <template v-for="group in groups" :key="group.name">
        <div class="group-row" @click="toggleGroup(group.name)">
          <el-icon class="toggle" :class="{ open: group.open }"><ArrowRight /></el-icon>
          <strong>{{ group.name }}</strong>
          <span class="row-count">{{ group.activeCount }} 进行中 / {{ group.closedCount }} 已结题</span>
          <el-button text size="small" type="primary" :icon="Plus" @click.stop="addRow(group)">添加记录</el-button>
        </div>

        <template v-if="group.open">
          <div v-for="row in group.rows" :key="row.id" class="data-row" :class="{ closed: row.is_closed }" :style="gridStyle">
            <div class="project-cell">{{ row.customer_name }}</div>
            <EditableCell
              v-for="col in visibleBaseColumns"
              :key="col.key"
              :model-value="row[col.key]"
              :type="col.type || 'text'"
              :multiline="col.multiline"
              @save="value => saveField(row, col.key, value)"
            />
            <div>
              <el-tag :type="row.is_closed ? 'success' : 'warning'" size="small" @click="toggleClosed(row)">
                {{ row.is_closed ? '已结题' : '进行中' }}
              </el-tag>
            </div>
            <EditableCell
              v-for="col in visibleCustomColumns"
              :key="col.field_key"
              :model-value="row.extra_data?.[col.field_key]"
              multiline
              @save="value => saveExtraField(row, col.field_key, value)"
            />

            <!-- 文件列：纯附件存储，不入知识库 -->
            <div class="kb-cell">
              <div v-for="f in fileAtts(row)" :key="f.file_id" class="att-item">
                <a class="att-name" :href="csApi.fileDownloadUrl(row.id, f.file_id)" target="_blank" :title="f.name">{{ f.name }}</a>
                <span class="file-size">{{ formatSize(f.size) }}</span>
                <el-icon class="att-del" @click.stop="removeFile(row, f)"><Close /></el-icon>
              </div>
              <el-button text size="small" type="primary" class="kb-upload-btn" :loading="plainUploading.has(row.id)" @click.stop="triggerFileUpload(row)">
                <el-icon><Upload /></el-icon>
                <span v-if="!fileAtts(row).length">上传</span>
              </el-button>
            </div>

            <!-- 知识库列：智能解析 + 人工审核入库 -->
            <div class="kb-cell">
              <div v-for="att in kbAtts(row)" :key="att.name" class="att-item">
                <span class="att-name" :title="att.name">{{ att.name }}</span>
                <el-tag size="small" :type="parseTagType(att.parse_status)" class="att-tag">
                  {{ parseTagLabel(att.parse_status, att.chunks_added) }}
                </el-tag>
                <el-tooltip v-if="att.parse_status === 'pending_review' || att.parse_status === 'needs_revision'" content="审核">
                  <el-icon class="att-review" @click.stop="openReview(row, att)"><EditPen /></el-icon>
                </el-tooltip>
                <el-tooltip v-else-if="att.parse_status === 'summary_failed' || att.parse_status === 'error'" content="重新上传">
                  <el-icon class="att-retry" @click.stop="triggerUpload(row)"><RefreshRight /></el-icon>
                </el-tooltip>
                <el-icon class="att-del" @click.stop="removeAttachment(row, att)"><Close /></el-icon>
              </div>
              <el-button text size="small" type="primary" class="kb-upload-btn" :loading="uploading.has(row.id)" @click.stop="triggerUpload(row)">
                <el-icon><Upload /></el-icon>
                <span v-if="!kbAtts(row).length">上传</span>
              </el-button>
            </div>

            <div class="ops-cell">
              <el-button text size="small" :type="row.is_closed ? 'warning' : 'success'" @click="toggleClosed(row)">
                {{ row.is_closed ? '撤销' : '结题' }}
              </el-button>
              <el-button text size="small" type="danger" :icon="Delete" @click="deleteRow(row)" />
            </div>
          </div>
        </template>
      </template>
    </div>

    <el-dialog v-model="createVisible" title="新建客户记录" width="460px" destroy-on-close>
      <el-form :model="newRecord" label-width="86px">
        <el-form-item label="客户名称" required><el-input v-model="newRecord.customer_name" /></el-form-item>
        <el-form-item label="记录类型">
          <el-radio-group v-model="newRecord.record_type">
            <el-radio value="售前">售前</el-radio>
            <el-radio value="售后">售后</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="产品货号"><el-input v-model="newRecord.product_no" /></el-form-item>
        <el-form-item label="开始日期"><el-date-picker v-model="newRecord.start_date" type="date" value-format="YYYY-MM-DD" /></el-form-item>
        <el-form-item label="负责人"><el-input v-model="newRecord.owner" /></el-form-item>
        <el-form-item label="问题"><el-input v-model="newRecord.problem" type="textarea" :rows="2" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="createVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="createRecord">创建</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="columnSettingsVisible" title="列设置" width="440px">
      <div class="col-settings">
        <div class="col-settings-row" v-for="col in BASE_COLUMNS" :key="col.key">
          <el-checkbox :model-value="isColumnVisible(col.key)" @change="toggleColumn(col.key)">{{ col.label }}</el-checkbox>
        </div>
        <div class="col-settings-row" v-for="col in customColumns" :key="col.field_key">
          <el-checkbox :model-value="isColumnVisible('custom:' + col.field_key)" @change="toggleColumn('custom:' + col.field_key)">{{ col.label }}</el-checkbox>
          <el-tag size="small" type="info">自定义</el-tag>
          <el-button text size="small" type="danger" :icon="Delete" @click="removeCustomColumn(col)" />
        </div>
      </div>
      <div class="col-settings-add">
        <el-input v-model="newColumnLabel" placeholder="新列名称，如「关注点」" size="small" @keyup.enter="addCustomColumn" />
        <el-button type="primary" size="small" @click="addCustomColumn">添加列</el-button>
      </div>
      <template #footer>
        <el-button @click="columnSettingsVisible = false">关闭</el-button>
      </template>
    </el-dialog>

    <KanbanParserDrawer
      v-model="drawerVisible"
      :att="drawerAtt"
      :record-id="drawerRow?.id"
      kanban-type="cs"
      @done="onDrawerDone"
    />
    <input ref="fileInput" type="file" class="hidden-file" accept=".pdf,.ppt,.pptx,.doc,.docx,.xls,.xlsx,.md,.markdown" @change="onFileSelected" />
    <input ref="plainFileInput" type="file" class="hidden-file" @change="onPlainFileSelected" />
  </div>
</template>

<script setup>
import { computed, defineComponent, h, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { ArrowRight, Close, Delete, EditPen, Plus, RefreshRight, Search, Upload } from '@element-plus/icons-vue'
import { csApi } from '@/api/kanban_cs'
import KanbanParserDrawer from '@/components/KanbanParserDrawer.vue'

const EditableCell = defineComponent({
  props: {
    modelValue: { type: [String, Number], default: '' },
    multiline: { type: Boolean, default: false },
    type: { type: String, default: 'text' },
  },
  emits: ['save'],
  setup(props, { emit }) {
    const editing = ref(false)
    const draft = ref(props.modelValue || '')
    const start = () => {
      draft.value = props.modelValue || ''
      editing.value = true
    }
    const commit = () => {
      editing.value = false
      emit('save', draft.value)
    }
    return () => h('div', { class: 'editable-cell', onClick: start }, editing.value
      ? h(props.type === 'date' ? 'input' : props.multiline ? 'textarea' : 'input', {
          class: 'cell-input',
          value: draft.value,
          type: props.type === 'date' ? 'date' : 'text',
          onInput: event => { draft.value = event.target.value },
          onBlur: commit,
          onKeydown: event => {
            if (event.key === 'Enter' && !props.multiline) {
              event.preventDefault()
              commit()
            }
          },
          autofocus: true,
        })
      : h('span', { class: 'cell-text' }, props.modelValue || ''))
  },
})

// ── 内置列定义（key 与 record 字段名一致；"状态" 单独固定展示，不纳入可隐藏列表）──────
const BASE_COLUMNS = [
  { key: 'record_type', label: '类型', width: '70px' },
  { key: 'product_no', label: '产品货号', width: '140px' },
  { key: 'start_date', label: '开始日期', type: 'date', width: '106px' },
  { key: 'problem', label: '问题', multiline: true, width: 'minmax(220px,1fr)' },
  { key: 'conclusion', label: '结论/进展', multiline: true, width: 'minmax(200px,1fr)' },
]

const records = ref([])
const search = ref('')
const filterType = ref('')
const filterClosed = ref(null)
const filterCustomer = ref('')
const filterProductNo = ref('')
const filterCaseType = ref('')
const filterDateRange = ref([])
const customers = ref([])
const caseTypes = ref([])
const createVisible = ref(false)
const saving = ref(false)
const newRecord = reactive(makeNewRecord())
const fileInput = ref(null)
const uploadRow = ref(null)
const uploading = ref(new Set())
const polling = {}

// 文件列（纯附件存储）
const plainFileInput = ref(null)
const plainUploadRow = ref(null)
const plainUploading = ref(new Set())

const drawerVisible = ref(false)
const drawerRow = ref(null)
const drawerAtt = ref(null)

// ── 自定义列 ──────────────────────────────────────────────────────────────────
const customColumns = ref([])
const columnSettingsVisible = ref(false)
const newColumnLabel = ref('')

// ── 列显示/隐藏（内置 + 自定义统一管理，存浏览器本地）─────────────────────────────
const HIDDEN_COLS_KEY = 'kanban_cs_hidden_cols'
function loadHiddenCols() {
  try { return new Set(JSON.parse(localStorage.getItem(HIDDEN_COLS_KEY) || '[]')) }
  catch { return new Set() }
}
const hiddenColumnKeys = ref(loadHiddenCols())
function isColumnVisible(key) { return !hiddenColumnKeys.value.has(key) }
function toggleColumn(key) {
  const next = new Set(hiddenColumnKeys.value)
  if (next.has(key)) next.delete(key)
  else next.add(key)
  hiddenColumnKeys.value = next
  localStorage.setItem(HIDDEN_COLS_KEY, JSON.stringify([...next]))
}
const visibleBaseColumns = computed(() => BASE_COLUMNS.filter(c => isColumnVisible(c.key)))
const visibleCustomColumns = computed(() => customColumns.value.filter(c => isColumnVisible('custom:' + c.field_key)))

// ── 分组展开/折叠（用独立集合持久化，避免每次刷新数据都被重置）──────────────────────
const collapsedGroups = ref(new Set())

function toggleGroup(name) {
  const next = new Set(collapsedGroups.value)
  if (next.has(name)) next.delete(name)
  else next.add(name)
  collapsedGroups.value = next
}

const groups = computed(() => {
  const map = new Map()
  for (const row of records.value) {
    const name = row.customer_name || '未知客户'
    if (!map.has(name)) map.set(name, { name, rows: [], open: !collapsedGroups.value.has(name) })
    map.get(name).rows.push(row)
  }
  return [...map.values()].map(group => ({
    ...group,
    rows: group.rows.sort((a, b) => String(b.start_date || '').localeCompare(String(a.start_date || ''))),
    activeCount: group.rows.filter(row => !row.is_closed).length,
    closedCount: group.rows.filter(row => row.is_closed).length,
  }))
})

const gridStyle = computed(() => {
  const parts = ['150px']
  parts.push(...visibleBaseColumns.value.map(c => c.width))
  parts.push('72px') // 状态
  parts.push(...visibleCustomColumns.value.map(() => '150px'))
  parts.push('170px', '210px', '106px') // 文件 / 知识库 / 操作
  return { gridTemplateColumns: parts.join(' ') }
})

const activeFilterCount = computed(() => {
  let n = 0
  if (filterCustomer.value) n++
  if (filterProductNo.value) n++
  if (filterCaseType.value) n++
  if (filterType.value) n++
  if (filterClosed.value !== null && filterClosed.value !== '') n++
  if (filterDateRange.value && filterDateRange.value.length === 2) n++
  return n
})

let searchDebounce = null
watch(search, () => {
  clearTimeout(searchDebounce)
  searchDebounce = setTimeout(fetchRecords, 400)
})
watch([filterType, filterClosed, filterCustomer, filterProductNo, filterCaseType, filterDateRange], fetchRecords)

function makeNewRecord() {
  return {
    customer_name: '',
    record_type: '售后',
    product_no: '',
    start_date: new Date().toISOString().slice(0, 10),
    owner: '',
    problem: '',
    is_closed: 0,
  }
}

function resetFilters() {
  filterType.value = ''
  filterClosed.value = null
  filterCustomer.value = ''
  filterProductNo.value = ''
  filterCaseType.value = ''
  filterDateRange.value = []
}

async function fetchRecords() {
  const params = { page: 1, page_size: 5000 }
  if (filterType.value) params.record_type = filterType.value
  if (filterClosed.value !== null && filterClosed.value !== '') params.is_closed = filterClosed.value
  if (filterCustomer.value) params.customer_name = filterCustomer.value
  if (filterProductNo.value) params.product_no = filterProductNo.value
  if (filterCaseType.value) params.case_type = filterCaseType.value
  if (search.value.trim()) params.keyword = search.value.trim()
  if (filterDateRange.value && filterDateRange.value.length === 2) {
    params.date_from = filterDateRange.value[0]
    params.date_to = filterDateRange.value[1]
  }
  const result = await csApi.listRecords(params)
  records.value = (result.records || []).map(row => ({ ...row, attachments: row.attachments || [], extra_data: row.extra_data || {} }))
}

async function fetchFilterOptions() {
  try {
    const [customersRes, caseTypesRes] = await Promise.all([csApi.getCustomers(), csApi.getCaseTypes()])
    customers.value = customersRes.customers || []
    caseTypes.value = caseTypesRes.case_types || []
  } catch {
    // 筛选下拉可选项加载失败不影响主流程
  }
}

async function fetchCustomColumns() {
  try {
    const result = await csApi.listCustomColumns()
    customColumns.value = result.columns || []
  } catch {
    // 忽略，不阻塞看板主流程
  }
}

async function addCustomColumn() {
  const label = newColumnLabel.value.trim()
  if (!label) { ElMessage.warning('请输入列名称'); return }
  try {
    const col = await csApi.createCustomColumn(label)
    customColumns.value.push(col)
    newColumnLabel.value = ''
  } catch (error) {
    ElMessage.error(error.response?.data?.detail || error.message || '添加列失败')
  }
}

async function removeCustomColumn(col) {
  try {
    await ElMessageBox.confirm(`删除列「${col.label}」？已录入的内容也会一并隐藏。`, '删除列', { type: 'warning' })
  } catch {
    return
  }
  try {
    await csApi.deleteCustomColumn(col.field_key)
    customColumns.value = customColumns.value.filter(c => c.field_key !== col.field_key)
  } catch (error) {
    ElMessage.error(error.message || '删除失败')
  }
}

function openCreateDialog() {
  Object.assign(newRecord, makeNewRecord())
  createVisible.value = true
}

async function createRecord() {
  if (!newRecord.customer_name.trim()) {
    ElMessage.warning('请输入客户名称')
    return
  }
  saving.value = true
  try {
    const created = await csApi.createRecord({ ...newRecord })
    records.value.push({ ...created, attachments: created.attachments || [], extra_data: created.extra_data || {} })
    createVisible.value = false
  } catch (error) {
    ElMessage.error(error.message || '创建失败')
  } finally {
    saving.value = false
  }
}

async function addRow(group) {
  const created = await csApi.createRecord({
    customer_name: group.name,
    record_type: '售后',
    start_date: new Date().toISOString().slice(0, 10),
  })
  records.value.push({ ...created, attachments: created.attachments || [], extra_data: created.extra_data || {} })
  if (collapsedGroups.value.has(group.name)) {
    const next = new Set(collapsedGroups.value)
    next.delete(group.name)
    collapsedGroups.value = next
  }
}

async function saveField(row, key, value) {
  row[key] = value
  try {
    await csApi.updateRecord(row.id, { [key]: value })
  } catch (error) {
    ElMessage.error(error.message || '保存失败')
  }
}

async function saveExtraField(row, fieldKey, value) {
  if (!row.extra_data) row.extra_data = {}
  row.extra_data[fieldKey] = value
  try {
    await csApi.updateRecord(row.id, { extra_data: { [fieldKey]: value } })
  } catch (error) {
    ElMessage.error(error.message || '保存失败')
  }
}

async function toggleClosed(row) {
  const next = row.is_closed ? 0 : 1
  try {
    await csApi.closeRecord(row.id, next)
    row.is_closed = next
  } catch (error) {
    ElMessage.error(error.message || '操作失败')
  }
}

async function deleteRow(row) {
  try {
    await ElMessageBox.confirm('确认删除这条记录？', '删除', { type: 'warning' })
  } catch {
    return
  }
  await csApi.deleteRecord(row.id)
  records.value = records.value.filter(item => item.id !== row.id)
}

// ── 文件列（纯附件存储，不入知识库）──────────────────────────────────────────────
function fileAtts(row) { return (row.attachments || []).filter(a => a.kind === 'file') }
function kbAtts(row)   { return (row.attachments || []).filter(a => a.kind !== 'file') }

function formatSize(bytes) {
  if (!bytes) return ''
  if (bytes < 1024) return `${bytes}B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)}KB`
  return `${(bytes / 1024 / 1024).toFixed(1)}MB`
}

function triggerFileUpload(row) {
  plainUploadRow.value = row
  plainFileInput.value?.click()
}

async function onPlainFileSelected(event) {
  const file = event.target.files?.[0]
  event.target.value = ''
  const row = plainUploadRow.value
  if (!file || !row) return
  plainUploading.value = new Set([...plainUploading.value, row.id])
  try {
    const entry = await csApi.uploadFile(row.id, file)
    if (!Array.isArray(row.attachments)) row.attachments = []
    row.attachments.push(entry)
  } catch (error) {
    ElMessage.error(error.response?.data?.detail || error.message || '上传失败')
  } finally {
    plainUploading.value = new Set([...plainUploading.value].filter(id => id !== row.id))
  }
}

async function removeFile(row, f) {
  try {
    await csApi.deleteFile(row.id, f.file_id)
    row.attachments = (row.attachments || []).filter(a => a.file_id !== f.file_id)
  } catch (error) {
    ElMessage.error(error.message || '删除失败')
  }
}

function triggerUpload(row) {
  uploadRow.value = row
  fileInput.value?.click()
}

// markdown 走普通解析（直接切分入库）；其余格式（PDF/PPT/Word/Excel）走智能解析通道
function isPlainChannel(file) {
  return /\.(md|markdown)$/i.test(file.name)
}

async function onFileSelected(event) {
  const file = event.target.files?.[0]
  event.target.value = ''
  const row = uploadRow.value
  if (!file || !row) return
  uploading.value = new Set([...uploading.value, row.id])
  if (!Array.isArray(row.attachments)) row.attachments = []
  const idx = row.attachments.findIndex(a => a.name === file.name && a.kind !== 'file')
  const plain = isPlainChannel(file)
  const pending = plain
    ? { name: file.name, parse_status: 'uploading', channel: 'plain', task_id: null, chunks_added: 0 }
    : { name: file.name, parse_status: 'converting', doc_id: null, partition_id: 'kanban_cs', chunks_added: 0 }
  if (idx >= 0) row.attachments.splice(idx, 1, pending)
  else row.attachments.push(pending)
  try {
    if (plain) {
      const resp = await csApi.kbUpload(row.id, file, 'kanban_cs')
      const att = row.attachments.find(a => a.name === file.name && a.kind !== 'file')
      if (att) { att.task_id = resp.task_id; att.parse_status = resp.parse_status || 'uploading' }
      if (resp.task_id) startPlainPolling(row, resp.task_id, file.name)
    } else {
      const resp = await csApi.parserUpload(row.id, file, 'kanban_cs')
      const att = row.attachments.find(a => a.name === file.name && a.kind !== 'file')
      if (att) { att.doc_id = resp.doc_id; att.parse_status = resp.parse_status || 'converting' }
      if (resp.doc_id) startPolling(row, resp.doc_id, file.name)
    }
  } catch (error) {
    const att = row.attachments.find(a => a.name === file.name && a.kind !== 'file')
    if (att) att.parse_status = plain ? 'error' : 'summary_failed'
    ElMessage.error(error.response?.data?.detail || error.message || '上传失败')
  } finally {
    uploading.value = new Set([...uploading.value].filter(id => id !== row.id))
  }
}

// ── 普通解析通道轮询（直接切分入库，无需人工审核）──────────────────────────────────
function startPlainPolling(row, taskId, filename) {
  stopPolling(taskId)
  let attempts = 0
  polling[taskId] = setInterval(async () => {
    attempts += 1
    if (attempts > 150) {
      stopPolling(taskId)
      const att = row.attachments?.find(a => a.task_id === taskId)
      if (att) att.parse_status = 'error'
      ElMessage.error(`「${filename}」上传超时`)
      return
    }
    try {
      const s = await csApi.kbPollStatus(row.id, taskId)
      const att = row.attachments?.find(a => a.task_id === taskId)
      if (att) { att.parse_status = s.parse_status; att.chunks_added = s.chunks_added }
      if (s.parse_status === 'indexed' || s.parse_status === 'error') {
        stopPolling(taskId)
        if (s.parse_status === 'indexed') ElMessage.success(`「${filename}」已入库`)
        else ElMessage.error(`「${filename}」上传失败，请重试`)
      }
    } catch { stopPolling(taskId) }
  }, 2000)
}

const TERMINAL = new Set(['pending_review', 'summary_failed', 'indexed', 'needs_revision', 'rejected', 'approved'])

function startPolling(row, docId, filename) {
  stopPolling(docId)
  let attempts = 0
  polling[docId] = setInterval(async () => {
    attempts += 1
    if (attempts > 150) {
      stopPolling(docId)
      const att = row.attachments?.find(a => a.doc_id === docId)
      if (att) att.parse_status = 'summary_failed'
      ElMessage.error(`「${filename}」解析超时`)
      return
    }
    try {
      const s = await csApi.parserPollStatus(row.id, docId)
      const att = row.attachments?.find(a => a.doc_id === docId)
      if (att) att.parse_status = s.parse_status
      if (s.parse_status === 'converted') {
        stopPolling(docId)
        try {
          await csApi.parserTrigger(row.id, docId)
          if (att) att.parse_status = 'summarizing'
          startPolling(row, docId, filename)
        } catch { if (att) att.parse_status = 'summary_failed' }
        return
      }
      if (TERMINAL.has(s.parse_status)) {
        stopPolling(docId)
        if (s.parse_status === 'pending_review') ElMessage.success(`「${filename}」解析完成，请审核`)
        else if (s.parse_status === 'indexed') ElMessage.success(`「${filename}」已入库`)
        else if (s.parse_status === 'summary_failed') ElMessage.error(`「${filename}」解析失败，请重试`)
      }
    } catch { stopPolling(docId) }
  }, 2500)
}

function stopPolling(docId) {
  if (polling[docId]) { clearInterval(polling[docId]); delete polling[docId] }
}

async function removeAttachment(row, att) {
  try {
    await csApi.parserDeleteAttachment(row.id, att.name)
    row.attachments = (row.attachments || []).filter(a => a.name !== att.name || a.kind === 'file')
  } catch (error) {
    ElMessage.error(error.message || '删除失败')
  }
}

function openReview(row, att) {
  drawerRow.value  = row
  drawerAtt.value  = att
  drawerVisible.value = true
}

function onDrawerDone(updatedAtt) {
  const row = drawerRow.value
  if (!row) return
  const idx = (row.attachments || []).findIndex(a => a.name === updatedAtt.name && a.kind !== 'file')
  if (idx >= 0) {
    Object.assign(row.attachments[idx], updatedAtt)
    if (updatedAtt.parse_status === 'approved') startPolling(row, updatedAtt.doc_id, updatedAtt.name)
  }
}

const PARSE_TYPE = {
  pending:'info', converting:'warning', converted:'', summarizing:'warning',
  pending_review:'', summary_failed:'danger', approved:'warning',
  indexed:'success', needs_revision:'warning', rejected:'danger',
  uploading:'warning', error:'danger',
}
const PARSE_LABEL = {
  pending:'待转换', converting:'转图中', converted:'待解析', summarizing:'解析中',
  pending_review:'待审核', summary_failed:'解析失败', approved:'入库中',
  indexed:'已入库', needs_revision:'需修改', rejected:'已驳回',
  uploading:'上传中', error:'上传失败',
}
function parseTagType(s)          { return PARSE_TYPE[s]  ?? '' }
function parseTagLabel(s, chunks) {
  if (s === 'indexed') return `已入库(${chunks ?? 0}块)`
  return PARSE_LABEL[s] ?? '上传中'
}

onMounted(async () => {
  await Promise.all([fetchRecords(), fetchCustomColumns(), fetchFilterOptions()])
  for (const row of records.value) {
    for (const att of kbAtts(row)) {
      const active = new Set(['converting', 'converted', 'summarizing', 'approved'])
      if (active.has(att.parse_status) && att.doc_id) startPolling(row, att.doc_id, att.name)
      if (att.parse_status === 'uploading' && att.task_id) startPlainPolling(row, att.task_id, att.name)
    }
  }
})
onUnmounted(() => Object.keys(polling).forEach(stopPolling))
</script>

<style scoped>
.kanban-page {
  height: 100%;
  min-height: 0;
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.toolbar {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
}
.search-input { width: 200px; }
.filter-badge :deep(.el-badge__content) { z-index: 1; }
.toolbar-spacer { flex: 1; }
.filter-panel {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.filter-panel-row {
  display: flex;
  align-items: center;
  gap: 8px;
}
.filter-panel-label {
  width: 62px;
  flex-shrink: 0;
  font-size: 12px;
  color: #64748b;
}
.filter-panel-actions {
  display: flex;
  justify-content: flex-end;
}
.board {
  flex: 1;
  min-height: 0;
  overflow: auto;
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  background: #fff;
}
.board-head,
.data-row {
  display: grid;
}
.board-head {
  position: sticky;
  top: 0;
  z-index: 2;
  background: #f8fafc;
  color: #475569;
  font-size: 12px;
  font-weight: 700;
}
.board-head > div,
.data-row > div {
  padding: 8px 10px;
  border-right: 1px solid #e2e8f0;
  border-bottom: 1px solid #f1f5f9;
  min-width: 0;
}
.data-row.closed {
  opacity: .62;
}
.group-row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 10px;
  background: #f1f5f9;
  border-bottom: 1px solid #e2e8f0;
  cursor: pointer;
}
.toggle { transition: transform .16s; color: #64748b; }
.toggle.open { transform: rotate(90deg); }
.row-count { color: #94a3b8; font-size: 12px; margin-right: auto; }
.project-cell,
.cell-text {
  white-space: pre-wrap;
  word-break: break-word;
  font-size: 13px;
  color: #334155;
}
.editable-cell {
  min-height: 34px;
  cursor: text;
}
.cell-input {
  width: 100%;
  min-height: 28px;
  border: 1px solid #cbd5e1;
  border-radius: 4px;
  padding: 4px 6px;
  font: inherit;
  box-sizing: border-box;
}
textarea.cell-input {
  resize: vertical;
}
.kb-cell {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 3px;
}
.ops-cell {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 2px;
}
.att-item {
  width: 100%;
  display: flex;
  align-items: center;
  gap: 3px;
  min-width: 0;
}
.att-name {
  flex: 1;
  max-width: 96px;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 11px;
  color: #475569;
}
a.att-name {
  color: #3b82f6;
  text-decoration: none;
}
a.att-name:hover { text-decoration: underline; }
.file-size {
  flex-shrink: 0;
  font-size: 10px;
  color: #94a3b8;
}
.att-tag {
  flex-shrink: 0;
  font-size: 10px;
}
.att-review {
  color: #3b82f6;
  cursor: pointer;
}
.att-review:hover { color: #2563eb; }
.att-retry {
  color: #f59e0b;
  cursor: pointer;
}
.att-del {
  color: #cbd5e1;
  cursor: pointer;
  flex-shrink: 0;
}
.att-del:hover {
  color: #ef4444;
}
.kb-upload-btn {
  padding: 0 4px !important;
  font-size: 12px !important;
}
.hidden-file {
  display: none;
}
.col-settings {
  max-height: 300px;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 4px;
  margin-bottom: 12px;
}
.col-settings-row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 8px;
  border: 1px solid #e2e8f0;
  border-radius: 6px;
  font-size: 13px;
  color: #334155;
}
.col-settings-row > .el-checkbox {
  flex: 1;
}
.col-settings-add {
  display: flex;
  gap: 8px;
}
.empty-hint {
  font-size: 12px;
  color: #94a3b8;
  padding: 8px 0;
}
</style>
