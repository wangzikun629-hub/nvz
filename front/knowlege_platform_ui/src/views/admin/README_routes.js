/**
 * 将以下路由配置合并到你的 Vue Router（router/index.js 或 router.js）
 *
 * 用法：
 *   import { adminRoutes } from '@/views/admin/README_routes.js'
 *   const router = createRouter({ routes: [...yourRoutes, ...adminRoutes] })
 */

export const adminRoutes = [
  {
    path: '/admin/login',
    name: 'AdminLogin',
    component: () => import('@/views/admin/AdminLogin.vue'),
    meta: { title: '管理员登录', layout: 'blank' },
  },
  {
    path: '/admin/dashboard',
    name: 'AdminDashboard',
    component: () => import('@/views/admin/AdminDashboard.vue'),
    meta: { title: '成员管理', requiresAdmin: true },
    beforeEnter: (_to, _from, next) => {
      // 简单鉴权：未登录则跳回登录页
      if (!localStorage.getItem('adminToken')) {
        next('/admin/login')
      } else {
        next()
      }
    },
  },
  {
    // 访问 /admin 直接跳到 dashboard
    path: '/admin',
    redirect: '/admin/dashboard',
  },
]
