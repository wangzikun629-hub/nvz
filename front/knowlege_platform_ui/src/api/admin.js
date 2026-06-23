/**
 * 管理员 API 封装
 * 所有请求需在请求头中携带 X-Admin-Token
 */

const BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000'

function getToken() {
  return localStorage.getItem('adminToken') || ''
}

async function request(method, path, body) {
  const res = await fetch(`${BASE_URL}${path}`, {
    method,
    headers: {
      'Content-Type': 'application/json',
      'X-Admin-Token': getToken(),
    },
    body: body ? JSON.stringify(body) : undefined,
  })
  const data = await res.json()
  if (!res.ok || !data.ok) {
    throw new Error(data.message || `请求失败 (${res.status})`)
  }
  return data
}

export const adminApi = {
  /** 获取平台统计概览 */
  getStats() {
    return request('GET', '/admin/stats')
  },

  /** 获取所有用户列表 */
  listUsers() {
    return request('GET', '/admin/users')
  },

  /** 获取指定用户的对话列表 */
  getUserConversations(userId) {
    return request('GET', `/admin/users/${userId}/conversations`)
  },

  /** 重置指定用户密码 */
  resetPassword(userId, newPassword) {
    return request('PUT', `/admin/users/${userId}/password`, { new_password: newPassword })
  },

  /** 删除用户 */
  deleteUser(userId) {
    return request('DELETE', `/admin/users/${userId}`)
  },
}
