import { createRouter, createWebHistory } from 'vue-router'
import Layout from '@/layout/index.vue'

const routes = [
  {
    path: '/login',
    name: 'Login',
    component: () => import('@/views/Login.vue'),
    meta: { public: true }
  },
  {
    path: '/',
    component: Layout,
    redirect: '/knowledge',
    children: [
      {
        path: 'knowledge',
        name: 'Knowledge',
        component: () => import('@/views/Knowledge.vue'),
        meta: { title: '知识库', icon: 'Files' }
      },
      {
        path: 'kb-chat',
        name: 'KbChat',
        component: () => import('@/views/KbChat.vue'),
        meta: { title: '知识库问答', icon: 'ChatLineRound' }
      },
      {
        path: 'admin',
        name: 'AdminDashboard',
        component: () => import('@/views/admin/AdminDashboard.vue'),
        meta: { title: '成员管理', icon: 'UserFilled', adminOnly: true }
      }
    ]
  }
]

const router = createRouter({
  history: createWebHistory(),
  routes
})

// 导航守卫：未登录跳转 /login
router.beforeEach((to) => {
  const isLoggedIn = !!localStorage.getItem('kp_user')
  if (!to.meta.public && !isLoggedIn) {
    return { name: 'Login' }
  }
  if (to.name === 'Login' && isLoggedIn) {
    return { path: '/' }
  }
})

export default router
