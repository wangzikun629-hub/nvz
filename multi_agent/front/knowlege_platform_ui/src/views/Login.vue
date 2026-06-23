<template>
  <div class="login-shell">
    <div class="login-panel">
      <div class="login-copy">
        <p class="eyebrow">诺唯赞</p>
        <h1>知识库控制台</h1>
        <p class="lead">管理知识文档、发起项目问答，AI 自动分析生信实验数据。</p>
        <div class="hero-cards">
          <article>
            <strong>知识库管理</strong>
            <p>上传、分类、检索实验相关文档与参考资料。</p>
          </article>
          <article>
            <strong>项目问答</strong>
            <p>绑定项目后，AI 实时解读 QC 数据并给出诊断建议。</p>
          </article>
        </div>
      </div>

      <div class="login-card">
        <img class="brand-logo" src="/vazyme-mark.png" alt="Vazyme" />
        <p class="card-tag">内部访问</p>
        <h2>{{ mode === 'login' ? '登录' : '注册' }}</h2>

        <label class="field-label">用户名</label>
        <input
          v-model="form.username"
          class="text-input"
          type="text"
          placeholder="请输入用户名"
          @keyup.enter="mode === 'login' ? doLogin() : doRegister()"
        />

        <label class="field-label">密码</label>
        <input
          v-model="form.password"
          class="text-input"
          type="password"
          placeholder="请输入密码（至少 6 位）"
          @keyup.enter="mode === 'login' ? doLogin() : doRegister()"
        />

        <template v-if="mode === 'register'">
          <label class="field-label">确认密码</label>
          <input
            v-model="form.password2"
            class="text-input"
            type="password"
            placeholder="再次输入密码"
            @keyup.enter="doRegister"
          />
        </template>

        <p v-if="errorMsg" class="msg error">{{ errorMsg }}</p>
        <p v-if="successMsg" class="msg success">{{ successMsg }}</p>

        <button class="submit-btn" :disabled="loading" @click="mode === 'login' ? doLogin() : doRegister()">
          {{ loading ? '请稍候…' : (mode === 'login' ? '登录' : '注册账号') }}
        </button>

        <div class="auth-switch">
          <template v-if="mode === 'login'">
            没有账号？<span @click="switchMode('register')">立即注册</span>
          </template>
          <template v-else>
            已有账号？<span @click="switchMode('login')">返回登录</span>
          </template>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { reactive, ref } from 'vue'
import { useRouter } from 'vue-router'

const router = useRouter()
const mode = ref('login')
const loading = ref(false)
const errorMsg = ref('')
const successMsg = ref('')

const form = reactive({ username: '', password: '', password2: '' })

const switchMode = (m) => {
  mode.value = m
  errorMsg.value = ''
  successMsg.value = ''
  form.username = ''
  form.password = ''
  form.password2 = ''
}

const doLogin = async () => {
  errorMsg.value = ''
  successMsg.value = ''
  if (!form.username.trim() || !form.password) {
    errorMsg.value = '请填写用户名和密码'
    return
  }
  loading.value = true
  try {
    const res = await fetch('/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username: form.username.trim(), password: form.password }),
    })
    const data = await res.json()
    if (!data.ok) {
      errorMsg.value = data.message || '用户名或密码错误'
      return
    }
    localStorage.setItem('kp_user', data.username)
    localStorage.setItem('kp_user_id', data.userId || data.username)
    localStorage.setItem('kp_auth_token', data.authToken || '')
    localStorage.setItem('kp_is_admin', data.isAdmin ? '1' : '0')
    router.replace('/')
  } catch {
    errorMsg.value = '网络错误，请稍后重试'
  } finally {
    loading.value = false
  }
}

const doRegister = async () => {
  errorMsg.value = ''
  successMsg.value = ''
  if (!form.username.trim() || !form.password) {
    errorMsg.value = '请填写用户名和密码'
    return
  }
  if (form.password !== form.password2) {
    errorMsg.value = '两次密码不一致'
    return
  }
  loading.value = true
  try {
    const res = await fetch('/auth/register', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username: form.username.trim(), password: form.password }),
    })
    const data = await res.json()
    if (!data.ok) {
      errorMsg.value = data.message || '注册失败'
      return
    }
    successMsg.value = '注册成功！请登录'
    switchMode('login')
  } catch {
    errorMsg.value = '网络错误，请稍后重试'
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.login-shell {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 100vh;
  background-color: #0d1117;
  background-image: radial-gradient(#2d333b 1px, transparent 1px);
  background-size: 30px 30px;
  padding: 40px 24px;
  box-sizing: border-box;
}

.login-panel {
  display: flex;
  gap: 64px;
  align-items: center;
  max-width: 960px;
  width: 100%;
}

.login-copy {
  flex: 1;
  color: #c9d1d9;
}

.eyebrow {
  margin: 0 0 10px;
  font-size: 12px;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: #94a3b8;
}

.login-copy h1 {
  margin: 0 0 16px;
  font-size: 32px;
  font-weight: 700;
  color: #e6edf3;
}

.lead {
  color: #94a3b8;
  font-size: 15px;
  line-height: 1.6;
  margin: 0 0 32px;
}

.hero-cards {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.hero-cards article {
  background: rgba(255, 255, 255, 0.04);
  border: 1px solid #30363d;
  border-radius: 10px;
  padding: 16px 20px;
}

.hero-cards article strong {
  display: block;
  color: #e6edf3;
  font-size: 14px;
  margin-bottom: 6px;
}

.hero-cards article p {
  margin: 0;
  font-size: 13px;
  color: #8b949e;
  line-height: 1.5;
}

.login-card {
  width: 340px;
  flex-shrink: 0;
  background: rgba(255, 255, 255, 0.04);
  border: 1px solid #30363d;
  border-radius: 16px;
  padding: 36px 32px;
  display: flex;
  flex-direction: column;
}

.brand-logo {
  width: 48px;
  height: 48px;
  object-fit: contain;
  margin-bottom: 12px;
}

.card-tag {
  margin: 0 0 4px;
  font-size: 11px;
  letter-spacing: 0.1em;
  text-transform: uppercase;
  color: #6366f1;
}

.login-card h2 {
  margin: 0 0 24px;
  font-size: 22px;
  font-weight: 700;
  color: #e6edf3;
}

.field-label {
  display: block;
  font-size: 13px;
  color: #94a3b8;
  margin-bottom: 6px;
  margin-top: 14px;
}

.field-label:first-of-type {
  margin-top: 0;
}

.text-input {
  width: 100%;
  box-sizing: border-box;
  background: rgba(255, 255, 255, 0.06);
  border: 1px solid #30363d;
  border-radius: 8px;
  color: #e6edf3;
  font-size: 14px;
  padding: 10px 12px;
  outline: none;
  transition: border-color 0.2s;
}

.text-input:focus {
  border-color: #6366f1;
}

.msg {
  font-size: 13px;
  margin: 10px 0 0;
}

.error { color: #f87171; }
.success { color: #4ade80; }

.submit-btn {
  margin-top: 20px;
  width: 100%;
  padding: 11px 0;
  background: #6366f1;
  border: none;
  border-radius: 8px;
  color: #fff;
  font-size: 15px;
  font-weight: 600;
  cursor: pointer;
  transition: background 0.2s, opacity 0.2s;
}

.submit-btn:hover:not(:disabled) { background: #4f46e5; }
.submit-btn:disabled { opacity: 0.5; cursor: not-allowed; }

.auth-switch {
  margin-top: 16px;
  text-align: center;
  font-size: 13px;
  color: #94a3b8;
}

.auth-switch span {
  color: #6366f1;
  cursor: pointer;
  margin-left: 4px;
  text-decoration: underline;
}

.auth-switch span:hover { color: #818cf8; }

@media (max-width: 720px) {
  .login-panel { flex-direction: column; gap: 32px; }
  .login-copy { display: none; }
  .login-card { width: 100%; }
}
</style>
