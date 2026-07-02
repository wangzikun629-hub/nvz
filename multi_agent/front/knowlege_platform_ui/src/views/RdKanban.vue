<template>
  <div class="kanban-page">
    <div class="toolbar">
      <el-input v-model="search" placeholder="搜索项目/问题" clearable size="small" class="search-input">
        <template #prefix><el-icon><Search /></el-icon></template>
      </el-input>
      <el-popover placement="bottom-start" :width="300" trigger="click">
        <template #reference>
          <el-badge :value="activeFilterCount" :hidden="!activeFilterCount" :max="9" class="filter-badge">
            <el-button size="small">筛选</el-button>
          </el-badge>
        </template>
        <div class="filter-panel">
          <div class="filter-panel-row">
            <span class="filter-panel-label">产品线</span>
            <el-select v-model="filterProductLine" placeholder="不限" filterable clearable size="small" style="width:180px">
              <el-option v-for="line in productLines" :key="line" :label="line" :value="line" />
            </el-select>
          </div>
          <div class="filter-panel-row">
            <span class="filter-panel-label">负责人</span>
            <el-select v-model="filterOwner" placeholder="不限" filterable clearable size="small" style="width:180px">
              <el-option v-for="o in owners" :key="o" :label="o" :value="o" />
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
              style="width:180px"
            />
          </div>
          <div class="filter-panel-actions">
            <el-button size="small" @click="resetFilters">重置筛选</el-button>
          </div>
        </div>
      </el-popover>
      <div class="toolbar-spacer" />
      <el-button size="small" @click="columnSettingsVisible = true">列设置</el-button>
      <el-button type="primary" size="small" :icon="Plus" @click="openCreateDialog">新建项目</el-button>
    </div>

    <div class="board">
      <div class="board-head" :style="gridStyle">
        <div>项目</div>
        <div v-for="col in visibleBaseColumns" :key="col.key">{{ col.label }}</div>
        <div v-for="col in visibleCustomColumns" :key="col.field_key">{{ col.label }}</div>
        <div>文件</div>
        <div>知识库</div>
        <div>操作</div>
      </div>

      <template v-for="group in groups" :key="group.name">
        <div class="group-row" @click="toggleGroup(group.name)">
          <el-icon class="toggle" :class="{ open: group.open }"><ArrowRight /></el-icon>
          <strong>{{ group.name }}</strong>
          <el-tag v-if="group.productLine" size="small" type="info">{{ group.productLine }}</el-tag>
          <span class="row-count">{{ group.rows.length }} 条</span>
          <el-button text size="small" type="primary" :icon="Plus" @click.stop="addRow(group)">添加进展</el-button>
        </div>

        <template v-if="group.open">
          <div v-for="row in group.rows" :key="row.id" class="data-row" :style="gridStyle">
            <div class="project-cell">{{ row.project_name }}</div>
            <EditableCell
              v-for="col in visibleBaseColumns"
              :key="col.key"
              :model-value="row[col.key]"
              :type="col.type || 'text'"
              :multiline="col.multiline"
              @save="value => saveField(row, col.key, value)"
            />
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
                <a class="att-name" :href="rdApi.fileDownloadUrl(row.id, f.file_id)" target="_blank" :title="f.name">{{ f.name }}</a>
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
                <!-- 待审核 / 需修改：打开审核抽屉 -->
                <el-tooltip v-if="att.parse_status === 'pending_review' || att.parse_status === 'needs_revision'" content="审核">
                  <el-icon class="att-review" @click.stop="openReview(row, att)"><EditPen /></el-icon>
                </el-tooltip>
                <!-- 失败：重新上传 -->
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
              <el-button text size="small" type="danger" :icon="Delete" @click="deleteRow(row)" />
            </div>
          </div>
        </template>
      </template>
    </div>

    <el-dialog v-model="createVisible" title="新建研发项目" width="460px" destroy-on-close>
      <el-form :model="newRecord" label-width="86px">
        <el-form-item label="项目名称" required><el-input v-model="newRecord.project_name" /></el-form-item>
        <el-form-item label="产品线"><el-input v-model="newRecord.product_line" /></el-form-item>
        <el-form-item label="项目组"><el-input v-model="newRecord.team_group" /></el-form-item>
        <el-form-item label="进展日期"><el-date-picker v-model="newRecord.progress_date" type="date" value-format="YYYY-MM-DD" /></el-form-item>
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
        <div v-if="!BASE_COLUMNS.length && !customColumns.length" class="empty-hint">没有可设置的列</div>
      </div>
      <div class="col-settings-add">
        <el-input v-model="newColumnLabel" placeholder="新列名称，如「实验计划」" size="small" @keyup.enter="addCustomColumn" />
        <el-button type="primary" size="small" @click="addCustomColumn">添加列</el-button>
      </div>
      <template #footer>
        <el-button @click="columnSettingsVisible = false">关闭</el-button>
      </template>
    </el-dialog>

    <!-- 审核抽屉 -->
    <KanbanParserDrawer
      v-model="drawerVisible"
      :att="drawerAtt"
      :record-id="drawerRow?.id"
      kanban-type="rd"
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
import { rdApi } from '@/api/kanban_rd'
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

// ── 内置列定义（key 与 record 字段名一致）────────────────────────────────────────
const BASE_COLUMNS = [
  { key: 'progress_date', label: '进展日期', type: 'date', width: '112px' },
  { key: 'owner', label: '负责人', width: '92px' },
  { key: 'problem', label: '问题', multiline: true, width: 'minmax(180px,1fr)' },
  { key: 'solution', label: '解决方案', multiline: true, width: 'minmax(180px,1fr)' },
  { key: 'conclusion', label: '进展/结论', multiline: true, width: 'minmax(180px,1fr)' },
]

const records = ref([])
const search = ref('')
const filterProductLine = ref('')
const filterOwner = ref('')
const filterDateRange = ref([])
const owners = ref([])
const createVisible = ref(false)
const saving = ref(false)
const newRecord = reactive(makeNewRecord())
const fileInput = ref(null)
const uploadRow = ref(null)
const uploading = ref(new Set())
const polling = {}            // docId → intervalId

// 文件列（纯附件存储）
const plainFileInput = ref(null)
const plainUploadRow = ref(null)
const plainUploading = ref(new Set())

// 审核抽屉
const drawerVisible = ref(false)
const drawerRow = ref(null)
const drawerAtt = ref(null)

// ── 自定义列 ──────────────────────────────────────────────────────────────────
const customColumns = ref([])
const columnSettingsVisible = ref(false)
const newColumnLabel = ref('')

// ── 列显示/隐藏（内置 + 自定义统一管理，存浏览器本地）─────────────────────────────
const HIDDEN_COLS_KEY = 'kanban_rd_hidden_cols'
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

const productLines = computed(() => [...new Set(records.value.map(row => row.product_line).filter(Boolean))].sort())

const groups = computed(() => {
  const map = new Map()
  for (const row of records.value) {
    const name = row.project_name || '未命名项目'
    if (!map.has(name)) map.set(name, { name, productLine: row.product_line || '', rows: [], open: !collapsedGroups.value.has(name) })
    map.get(name).rows.push(row)
  }
  return [...map.values()].map(group => ({
    ...group,
    rows: group.rows.sort((a, b) => String(b.progress_date || '').localeCompare(String(a.progress_date || ''))),
  }))
})

const gridStyle = computed(() => {
  const parts = ['180px']
  parts.push(...visibleBaseColumns.value.map(c => c.width))
  parts.push(...visibleCustomColumns.value.map(() => '150px'))
  parts.push('170px', '220px', '70px') // 文件 / 知识库 / 操作
  return { gridTemplateColumns: parts.join(' ') }
})

const activeFilterCount = computed(() => {
  let n = 0
  if (filterProductLine.value) n++
  if (filterOwner.value) n++
  if (filterDateRange.value && filterDateRange.value.length === 2) n++
  return n
})

let searchDebounce = null
watch(search, () => {
  clearTimeout(searchDebounce)
  searchDebounce = setTimeout(fetchRecords, 400)
})
watch([filterProductLine, filterOwner, filterDateRange], fetchRecords)

function makeNewRecord() {
  return {
    project_name: '',
    product_line: '',
    team_group: '',
    project_bg: '',
    progress_date: new Date().toISOString().slice(0, 10),
    owner: '',
    problem: '',
  }
}

function resetFilters() {
  filterProductLine.value = ''
  filterOwner.value = ''
  filterDateRange.value = []
}

async function fetchRecords() {
  const params = { page: 1, page_size: 5000 }
  if (filterProductLine.value) params.product_line = filterProductLine.value
  if (filterOwner.value) params.owner = filterOwner.value
  if (search.value.trim()) params.keyword = search.value.trim()
  if (filterDateRange.value && filterDateRange.value.length === 2) {
    params.date_from = filterDateRange.value[0]
    params.date_to = filterDateRange.value[1]
  }
  const result = await rdApi.listRecords(params)
  records.value = (result.records || []).map(row => ({ ...row, attachments: row.attachments || [], extra_data: row.extra_data || {} }))
}

async function fetchFilterOptions() {
  try {
    const ownersRes = await rdApi.getOwners()
    owners.value = ownersRes.owners || []
  } catch {
    // 筛选下拉可选项加载失败不影响主流程
  }
}

async function fetchCustomColumns() {
  try {
    const result = await rdApi.listCustomColumns()
    customColumns.value = result.columns || []
  } catch {
    // 忽略，不阻塞看板主流程
  }
}

async function addCustomColumn() {
  const label = newColumnLabel.value.trim()
  if (!label) { ElMessage.warning('请输入列名称'); return }
  try {
    const col = await rdApi.createCustomColumn(label)
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
    await rdApi.deleteCustomColumn(col.field_key)
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
  if (!newRecord.project_name.trim()) {
    ElMessage.warning('请输入项目名称')
    return
  }
  saving.value = true
  try {
    const created = await rdApi.createRecord({ ...newRecord })
    records.value.push({ ...created, attachments: created.attachments || [], extra_data: created.extra_data || {} })
    createVisible.value = false
  } catch (error) {
    ElMessage.error(error.message || '创建失败')
  } finally {
    saving.value = false
  }
}

async function addRow(group) {
  const created = await rdApi.createRecord({
    project_name: group.name,
    product_line: group.productLine,
    progress_date: new Date().toISOString().slice(0, 10),
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
    await rdApi.updateRecord(row.id, { [key]: value })
  } catch (error) {
    ElMessage.error(error.message || '保存失败')
  }
}

async function saveExtraField(row, fieldKey, value) {
  if (!row.extra_data) row.extra_data = {}
  row.extra_data[fieldKey] = value
  try {
    await rdApi.updateRecord(row.id, { extra_data: { [fieldKey]: value } })
  } catch (error) {
    ElMessage.error(error.message || '保存失败')
  }
}

async function deleteRow(row) {
  try {
    await ElMessageBox.confirm('确认删除这条进展记录？', '删除', { type: 'warning' })
  } catch {
    return
  }
  await rdApi.deleteRecord(row.id)
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
    const entry = await rdApi.uploadFile(row.id, file)
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
    await rdApi.deleteFile(row.id, f.file_id)
    row.attachments = (row.attachments || []).filter(a => a.file_id !== f.file_id)
  } catch (error) {
    ElMessage.error(error.message || '删除失败')
  }
}

// ── 上传（普通解析 / 智能解析）─────────────────────────────────────────────────────
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
  // 乐观更新
  const idx = row.attachments.findIndex(a => a.name === file.name && a.kind !== 'file')
  const plain = isPlainChannel(file)
  const pending = plain
    ? { name: file.name, parse_status: 'uploading', channel: 'plain', task_id: null, chunks_added: 0 }
    : { name: file.name, parse_status: 'converting', doc_id: null, partition_id: 'kanban_rd', chunks_added: 0 }
  if (idx >= 0) row.attachments.splice(idx, 1, pending)
  else row.attachments.push(pending)
  try {
    if (plain) {
      const resp = await rdApi.kbUpload(row.id, file, 'kanban_rd')
      const att = row.attachments.find(a => a.name === file.name && a.kind !== 'file')
      if (att) { att.task_id = resp.task_id; att.parse_status = resp.parse_status || 'uploading' }
      if (resp.task_id) startPlainPolling(row, resp.task_id, file.name)
    } else {
      const resp = await rdApi.parserUpload(row.id, file, 'kanban_rd')
      const att = row.attachments.find(a => a.name === file.name && a.kind !== 'file')
      if (att) {
        att.doc_id       = resp.doc_id
        att.parse_status = resp.parse_status || 'converting'
      }
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
      const s = await rdApi.kbPollStatus(row.id, taskId)
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

// ── 轮询状态 ──────────────────────────────────────────────────────────────────
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
      const s = await rdApi.parserPollStatus(row.id, docId)
      const att = row.attachments?.find(a => a.doc_id === docId)
      if (att) att.parse_status = s.parse_status
      // converted → 自动触发 LLM 解析
      if (s.parse_status === 'converted') {
        stopPolling(docId)
        try {
          await rdApi.parserTrigger(row.id, docId)
          if (att) att.parse_status = 'summarizing'
          startPolling(row, docId, filename)
        } catch { if (att) att.parse_status = 'summary_failed' }
        return
      }
      if (TERMINAL.has(s.parse_status)) {
        stopPolling(docId)
        if (s.parse_status === 'pending_review')
          ElMessage.success(`「${filename}」解析完成，请审核`)
        else if (s.parse_status === 'indexed')
          ElMessage.success(`「${filename}」已入库`)
        else if (s.parse_status === 'summary_failed')
          ElMessage.error(`「${filename}」解析失败，请重试`)
      }
    } catch { stopPolling(docId) }
  }, 2500)
}

function stopPolling(docId) {
  if (polling[docId]) { clearInterval(polling[docId]); delete polling[docId] }
}

// ── 删除附件 ──────────────────────────────────────────────────────────────────
async function removeAttachment(row, att) {
  try {
    await rdApi.parserDeleteAttachment(row.id, att.name)
    row.attachments = (row.attachments || []).filter(a => a.name !== att.name || a.kind === 'file')
  } catch (error) {
    ElMessage.error(error.message || '删除失败')
  }
}

// ── 审核抽屉 ──────────────────────────────────────────────────────────────────
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
    if (updatedAtt.parse_status === 'approved') {
      // 审核通过后继续轮询直到 indexed
      startPolling(row, updatedAtt.doc_id, updatedAtt.name)
    }
  }
}

// ── 状态展示 ──────────────────────────────────────────────────────────────────
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
function parseTagType(s)         { return PARSE_TYPE[s]  ?? '' }
function parseTagLabel(s, chunks) {
  if (s === 'indexed') return `已入库(${chunks ?? 0}块)`
  return PARSE_LABEL[s] ?? '上传中'
}

onMounted(async () => {
  await Promise.all([fetchRecords(), fetchCustomColumns(), fetchFilterOptions()])
  // 恢复进行中的轮询（仅智能解析通道需要轮询，文件列是同步上传，无需恢复）
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
.search-input { width: 220px; }
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
  width: 48px;
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
