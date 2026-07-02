import axios from 'axios'

const svc = axios.create({ baseURL: '', timeout: 30000 })
svc.interceptors.request.use(cfg => {
  const token = localStorage.getItem('kp_auth_token') || ''
  if (token) cfg.headers = { ...cfg.headers, Authorization: `Bearer ${token}` }
  return cfg
})

export const csApi = {
  listRecords: (params) => svc.get('/api/kanban/cs/records', { params }).then(r => r.data),
  getRecord: (id) => svc.get(`/api/kanban/cs/records/${id}`).then(r => r.data),
  createRecord: (data) => svc.post('/api/kanban/cs/records', data).then(r => r.data),
  updateRecord: (id, data) => svc.put(`/api/kanban/cs/records/${id}`, data).then(r => r.data),
  deleteRecord: (id) => svc.delete(`/api/kanban/cs/records/${id}`).then(r => r.data),
  closeRecord: (id, is_closed) => svc.patch(`/api/kanban/cs/records/${id}/close`, { is_closed }).then(r => r.data),
  getCustomers: () => svc.get('/api/kanban/cs/customers').then(r => r.data),
  getOwners: () => svc.get('/api/kanban/cs/owners').then(r => r.data),
  getProductNos: () => svc.get('/api/kanban/cs/product_nos').then(r => r.data),
  getCaseTypes: () => svc.get('/api/kanban/cs/case_types').then(r => r.data),
  getStats: () => svc.get('/api/kanban/cs/stats').then(r => r.data),
  aiQuery: (question) => svc.post('/api/kanban/cs/ai_query', { question }).then(r => r.data),

  // ── 自定义列 ──────────────────────────────────────────────────────────────────
  listCustomColumns: () => svc.get('/api/kanban/cs/custom_columns').then(r => r.data),
  createCustomColumn: (label) => svc.post('/api/kanban/cs/custom_columns', { label }).then(r => r.data),
  renameCustomColumn: (fieldKey, label) => svc.put(`/api/kanban/cs/custom_columns/${fieldKey}`, { label }).then(r => r.data),
  deleteCustomColumn: (fieldKey) => svc.delete(`/api/kanban/cs/custom_columns/${fieldKey}`).then(r => r.data),

  // ── 文件列（纯附件存储，不入知识库）───────────────────────────────────────────
  uploadFile: (id, file) => {
    const fd = new FormData()
    fd.append('file', file)
    return svc.post(`/api/kanban/cs/records/${id}/file`, fd, {
      headers: { 'Content-Type': 'multipart/form-data' },
      timeout: 60000,
    }).then(r => r.data)
  },
  deleteFile: (id, fileId) => svc.delete(`/api/kanban/cs/records/${id}/file/${fileId}`).then(r => r.data),
  fileDownloadUrl: (id, fileId) => `/api/kanban/cs/records/${id}/file/${fileId}`,

  // ── 普通解析路线（Markdown 等文本类文件，直接切分入库）───────────────────────────
  kbUpload: (id, file, partitionId = 'kanban_cs') => {
    const fd = new FormData()
    fd.append('file', file)
    return svc.post(`/api/kanban/cs/records/${id}/kb-upload?partition_id=${partitionId}`, fd, {
      headers: { 'Content-Type': 'multipart/form-data' },
      timeout: 60000,
    }).then(r => r.data)
  },
  kbPollStatus: (id, taskId) =>
    svc.get(`/api/kanban/cs/records/${id}/kb-status/${taskId}`).then(r => r.data),

  // ── 智能解析路线 ─────────────────────────────────────────────────────────────
  parserUpload: (id, file, partitionId = 'kanban_cs') => {
    const fd = new FormData()
    fd.append('file', file)
    return svc.post(`/api/kanban/cs/records/${id}/parser-upload?partition_id=${partitionId}`, fd, {
      headers: { 'Content-Type': 'multipart/form-data' },
      timeout: 120000,
    }).then(r => r.data)
  },
  parserTrigger: (id, docId) =>
    svc.post(`/api/kanban/cs/records/${id}/parser-trigger/${docId}`).then(r => r.data),
  parserPollStatus: (id, docId) =>
    svc.get(`/api/kanban/cs/records/${id}/parser-status/${docId}`).then(r => r.data),
  parserGetSummary: (id, docId) =>
    svc.get(`/api/kanban/cs/records/${id}/parser-summary/${docId}`).then(r => r.data),
  parserSaveDraft: (id, summaryId, reviewedJson) =>
    svc.put(`/api/kanban/cs/records/${id}/parser-summary/${summaryId}`, { reviewed_json: reviewedJson }).then(r => r.data),
  parserApprove: (id, summaryId) =>
    svc.post(`/api/kanban/cs/records/${id}/parser-approve/${summaryId}`).then(r => r.data),
  parserReviewAction: (id, summaryId, action, comment = '') =>
    svc.post(`/api/kanban/cs/records/${id}/parser-review-action/${summaryId}`, { action, comment }).then(r => r.data),
  parserDeleteAttachment: (id, filename) =>
    svc.delete(`/api/kanban/cs/records/${id}/parser-attachment`, { params: { filename } }).then(r => r.data),
}
