import request, { knowledgeService } from './request'

export function getCategories() {
  return knowledgeService({
    url: '/categories',
    method: 'get'
  })
}

export function createCategory(data) {
  return knowledgeService({
    url: '/categories',
    method: 'post',
    data
  })
}

export function updateCategory(categoryId, data) {
  return knowledgeService({
    url: `/categories/${categoryId}`,
    method: 'put',
    data
  })
}

export function deleteCategory(categoryId) {
  return knowledgeService({
    url: `/categories/${categoryId}`,
    method: 'delete'
  })
}

export function getFiles(categoryId) {
  return knowledgeService({
    url: '/files',
    method: 'get',
    params: {
      ...(categoryId ? { category_id: categoryId } : {})
    }
  })
}

export function getFileChunks(fileId) {
  return knowledgeService({
    url: `/files/${fileId}/chunks`,
    method: 'get'
  })
}

export function moveFileCategory(fileId, data) {
  return knowledgeService({
    url: `/files/${fileId}/move-category`,
    method: 'post',
    data
  })
}

export function deleteFileRecord(fileId) {
  return knowledgeService({
    url: `/files/${fileId}`,
    method: 'delete'
  })
}

export function uploadFile(data) {
  return knowledgeService({
    url: '/upload',
    method: 'post',
    data,
    timeout: 0
  })
}

export function getUploadStatus(taskId) {
  return knowledgeService({
    url: `/upload/${taskId}`,
    method: 'get',
    timeout: 0
  })
}

export function getUploadChunks(taskId) {
  return knowledgeService({
    url: `/upload/${taskId}/chunks`,
    method: 'get',
    timeout: 0
  })
}

export function deleteUploadChunk(taskId, chunkIndex) {
  return knowledgeService({
    url: `/upload/${taskId}/chunks/${chunkIndex}`,
    method: 'delete',
    timeout: 0
  })
}

export function queryKnowledge(data) {
  return knowledgeService({
    url: '/chat',
    method: 'post',
    data
  })
}

export async function streamQueryKnowledge(data, handlers = {}) {
  const authToken = localStorage.getItem('kp_auth_token') || ''
  const userId = localStorage.getItem('kp_user_id') || localStorage.getItem('kp_user') || ''
  const response = await fetch('/api/query', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...(authToken ? { Authorization: `Bearer ${authToken}` } : {}),
      ...(userId ? { 'X-User-Id': userId } : {})
    },
    body: JSON.stringify({
      query: data.question || data.query || '',
      context: {
        user_id: data.user_id || userId,
        session_id: data.session_id
      },
      flag: data.flag ?? true,
      mode: data.mode ?? 'agent',
      project_id: data.project_id,
      project_root: data.project_root,
      max_evidence_files: data.max_evidence_files ?? 6
    })
  })

  if (!response.ok) {
    const errorText = await response.text()
    throw new Error(errorText || `请求失败，状态码：${response.status}`)
  }

  const reader = response.body?.getReader()
  if (!reader) {
    throw new Error('流式响应不可用。')
  }

  const decoder = new TextDecoder('utf-8')
  let buffer = ''

  while (true) {
    const { value, done } = await reader.read()
    buffer += decoder.decode(value || new Uint8Array(), { stream: !done })

    let boundaryIndex = buffer.indexOf('\n\n')
    while (boundaryIndex !== -1) {
      const rawEvent = buffer.slice(0, boundaryIndex).trim()
      buffer = buffer.slice(boundaryIndex + 2)

      if (rawEvent.startsWith('data: ')) {
        const payload = rawEvent.slice(6).trim()
        if (payload) {
          const packet = JSON.parse(payload)
          handlers.onPacket?.(packet)
        }
      }

      boundaryIndex = buffer.indexOf('\n\n')
    }

    if (done) {
      break
    }
  }

  handlers.onFinish?.()
}

export function getProjectContext(data) {
  return request({
    url: '/project_context',
    method: 'post',
    data
  })
}

export function getSessionMessages(data) {
  return request({
    url: '/session_messages',
    method: 'post',
    data
  })
}

export function clearProjectContext(data) {
  return request({
    url: '/project_context/clear',
    method: 'post',
    data
  })
}
