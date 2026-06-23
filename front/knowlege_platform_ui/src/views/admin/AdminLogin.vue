<template>
  <div class="admin-login">
    <div class="login-card">
      <div class="login-logo">
        <svg viewBox="0 0 40 40" fill="none" xmlns="http://www.w3.org/2000/svg">
          <circle cx="20" cy="20" r="20" fill="#4f6ef7"/>
          <path d="M13 20l5 5 9-10" stroke="#fff" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/>
        </svg>
      </div>
      <h2>管理员平台</h2>
      <p class="subtitle">知识库多智能体平台后台管理</p>

      <form @submit.prevent="handleLogin">
        <div class="field">
          <label>管理员令牌</label>
          <input
            v-model="token"
            type="password"
            placeholder="请输入 APP_API_KEY"
            autocomplete="current-password"
          />
        </div>
        <p v-if="error" class="error-msg">{{ error }}</p>
        <button type="submit" :disabled="loading" class="btn-primary">
          {{ loading ? '验证中…' : '进入管理台' }}
        </button>
      </form>
    </div>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { adminApi } from '@/api/admin.js'

const router = useRouter()
const token = ref('')
const loading = ref(false)
const error = ref('')

async function handleLogin() {
  error.value = ''
  if (!token.value.trim()) {
    error.value = '请输入管理员令牌'
    return
  }
  loading.value = true
  // 临时写入 localStorage 让 adminApi 能读到
  localStorage.setItem('adminToken', token.value.trim())
  try {
    await adminApi.getStats()
    router.push('/admin/dashboard')
  } catch (e) {
    localStorage.removeItem('adminToken')
    error.value = e.message || '令牌错误，请重试'
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.admin-login {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  background: linear-gradient(135deg, #f0f4ff 0%, #e8eeff 100%);
}
.login-card {
  background: #fff;
  border-radius: 16px;
  padding: 48px 40px 40px;
  width: 380px;
  box-shadow: 0 8px 40px rgba(79,110,247,.12);
  text-align: center;
}
.login-logo svg {
  width: 56px;
  height: 56px;
  margin-bottom: 16px;
}
h2 {
  font-size: 22px;
  font-weight: 700;
  color: #1a1a2e;
  margin: 0 0 6px;
}
.subtitle {
  font-size: 13px;
  color: #8b93a7;
  margin: 0 0 32px;
}
.field {
  text-align: left;
  margin-bottom: 16px;
}
.field label {
  display: block;
  font-size: 13px;
  font-weight: 600;
  color: #4a5568;
  margin-bottom: 6px;
}
.field input {
  width: 100%;
  padding: 10px 14px;
  border: 1.5px solid #e2e8f0;
  border-radius: 8px;
  font-size: 14px;
  outline: none;
  box-sizing: border-box;
  transition: border-color .2s;
}
.field input:focus {
  border-color: #4f6ef7;
}
.error-msg {
  font-size: 13px;
  color: #e53e3e;
  margin: 0 0 12px;
  text-align: left;
}
.btn-primary {
  width: 100%;
  padding: 12px;
  background: #4f6ef7;
  color: #fff;
  border: none;
  border-radius: 8px;
  font-size: 15px;
  font-weight: 600;
  cursor: pointer;
  transition: background .2s;
  margin-top: 4px;
}
.btn-primary:hover:not(:disabled) {
  background: #3a57e8;
}
.btn-primary:disabled {
  opacity: .6;
  cursor: not-allowed;
}
</style>
