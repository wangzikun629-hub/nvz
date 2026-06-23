<template>
  <div class="knowledge-page">
    <!-- ── 左侧分类栏 ── -->
    <aside class="sidebar-card">
      <div class="sidebar-head">
        <div>
          <div class="sidebar-title">我的分类</div>
          <div class="sidebar-desc">每位用户管理自己的内容</div>
        </div>
        <el-tooltip content="新建分类" placement="right">
          <el-button
            type="primary"
            :icon="Plus"
            circle
            size="small"
            class="add-cat-btn"
            @click="handleCreateCategory"
          />
        </el-tooltip>
      </div>

      <div v-if="categories.length === 0" class="empty-sidebar">
        <el-icon :size="40" color="#1e293b"><FolderOpened /></el-icon>
        <p>暂无分类，先新建一个</p>
        <el-button type="primary" plain size="small" @click="handleCreateCategory">新建分类</el-button>
      </div>

      <div v-else class="category-list">
        <div
          v-for="cat in categories"
          :key="cat.id"
          class="category-item"
          :class="{ active: selectedCategoryId === cat.id }"
          @click="selectCategory(cat.id)"
        >
          <el-icon class="cat-icon"><Folder /></el-icon>
          <span class="cat-name">{{ cat.name }}</span>
          <span class="cat-count">{{ cat.file_count ?? 0 }}</span>
          <div class="cat-actions" @click.stop>
            <button class="cat-btn" title="重命名" @click="handleRenameCategory(cat)">
              <el-icon><EditPen /></el-icon>
            </button>
            <button class="cat-btn danger" title="删除分类" @click="handleDeleteCategory(cat)">
              <el-icon><Delete /></el-icon>
            </button>
          </div>
        </div>
      </div>
    </aside>

    <!-- ── 右侧主区域 ── -->
    <section class="main-panel">
      <template v-if="activeCategory">
        <!-- 页头 -->
        <header class="content-header">
          <div class="content-header-left">
            <el-icon class="header-folder-icon"><FolderOpened /></el-icon>
            <div>
              <h1 class="content-title">{{ activeCategory.name }}</h1>
              <span class="content-meta">{{ filteredFiles.length }} 个文件</span>
            </div>
          </div>
        </header>

        <!-- 上传卡片 -->
        <div class="panel-card">
          <div class="panel-card-header">
            <el-icon class="pch-icon upload-color"><UploadFilled /></el-icon>
            <span class="pch-title">上传文件</span>
            <span class="pch-hint">文件将自动切分并写入向量库</span>
          </div>
          <el-upload
            drag
            action="#"
            multiple
            :show-file-list="false"
            :disabled="uploading"
            :http-request="handleUpload"
          >
            <el-icon class="el-icon--upload"><UploadFilled /></el-icon>
            <div class="el-upload__text">
              拖拽文件到此处，或<em>点击选择文件</em>
            </div>
            <template #tip>
              <div class="upload-tip-text">支持多文件同时上传，支持常见文档格式</div>
            </template>
          </el-upload>

          <transition name="slide-fade">
            <div v-if="uploadingFileName" class="upload-progress">
              <span class="progress-pulse"></span>
              正在处理：<strong>{{ uploadingFileName }}</strong>
            </div>
          </transition>
        </div>

        <!-- 文件列表卡片 -->
        <div class="panel-card">
          <div class="panel-card-header">
            <el-icon class="pch-icon list-color"><DocumentCopy /></el-icon>
            <span class="pch-title">文件列表</span>
            <el-tag size="small" round class="count-tag">{{ filteredFiles.length }}</el-tag>
            <el-button
              class="refresh-link"
              link
              :loading="filesLoading"
              @click="doLoadFiles"
            >
              <el-icon><Refresh /></el-icon>
              刷新
            </el-button>
          </div>

          <div v-if="filteredFiles.length === 0" class="empty-files">
            <el-icon :size="48" color="#1e293b"><DocumentAdd /></el-icon>
            <p>当前分类还没有文件</p>
            <span class="empty-files-hint">上传文件后将在这里展示</span>
          </div>

          <el-table v-else :data="filteredFiles" class="file-table">
            <el-table-column label="文件名" min-width="220">
              <template #default="{ row }">
                <div class="file-name-cell">
                  <el-icon class="file-icon"><Document /></el-icon>
                  <span :title="row.file_name">{{ row.file_name }}</span>
                </div>
              </template>
            </el-table-column>
            <el-table-column prop="kb_scope" label="Scope" width="110" />
            <el-table-column prop="chunk_count" label="分块" width="72" align="center" />
            <el-table-column label="状态" width="88" align="center">
              <template #default="{ row }">
                <el-tag :type="statusTag(row.status)" size="small" round>
                  {{ statusLabel(row.status) }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column label="更新时间" width="158">
              <template #default="{ row }">
                <span class="time-text">{{ formatDateTime(row.updated_at) }}</span>
              </template>
            </el-table-column>
            <el-table-column label="操作" width="188" align="center" fixed="right">
              <template #default="{ row }">
                <el-button link type="primary" size="small" @click="openFileChunks(row)">查看分块</el-button>
                <el-divider direction="vertical" />
                <el-button link type="warning" size="small" @click="openMoveDialog(row)">移动</el-button>
                <el-divider direction="vertical" />
                <el-button link type="danger" size="small" @click="handleDeleteFile(row)">删除</el-button>
              </template>
            </el-table-column>
          </el-table>
        </div>
      </template>

      <!-- 未选择分类时的欢迎态 -->
      <div v-else class="welcome-state">
        <div class="welcome-content">
          <div class="welcome-icon-wrap">
            <el-icon :size="56" color="#22c55e"><Files /></el-icon>
          </div>
          <h2>选择分类开始管理</h2>
          <p>从左侧选择已有分类，或新建分类后上传文件</p>
          <el-button type="primary" size="large" @click="handleCreateCategory">
            <el-icon><Plus /></el-icon>
            新建第一个分类
          </el-button>
        </div>
      </div>
    </section>

    <!-- 分块预览 dialog -->
    <el-dialog v-model="chunkDialogVisible" title="文件分块预览" width="72%">
      <div v-if="chunkLoading" class="dialog-loading">
        <el-skeleton :rows="6" animated />
      </div>
      <div v-else-if="chunkError" class="dialog-state error">{{ chunkError }}</div>
      <div v-else-if="chunkList.length === 0" class="dialog-state">暂无分块内容</div>
      <div v-else class="chunk-list">
        <article
          v-for="chunk in chunkList"
          :key="`${chunk.file_id || 'tmp'}-${chunk.chunk_index}`"
          class="chunk-item"
          :class="{ deleted: chunk.deleted || chunk.is_deleted }"
        >
          <div class="chunk-head">
            <strong>分块 {{ chunk.chunk_index }}</strong>
            <span>{{ chunk.length }} 字符</span>
          </div>
          <div class="chunk-meta">
            {{ chunk.metadata?.title || chunk.metadata?.source || '未知来源' }}
          </div>
          <pre class="chunk-content">{{ chunk.content }}</pre>
        </article>
      </div>
    </el-dialog>

    <!-- 移动分类 dialog -->
    <el-dialog v-model="moveDialogVisible" title="移动文件到其他分类" width="420px">
      <el-form label-position="top">
        <el-form-item label="目标分类">
          <el-select v-model="moveTargetCategoryId" placeholder="请选择分类" style="width: 100%">
            <el-option
              v-for="cat in movableCategories"
              :key="cat.id"
              :label="cat.name"
              :value="cat.id"
            />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="moveDialogVisible = false">取消</el-button>
        <el-button type="primary" :disabled="!moveTargetCategoryId" @click="confirmMoveFile">
          确认移动
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import {
  Delete,
  Document,
  DocumentAdd,
  DocumentCopy,
  EditPen,
  Files,
  Folder,
  FolderOpened,
  Plus,
  Refresh,
  UploadFilled
} from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'

import {
  createCategory,
  deleteCategory,
  deleteFileRecord,
  getCategories,
  getFileChunks,
  getFiles,
  getUploadChunks,
  getUploadStatus,
  moveFileCategory,
  updateCategory,
  uploadFile
} from '@/api/knowledge'

const categories = ref([])
const selectedCategoryId = ref(null)
const files = ref([])
const uploading = ref(false)
const uploadingFileName = ref('')
const filesLoading = ref(false)

const chunkDialogVisible = ref(false)
const chunkLoading = ref(false)
const chunkError = ref('')
const chunkList = ref([])

const moveDialogVisible = ref(false)
const moveFile = ref(null)
const moveTargetCategoryId = ref(null)

const activeCategory = computed(() =>
  categories.value.find(item => item.id === selectedCategoryId.value) || null
)

const filteredFiles = computed(() =>
  files.value.filter(item => item.category_id === selectedCategoryId.value)
)

const movableCategories = computed(() =>
  categories.value.filter(item => item.id !== moveFile.value?.category_id)
)

const sleep = ms => new Promise(resolve => setTimeout(resolve, ms))

const formatDateTime = value => {
  if (!value) return '-'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return date.toLocaleString('zh-CN', { hour12: false })
}

const statusTag = status => {
  if (status === 'success') return 'success'
  if (status === 'processing') return 'warning'
  if (status === 'deleted') return 'info'
  return 'danger'
}

const statusLabel = status =>
  ({ success: '成功', processing: '处理中', error: '失败', deleted: '已删除' }[status] || status || '未知')

async function loadCategories() {
  categories.value = await getCategories()
  if (!categories.value.length) {
    selectedCategoryId.value = null
    return
  }
  if (!selectedCategoryId.value || !categories.value.some(item => item.id === selectedCategoryId.value)) {
    selectedCategoryId.value = categories.value[0].id
  }
}

async function doLoadFiles() {
  filesLoading.value = true
  try {
    files.value = await getFiles()
  } finally {
    filesLoading.value = false
  }
}

async function loadFiles() {
  files.value = await getFiles()
}

async function refreshAll() {
  await loadCategories()
  await loadFiles()
}

function selectCategory(categoryId) {
  selectedCategoryId.value = categoryId
}

async function handleCreateCategory() {
  try {
    const { value } = await ElMessageBox.prompt('请输入分类名称', '新建分类', {
      confirmButtonText: '创建',
      cancelButtonText: '取消',
      inputPattern: /\S+/,
      inputErrorMessage: '分类名称不能为空'
    })
    const created = await createCategory({ name: value.trim(), description: '' })
    await refreshAll()
    selectedCategoryId.value = created.id
    ElMessage.success('分类已创建')
  } catch (error) {
    if (error !== 'cancel') ElMessage.error(error.message || '创建分类失败')
  }
}

async function handleRenameCategory(cat) {
  const target = cat || activeCategory.value
  if (!target) return
  try {
    const { value } = await ElMessageBox.prompt('请输入新的分类名称', '重命名分类', {
      confirmButtonText: '保存',
      cancelButtonText: '取消',
      inputValue: target.name,
      inputPattern: /\S+/,
      inputErrorMessage: '分类名称不能为空'
    })
    await updateCategory(target.id, { name: value.trim(), description: target.description || '' })
    await refreshAll()
    ElMessage.success('分类已更新')
  } catch (error) {
    if (error !== 'cancel') ElMessage.error(error.message || '更新分类失败')
  }
}

async function handleDeleteCategory(cat) {
  const target = cat || activeCategory.value
  if (!target) return
  try {
    await ElMessageBox.confirm('删除分类前，需确保该分类下没有文件。', '删除分类', {
      confirmButtonText: '删除',
      cancelButtonText: '取消',
      type: 'warning'
    })
    await deleteCategory(target.id)
    await refreshAll()
    ElMessage.success('分类已删除')
  } catch (error) {
    if (error !== 'cancel') ElMessage.error(error.message || '删除分类失败')
  }
}

async function pollUploadStatus(taskId, fileName) {
  while (true) {
    const statusRes = await getUploadStatus(taskId)
    await loadFiles()
    if (statusRes.status === 'success') {
      ElMessage.success(`文件 ${fileName} 上传成功`)
      return
    }
    if (statusRes.status === 'error') {
      throw new Error(statusRes.message || `文件 ${fileName} 上传失败`)
    }
    await sleep(1200)
  }
}

async function handleUpload(options) {
  if (!activeCategory.value) {
    ElMessage.warning('请先选择分类')
    options.onError?.(new Error('未选择分类'))
    return
  }

  const { file, onSuccess, onError } = options
  const formData = new FormData()
  formData.append('file', file)
  formData.append('category_id', String(activeCategory.value.id))
  formData.append('kb_scope', 'general')

  uploading.value = true
  uploadingFileName.value = file.name

  try {
    const result = await uploadFile(formData)
    onSuccess?.(result)
    ElMessage.info(`文件 ${file.name} 已接收，正在后台处理`)
    await loadFiles()
    await pollUploadStatus(result.task_id, file.name)
    await refreshAll()
  } catch (error) {
    onError?.(error)
    ElMessage.error(error.message || `文件 ${file.name} 上传失败`)
  } finally {
    uploading.value = false
    uploadingFileName.value = ''
  }
}

async function openFileChunks(row) {
  chunkDialogVisible.value = true
  chunkLoading.value = true
  chunkError.value = ''
  chunkList.value = []
  try {
    const result =
      row.status === 'processing' && row.upload_task_id
        ? await getUploadChunks(row.upload_task_id)
        : await getFileChunks(row.id)
    chunkList.value = result.chunks || []
  } catch (error) {
    chunkError.value = error.message || '加载分块失败'
  } finally {
    chunkLoading.value = false
  }
}

function openMoveDialog(row) {
  moveFile.value = row
  moveTargetCategoryId.value = null
  moveDialogVisible.value = true
}

async function confirmMoveFile() {
  if (!moveFile.value || !moveTargetCategoryId.value) return
  try {
    await moveFileCategory(moveFile.value.id, { target_category_id: moveTargetCategoryId.value })
    moveDialogVisible.value = false
    await refreshAll()
    ElMessage.success('文件已移动')
  } catch (error) {
    ElMessage.error(error.message || '移动文件失败')
  }
}

async function handleDeleteFile(row) {
  try {
    await ElMessageBox.confirm(
      `确认删除文件「${row.file_name}」吗？这会同时删除向量库中的相关分块。`,
      '删除文件',
      { confirmButtonText: '删除', cancelButtonText: '取消', type: 'warning' }
    )
    await deleteFileRecord(row.id)
    await refreshAll()
    ElMessage.success('文件已删除')
  } catch (error) {
    if (error !== 'cancel') ElMessage.error(error.message || '删除文件失败')
  }
}

onMounted(async () => {
  await refreshAll()
})
</script>

<style lang="scss" scoped>
/* ── 整体布局 ── */
.knowledge-page {
  display: grid;
  grid-template-columns: 260px minmax(0, 1fr);
  gap: 20px;
  min-height: calc(100vh - 40px);
  align-items: start;
}

/* ── 左侧分类栏 ── */
.sidebar-card {
  background: #0b1320;
  border: 1px solid #1e2d40;
  border-radius: 16px;
  padding: 18px;
  position: sticky;
  top: 0;
}

.sidebar-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  margin-bottom: 18px;
  gap: 8px;
}

.sidebar-title {
  font-size: 14px;
  font-weight: 700;
  color: #e2e8f0;
  margin-bottom: 3px;
}

.sidebar-desc {
  font-size: 11px;
  color: #475569;
}

.add-cat-btn {
  flex-shrink: 0;
  margin-top: 2px;
}

.empty-sidebar {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 10px;
  padding: 28px 12px;
  text-align: center;
  color: #475569;
  font-size: 13px;

  p { margin: 0; }
}

.category-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.category-item {
  display: flex;
  align-items: center;
  gap: 9px;
  padding: 11px 12px;
  border-radius: 10px;
  border: 1px solid transparent;
  background: transparent;
  color: #94a3b8;
  cursor: pointer;
  transition: background 0.18s, border-color 0.18s, color 0.18s;
  position: relative;

  &:hover {
    background: #111d2e;
    border-color: #1e2d40;
    color: #e2e8f0;

    .cat-actions { opacity: 1; pointer-events: auto; }
  }

  &.active {
    background: linear-gradient(135deg, rgba(34, 197, 94, 0.12), rgba(11, 19, 32, 0.9));
    border-color: rgba(34, 197, 94, 0.4);
    color: #e2e8f0;

    .cat-icon { color: #22c55e; }
    .cat-count { background: rgba(34, 197, 94, 0.2); color: #4ade80; }

    .cat-actions { opacity: 1; pointer-events: auto; }
  }
}

.cat-icon {
  font-size: 15px;
  flex-shrink: 0;
  color: #475569;
  transition: color 0.18s;
}

.cat-name {
  flex: 1;
  font-size: 13px;
  font-weight: 500;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.cat-count {
  font-size: 11px;
  padding: 2px 7px;
  border-radius: 20px;
  background: #1e2d40;
  color: #64748b;
  font-weight: 600;
  transition: background 0.18s, color 0.18s;
}

.cat-actions {
  display: flex;
  align-items: center;
  gap: 2px;
  opacity: 0;
  pointer-events: none;
  transition: opacity 0.15s;
}

.cat-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 26px;
  height: 26px;
  border-radius: 6px;
  border: none;
  background: transparent;
  color: #64748b;
  cursor: pointer;
  font-size: 13px;
  transition: background 0.15s, color 0.15s;

  &:hover {
    background: rgba(100, 116, 139, 0.15);
    color: #94a3b8;
  }

  &.danger:hover {
    background: rgba(239, 68, 68, 0.15);
    color: #f87171;
  }
}

/* ── 右侧主区域 ── */
.main-panel {
  display: flex;
  flex-direction: column;
  gap: 18px;
  min-width: 0;
}

/* 页头 */
.content-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 20px 24px;
  background: linear-gradient(
    135deg,
    rgba(34, 197, 94, 0.08) 0%,
    rgba(14, 165, 233, 0.06) 60%,
    transparent 100%
  ),
  #0b1320;
  border: 1px solid #1e2d40;
  border-radius: 16px;
}

.content-header-left {
  display: flex;
  align-items: center;
  gap: 14px;
  min-width: 0;
}

.header-folder-icon {
  font-size: 28px;
  color: #22c55e;
  flex-shrink: 0;
}

.content-title {
  margin: 0 0 3px;
  font-size: 20px;
  font-weight: 700;
  color: #f1f5f9;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.content-meta {
  font-size: 12px;
  color: #64748b;
}


/* 卡片通用 */
.panel-card {
  background: #0b1320;
  border: 1px solid #1e2d40;
  border-radius: 16px;
  overflow: hidden;
}

.panel-card-header {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 14px 20px;
  border-bottom: 1px solid #1e2d40;
  background: rgba(255, 255, 255, 0.02);
}

.pch-icon {
  font-size: 16px;
  flex-shrink: 0;
}

.upload-color { color: #60a5fa; }
.list-color   { color: #a78bfa; }

.pch-title {
  font-size: 14px;
  font-weight: 600;
  color: #e2e8f0;
}

.pch-hint {
  font-size: 12px;
  color: #475569;
  margin-left: 4px;
}

.count-tag {
  margin-left: 4px;
  --el-tag-bg-color: #1e2d40;
  --el-tag-text-color: #64748b;
  --el-tag-border-color: transparent;
}

.refresh-link {
  margin-left: auto;
  font-size: 12px;
  color: #475569 !important;
  gap: 4px;

  &:hover { color: #94a3b8 !important; }
}

/* 上传区 */
:deep(.el-upload) {
  width: 100%;
  display: block;
  padding: 0 20px 4px;
}

:deep(.el-upload-dragger) {
  width: 100%;
  border-color: #1e2d40;
  border-radius: 12px;
  background: #070e1a;
  transition: border-color 0.2s, background 0.2s;

  &:hover {
    border-color: #60a5fa;
    background: rgba(96, 165, 250, 0.04);
  }
}

:deep(.el-icon--upload) {
  font-size: 40px;
  color: #60a5fa;
  margin-bottom: 4px;
}

:deep(.el-upload__text) {
  color: #94a3b8;
  font-size: 14px;

  em {
    color: #60a5fa;
    font-style: normal;
    font-weight: 600;
  }
}

:deep(.el-upload__tip) {
  padding: 0 20px 16px;
}

.upload-tip-text {
  color: #475569;
  font-size: 12px;
  text-align: center;
}

.upload-progress {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 0 20px 16px;
  padding: 10px 14px;
  background: rgba(96, 165, 250, 0.08);
  border: 1px solid rgba(96, 165, 250, 0.2);
  border-radius: 8px;
  font-size: 13px;
  color: #94a3b8;

  strong { color: #e2e8f0; }
}

.progress-pulse {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #60a5fa;
  flex-shrink: 0;
  animation: pulse 1.4s ease-in-out infinite;
}

@keyframes pulse {
  0%, 100% { opacity: 1; transform: scale(1); }
  50%       { opacity: 0.4; transform: scale(0.7); }
}

/* 文件列表 */
.empty-files {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
  padding: 48px 24px;
  text-align: center;

  p {
    margin: 0;
    color: #475569;
    font-size: 14px;
  }
}

.empty-files-hint {
  font-size: 12px;
  color: #334155;
}

.file-table {
  width: 100%;
}

.file-name-cell {
  display: flex;
  align-items: center;
  gap: 8px;
  overflow: hidden;

  span {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    color: #dbe4ee;
    font-size: 13px;
  }
}

.file-icon {
  font-size: 14px;
  color: #a78bfa;
  flex-shrink: 0;
}

.time-text {
  font-size: 12px;
  color: #64748b;
}

/* 欢迎态 */
.welcome-state {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: calc(100vh - 100px);
}

.welcome-content {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 14px;
  text-align: center;
  max-width: 380px;

  h2 {
    margin: 0;
    font-size: 22px;
    font-weight: 700;
    color: #e2e8f0;
  }

  p {
    margin: 0;
    font-size: 14px;
    color: #64748b;
    line-height: 1.6;
  }
}

.welcome-icon-wrap {
  width: 100px;
  height: 100px;
  border-radius: 50%;
  background: rgba(34, 197, 94, 0.08);
  border: 1px solid rgba(34, 197, 94, 0.2);
  display: flex;
  align-items: center;
  justify-content: center;
  margin-bottom: 8px;
}

/* Chunk dialog */
.dialog-loading {
  padding: 8px 0;
}

.dialog-state {
  padding: 32px;
  text-align: center;
  color: #64748b;
  font-size: 14px;

  &.error { color: #fca5a5; }
}

.chunk-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
  max-height: 68vh;
  overflow: auto;
}

.chunk-item {
  padding: 16px;
  border-radius: 12px;
  border: 1px solid #1e2d40;
  background: #07101c;

  &.deleted { opacity: 0.5; }
}

.chunk-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 8px;
  color: #f1f5f9;
  font-size: 13px;

  span { color: #64748b; font-size: 12px; }
}

.chunk-meta {
  margin-bottom: 10px;
  font-size: 11px;
  color: #475569;
}

.chunk-content {
  margin: 0;
  white-space: pre-wrap;
  word-break: break-word;
  color: #94a3b8;
  font-size: 13px;
  line-height: 1.6;
}

/* Element Plus table 深色覆盖 */
:deep(.el-table) {
  --el-table-bg-color: transparent;
  --el-table-tr-bg-color: transparent;
  --el-table-row-hover-bg-color: rgba(255, 255, 255, 0.03);
  --el-table-border-color: #1a2638;
  --el-table-header-bg-color: #070e1a;
  --el-table-text-color: #94a3b8;
  --el-table-header-text-color: #64748b;
}

:deep(.el-table__inner-wrapper::before) {
  display: none;
}

:deep(.el-divider--vertical) {
  border-color: #1e2d40;
  margin: 0 4px;
}

/* 动画 */
.slide-fade-enter-active { transition: all 0.3s ease; }
.slide-fade-leave-active  { transition: all 0.2s ease; }
.slide-fade-enter-from    { opacity: 0; transform: translateY(-6px); }
.slide-fade-leave-to      { opacity: 0; transform: translateY(6px); }

@media (max-width: 1024px) {
  .knowledge-page {
    grid-template-columns: 1fr;
  }

  .sidebar-card {
    position: static;
  }

  .content-header {
    flex-direction: column;
    align-items: stretch;
  }

  .content-header-right {
    justify-content: flex-start;
  }
}
</style>
