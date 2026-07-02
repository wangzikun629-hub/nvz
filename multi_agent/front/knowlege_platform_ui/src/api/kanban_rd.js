import axios from 'axios'

const svc = axios.create({ baseURL: '', timeout: 30000 })
svc.interceptors.request.use(cfg => {
  const token = localStorage.getItem('kp_auth_token') || ''
  if (token) cfg.headers = { ...cfg.headers, Authorization: `Bearer ${token}` }
  return cfg
})

export const rdApi = {
  listRecords: (params) => svc.get('/api/kanban/rd/records', { params }).then(r => r.data),
  getRecord: (id) => svc.get(`/api/kanban/rd/records/${id}`).then(r => r.data),
  createRecord: (data) => svc.post('/api/kanban/rd/records', data).then(r => r.data),
  updateRecord: (id, data) => svc.put(`/api/kanban/rd/records/${id}`, data).then(r => r.data),
  deleteRecord: (id) => svc.delete(`/api/kanban/rd/records/${id}`).then(r => r.data),
  getProductLines: () => svc.get('/api/kanban/rd/product_lines').then(r => r.data),
  getOwners: () => svc.get('/api/kanban/rd/owners').then(r => r.data),
  getStats: () => svc.get('/api/kanban/rd/stats').then(r => r.data),
  aiQuery: (question) => svc.post('/api/kanban/rd/ai_query', { question }).then(r => r.data),

  // ── 自定义列 ──────────────────────────────────────────────────────────────────
  listCustomColumns: () => svc.get('/api/kanban/rd/custom_columns').then(r => r.data),
  createCustomColumn: (label) => svc.post('/api/kanban/rd/custom_columns', { label }).then(r => r.data),
  renameCustomColumn: (fieldKey, label) => svc.put(`/api/kanban/rd/custom_columns/${fieldKey}`, { label }).then(r => r.data),
  deleteCustomColumn: (fieldKey) => svc.delete(`/api/kanban/rd/custom_columns/${fieldKey}`).then(r => r.data),

  // ── 文件列（纯附件存储，不入知识库）───────────────────────────────────────────
  uploadFile: (id, file) => {
    const fd = new FormData()
    fd.append('file', file)
    return svc.post(`/api/kanban/rd/records/${id}/file`, fd, {
      headers: { 'Content-Type': 'multipart/form-data' },
      timeout: 60000,
    }).then(r => r.data)
  },
  deleteFile: (id, fileId) => svc.delete(`/api/kanban/rd/records/${id}/file/${fileId}`).then(r => r.data),
  fileDownloadUrl: (id, fileId) => `/api/kanban/rd/records/${id}/file/${fileId}`,

  // ── 普通解析路线（Markdown 等文本类文件，直接切分入库）───────────────────────────
  kbUpload: (id, file, partitionId = 'kanban_rd') => {
    const fd = new FormData()
    fd.append('file', file)
    return svc.post(`/api/kanban/rd/records/${id}/kb-upload?partition_id=${partitionId}`, fd, {
      headers: { 'Content-Type': 'multipart/form-data' },
      timeout: 60000,
    }).then(r => r.data)
  },
  kbPollStatus: (id, taskId) =>
    svc.get(`/api/kanban/rd/records/${id}/kb-status/${taskId}`).then(r => r.data),

  // ── 智能解析路线 ─────────────────────────────────────────────────────────────
  // 上传文件；返回 { doc_id, parse_status }
  parserUpload: (id, file, partitionId = 'kanban_rd') => {
    const fd = new FormData()
    fd.append('file', file)
    return svc.post(`/api/kanban/rd/records/${id}/parser-upload?partition_id=${partitionId}`, fd, {
      headers: { 'Content-Type': 'multipart/form-data' },
      timeout: 120000,
    }).then(r => r.data)
  },
  // 触发 LLM 摘要解析（文档转图完成后调用）
  parserTrigger: (id, docId) =>
    svc.post(`/api/kanban/rd/records/${id}/parser-trigger/${docId}`).then(r => r.data),
  // 轮询文档状态；返回 { parse_status, page_count }
  parserPollStatus: (id, docId) =>
    svc.get(`/api/kanban/rd/records/${id}/parser-status/${docId}`).then(r => r.data),
  // 获取摘要（供审核抽屉使用）
  parserGetSummary: (id, docId) =>
    svc.get(`/api/kanban/rd/records/${id}/parser-summary/${docId}`).then(r => r.data),
  // 保存草稿
  parserSaveDraft: (id, summaryId, reviewedJson) =>
    svc.put(`/api/kanban/rd/records/${id}/parser-summary/${summaryId}`, { reviewed_json: reviewedJson }).then(r => r.data),
  // 审核通过
  parserApprove: (id, summaryId) =>
    svc.post(`/api/kanban/rd/records/${id}/parser-approve/${summaryId}`).then(r => r.data),
  // 驳回 / 需修改
  parserReviewAction: (id, summaryId, action, comment = '') =>
    svc.post(`/api/kanban/rd/records/${id}/parser-review-action/${summaryId}`, { action, comment }).then(r => r.data),
  // 删除附件
  parserDeleteAttachment: (id, filename) =>
    svc.delete(`/api/kanban/rd/records/${id}/parser-attachment`, { params: { filename } }).then(r => r.data),
}
