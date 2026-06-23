import service from './request'

/**
 * 管理员 API
 * 所有请求自动带上 X-Admin-Token（来自 localStorage kp_admin_token）
 */
function adminService(method, url, data) {
  const token = localStorage.getItem('kp_admin_token') || ''
  return service({
    method,
    url,
    data: method !== 'get' ? data : undefined,
    headers: { 'X-Admin-Token': token },
  })
}

export const adminApi = {
  /** 平台概览统计 */
  getStats() {
    return adminService('get', '/admin/stats')
  },
  /** 所有成员列表（含活跃会话数、对话数） */
  listUsers() {
    return adminService('get', '/admin/users')
  },
  /** 某成员的所有对话 */
  getUserConversations(userId) {
    return adminService('get', `/admin/users/${userId}/conversations`)
  },
  /** 重置成员密码 */
  resetPassword(userId, newPassword) {
    return adminService('put', `/admin/users/${userId}/password`, { new_password: newPassword })
  },
  /** 删除成员 */
  deleteUser(userId) {
    return adminService('delete', `/admin/users/${userId}`)
  },
}
