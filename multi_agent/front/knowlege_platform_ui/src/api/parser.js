import { knowledgeService } from './request'

// ── 文档管理 ──
export function parserUploadDocument(formData) {
  return knowledgeService({
    url: '/parser/documents',
    method: 'post',
    data: formData,
    timeout: 0
  })
}

export function parserListDocuments(partitionId) {
  return knowledgeService({
    url: '/parser/documents',
    method: 'get',
    params: partitionId ? { partition_id: partitionId } : {}
  })
}

export function parserGetDocument(documentId) {
  return knowledgeService({
    url: `/parser/documents/${documentId}`,
    method: 'get'
  })
}

export function parserTriggerParse(documentId) {
  return knowledgeService({
    url: `/parser/documents/${documentId}/parse`,
    method: 'post'
  })
}

// ── 摘要管理 ──
export function parserGetSummaryByDoc(documentId) {
  return knowledgeService({
    url: `/parser/documents/${documentId}/summary`,
    method: 'get'
  })
}

export function parserUpdateSummary(summaryId, reviewedJson) {
  return knowledgeService({
    url: `/parser/case-summaries/${summaryId}`,
    method: 'put',
    data: { reviewed_json: reviewedJson }
  })
}

export function parserApproveSummary(summaryId) {
  return knowledgeService({
    url: `/parser/case-summaries/${summaryId}/approve`,
    method: 'post'
  })
}

export function parserDeleteDocument(documentId) {
  return knowledgeService({
    url: `/parser/documents/${documentId}`,
    method: 'delete'
  })
}

export function parserReviewAction(summaryId, action, comment = '') {
  return knowledgeService({
    url: `/parser/case-summaries/${summaryId}/review-action`,
    method: 'post',
    data: { action, comment }
  })
}
