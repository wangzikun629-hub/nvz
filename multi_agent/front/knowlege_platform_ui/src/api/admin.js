import service from './request'

/**
 * 管理员 API
 * 鉴权直接复用 request.js 的 Authorization Bearer 拦截器，无需额外 token。
 */
export const adminApi = {
  /** 平台概览统计 */
  getStats() {
    return service.get('/admin/stats')
  },
  /** 所有成员列表（含 is_admin、活跃会话数、对话数） */
  listUsers() {
    return service.get('/admin/users')
  },
  /** 某成员的所有对话 */
  getUserConversations(userId) {
    return service.get(`/admin/users/${userId}/conversations`)
  },
  /** 重置成员密码 */
  resetPassword(userId, newPassword) {
    return service.put(`/admin/users/${userId}/password`, { new_password: newPassword })
  },
  /** 设置/取消管理员权限 */
  setAdminStatus(userId, isAdmin) {
    return service.put(`/admin/users/${userId}/admin-status`, { is_admin: isAdmin })
  },
  /** 删除成员 */
  deleteUser(userId) {
    return service.delete(`/admin/users/${userId}`)
  },
}
