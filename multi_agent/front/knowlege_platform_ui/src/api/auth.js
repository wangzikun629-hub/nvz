const createAuthHeaders = () => {
  const authToken = localStorage.getItem('kp_auth_token') || ''
  const userId = localStorage.getItem('kp_user_id') || localStorage.getItem('kp_user') || ''
  return {
    ...(authToken ? { Authorization: `Bearer ${authToken}` } : {}),
    ...(userId ? { 'X-User-Id': userId } : {})
  }
}

async function requestAuth(url, options = {}) {
  const response = await fetch(url, {
    ...options,
    headers: {
      ...createAuthHeaders(),
      ...(options.headers || {})
    }
  })

  const data = await response.json().catch(() => ({}))
  if (!response.ok || data?.ok === false) {
    throw new Error(data?.message || '认证请求失败')
  }
  return data
}

export function logout() {
  return requestAuth('/auth/logout', { method: 'POST' })
}

export function listSessions() {
  return requestAuth('/auth/sessions')
}

export function revokeSession(sessionId) {
  return requestAuth(`/auth/sessions/${encodeURIComponent(sessionId)}/revoke`, {
    method: 'POST'
  })
}
