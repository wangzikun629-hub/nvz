<template>
  <div class="chat-page">
    <div class="chat-box">
      <div class="chat-header">
        <div class="header-copy">
          <h2>项目智能问答</h2>
          <p>多轮对话会在当前窗口中持续关联当前项目。</p>
        </div>
        <div class="header-tools">
          <div class="session-meta">
            <span class="meta-label">会话</span>
            <code>{{ shortSessionId }}</code>
          </div>
          <el-button
            class="clear-btn"
            text
            type="warning"
            :disabled="!projectContext.project_context_locked"
            @click="handleClearProjectContext"
          >
            清除项目
          </el-button>
        </div>
      </div>

      <div v-if="projectContext.project_context_locked" class="project-banner">
        <div class="project-banner-copy">
          <span class="project-label">当前项目</span>
          <strong>{{ projectContext.active_project_id || '-' }}</strong>
          <span class="project-source">{{ formatProjectSource(projectContext.project_context_source) }}</span>
        </div>
        <div class="project-path" :title="projectContext.active_project_root || ''">
          {{ projectContext.active_project_root || '暂无项目路径' }}
        </div>
      </div>

      <div v-else class="project-banner idle">
        <div class="project-banner-copy">
          <span class="project-label">当前项目</span>
          <strong>未锁定</strong>
        </div>
        <div class="project-path">提及一次项目后，本窗口后续对话将持续使用该项目。</div>
      </div>

      <div v-if="canContinueFollowup" class="followup-panel">
        <div class="followup-copy">
          <strong>可继续下一步</strong>
          <p>{{ followupSummary }}</p>
        </div>
        <el-button
          size="small"
          type="primary"
          class="followup-btn"
          :loading="loading"
          @click="continueFollowup"
        >
          继续排查
        </el-button>
      </div>

      <div v-if="projectContext.pending_project_confirmation" class="project-confirmation">
        <div class="confirmation-copy">
          <strong>需要确认匹配的项目</strong>
          <p>请选择候选项目，或使用准确的项目名称重新提问。</p>
        </div>
        <div class="candidate-list">
          <el-button
            v-for="candidate in projectCandidates"
            :key="candidate.project_id"
            size="small"
            class="candidate-btn"
            @click="confirmProjectCandidate(candidate)"
          >
            {{ candidate.project_id }}
          </el-button>
        </div>
      </div>

      <div ref="messagesRef" class="messages">
        <div v-if="messages.length === 0" class="empty-state">
          <el-icon :size="60" color="#30363d"><ChatDotRound /></el-icon>
          <p>可以先输入项目名称，也可以直接提出通用问题。</p>
        </div>

        <div
          v-for="(msg, index) in messages"
          :key="index"
          class="message-item"
          :class="msg.role"
        >
          <div class="avatar">
            <el-avatar
              :icon="msg.role === 'user' ? User : Service"
              :style="{ backgroundColor: msg.role === 'user' ? '#409eff' : '#10b981' }"
            />
          </div>
          <div class="content">
            <div class="bubble">
              <div v-if="msg.loading" class="typing-indicator">
                <div v-if="msg.processSteps?.length" class="process-steps">
                  <div
                    v-for="(step, stepIndex) in msg.processSteps"
                    :key="`${step.stage}-${stepIndex}`"
                    class="process-step"
                    :class="step.status"
                  >
                    <span class="step-dot"></span>
                    <span class="step-text">{{ step.text }}</span>
                  </div>
                </div>
                <span></span><span></span><span></span>
                <em v-if="msg.statusText">{{ msg.statusText }}</em>
              </div>
              <template v-else>
                <div v-html="formatContent(msg.content)"></div>
                <div v-if="msg.projectId" class="message-project-tag">
                  项目：{{ msg.projectId }}
                </div>
              </template>
            </div>
          </div>
        </div>
      </div>

      <div class="input-area">
        <div class="input-wrapper">
          <el-input
            v-model="input"
            placeholder="输入问题，提及项目后窗口自动关联。Enter 发送 / Shift+Enter 换行"
            :rows="3"
            type="textarea"
            resize="none"
            @keydown.enter.exact.prevent="handleSend"
          />
          <div class="input-footer">
            <span class="input-hint">Shift+Enter 换行</span>
            <el-button
              type="primary"
              class="send-btn"
              :loading="loading"
              :disabled="!input.trim()"
              @click="handleSend"
            >
              <el-icon><Position /></el-icon>
              发送
            </el-button>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, nextTick, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { User, Service, Position, ChatDotRound } from '@element-plus/icons-vue'
import { marked } from 'marked'

import {
  clearProjectContext,
  getProjectContext,
  getSessionMessages,
  streamQueryKnowledge
} from '@/api/knowledge'

const STORAGE_USER_KEY = 'kefu.chat.user_id'
const STORAGE_SESSION_KEY = 'kefu.chat.session_id'

const input = ref('')
const loading = ref(false)
const messages = ref([])
const messagesRef = ref(null)
const userId = ref(localStorage.getItem('kp_user_id') || localStorage.getItem(STORAGE_USER_KEY) || 'chat_user')
const sessionId = ref(localStorage.getItem(STORAGE_SESSION_KEY) || `chat_${Date.now()}`)
const projectContext = ref({
  active_project_id: null,
  active_project_root: null,
  project_context_locked: false,
  project_context_source: null,
  recent_project_questions: [],
  pending_project_confirmation: null,
  pending_followup_action: null,
  last_identified_at: null
})

localStorage.setItem(STORAGE_USER_KEY, userId.value)
localStorage.setItem(STORAGE_SESSION_KEY, sessionId.value)

const shortSessionId = computed(() => sessionId.value.slice(-12))
const projectCandidates = computed(() => projectContext.value.pending_project_confirmation?.candidates || [])
const canContinueFollowup = computed(() => {
  const action = projectContext.value.pending_followup_action
  return Boolean(
    projectContext.value.project_context_locked &&
    action &&
    Array.isArray(action.actions) &&
    action.actions.length > 0
  )
})
const followupSummary = computed(() => {
  const action = projectContext.value.pending_followup_action
  if (!action) {
    return ''
  }
  return action.summary || action.actions?.slice(0, 2).join('；') || ''
})

function scrollToBottom() {
  nextTick(() => {
    if (messagesRef.value) {
      messagesRef.value.scrollTop = messagesRef.value.scrollHeight
    }
  })
}

function formatContent(text) {
  return marked(text || '')
}

function formatProjectSource(source) {
  const mapping = {
    user_explicit: '用户指定',
    active_context: '窗口上下文',
    request: '请求指定',
    question: '问题匹配',
    session_memory: '会话记忆',
    inferred: '推断'
  }
  return mapping[source] || '上下文'
}

function updateProjectContext(payload) {
  projectContext.value = {
    ...projectContext.value,
    ...(payload || {})
  }
}

function normalizeMessages(rawMessages) {
  return (rawMessages || [])
    .filter((item) => item && (item.role === 'user' || item.role === 'assistant'))
    .map((item) => ({
      role: item.role,
      content: typeof item.content === 'string' ? item.content : JSON.stringify(item.content || '', null, 2),
      loading: false
    }))
}

function stripHtml(text) {
  return (text || '').replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').trim()
}

function applyProcessPacket(message, text) {
  let payload = null
  try {
    payload = JSON.parse(text)
  } catch {
    payload = null
  }

  if (payload?.type === 'project_stage') {
    if (!Array.isArray(message.processSteps)) {
      message.processSteps = []
    }

    const nextStep = {
      stage: payload.stage || `step_${message.processSteps.length + 1}`,
      status: payload.status || 'in_progress',
      text: payload.text || '处理中...'
    }

    const existingIndex = message.processSteps.findIndex((item) => item.stage === nextStep.stage)
    if (existingIndex >= 0) {
      message.processSteps.splice(existingIndex, 1, nextStep)
    } else {
      message.processSteps.push(nextStep)
    }
    message.statusText = nextStep.text
    return
  }

  message.statusText = stripHtml(text) || '处理中...'
}

function buildProjectContextEvent(previousContext, currentContext) {
  const previousProjectId = previousContext?.active_project_id
  const currentProjectId = currentContext?.active_project_id
  const previousLocked = Boolean(previousContext?.project_context_locked)
  const currentLocked = Boolean(currentContext?.project_context_locked)

  if (!previousLocked && currentLocked && currentProjectId) {
    return {
      type: 'project_bound',
      message: `当前窗口已关联到项目 ${currentProjectId}。`
    }
  }

  if (
    previousLocked &&
    currentLocked &&
    previousProjectId &&
    currentProjectId &&
    previousProjectId !== currentProjectId
  ) {
    return {
      type: 'project_switched',
      message: `项目上下文已从 ${previousProjectId} 切换为 ${currentProjectId}。`
    }
  }

  if (previousLocked && !currentLocked) {
    return {
      type: 'project_cleared',
      message: `已清除 ${previousProjectId || '当前窗口'} 的项目上下文。`
    }
  }

  return null
}

function showProjectContextEvent(event) {
  if (!event?.message) {
    return
  }
  if (event.type === 'project_cleared') {
    ElMessage.warning(event.message)
    return
  }
  ElMessage.success(event.message)
}

async function refreshProjectContext() {
  const res = await getProjectContext({
    user_id: userId.value,
    session_id: sessionId.value
  })
  updateProjectContext(res.project_context)
}

async function refreshSessionMessages() {
  const res = await getSessionMessages({
    user_id: userId.value,
    session_id: sessionId.value
  })
  messages.value = normalizeMessages(res.messages)
  scrollToBottom()
}

async function handleClearProjectContext() {
  const res = await clearProjectContext({
    user_id: userId.value,
    session_id: sessionId.value
  })
  updateProjectContext(res.project_context)
  showProjectContextEvent(res.project_context_event)
}

async function runQuestion(question, projectId = null) {
  const boundProjectId = projectContext.value.active_project_id || projectId || null
  const assistantMessage = {
    role: 'assistant',
    content: '',
    loading: true,
    statusText: '正在发起请求...',
    processSteps: []
  }

  messages.value.push({ role: 'user', content: question, projectId: boundProjectId })
  messages.value.push(assistantMessage)
  scrollToBottom()

  loading.value = true
  try {
    let answerBuffer = ''
    const previousProjectContext = { ...projectContext.value }

    await streamQueryKnowledge({
      question,
      user_id: userId.value,
      session_id: sessionId.value,
      mode: 'agent',
      project_id: projectId || undefined
    }, {
      onPacket(packet) {
        const content = packet?.content || {}
        if (content.contentType === 'sagegpt/finish') {
          return
        }

        const kind = content.kind
        const text = content.text || ''
        if (kind === 'ANSWER') {
          answerBuffer += text
          assistantMessage.loading = false
          assistantMessage.content = answerBuffer
          assistantMessage.statusText = ''
        } else if (kind === 'PROCESS' || kind === 'THINKING') {
          if (!answerBuffer) {
            applyProcessPacket(assistantMessage, text)
          }
        }
        scrollToBottom()
      }
    })

    if (!answerBuffer.trim()) {
      assistantMessage.loading = false
      assistantMessage.content = assistantMessage.statusText || '未返回答案。'
      assistantMessage.statusText = ''
    }

    await refreshProjectContext()
    const currentProjectContext = { ...projectContext.value }
    showProjectContextEvent(buildProjectContextEvent(previousProjectContext, currentProjectContext))
    assistantMessage.projectId = currentProjectContext.active_project_id || null
  } catch (error) {
    assistantMessage.role = 'assistant'
    assistantMessage.content = error.message || '请求失败，请重试。'
    assistantMessage.loading = false
    assistantMessage.statusText = ''
  } finally {
    loading.value = false
    scrollToBottom()
  }
}

async function handleSend() {
  if (!input.value.trim() || loading.value) {
    return
  }

  const question = input.value.trim()
  input.value = ''
  await runQuestion(question)
}

async function continueFollowup() {
  if (!canContinueFollowup.value || loading.value) {
    return
  }
  await runQuestion('继续排查')
}

async function confirmProjectCandidate(candidate) {
  if (!candidate?.project_id || loading.value) {
    return
  }
  const confirmText = `后续这个窗口都按项目 ${candidate.project_id} 处理`
  await runQuestion(confirmText, candidate.project_id)
}

onMounted(async () => {
  try {
    await Promise.all([refreshProjectContext(), refreshSessionMessages()])
  } catch (error) {
    console.error(error)
  }
})
</script>

<style lang="scss" scoped>
.chat-page {
  height: calc(100vh - 40px);
}

.chat-box {
  height: 100%;
  background-color: #161b22;
  border: 1px solid #30363d;
  border-radius: 8px;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.chat-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 16px;
  padding: 16px 20px;
  border-bottom: 1px solid #30363d;
  background-color: #0d1117;

  h2 {
    margin: 0 0 4px;
    color: #fff;
    font-size: 18px;
  }

  p {
    margin: 0;
    color: #8b949e;
    font-size: 12px;
  }
}

.header-copy {
  min-width: 0;
}

.header-tools {
  display: flex;
  align-items: center;
  gap: 12px;
}

.session-meta {
  display: flex;
  align-items: center;
  gap: 8px;
  color: #8b949e;
  font-size: 12px;

  code {
    background-color: #111827;
    color: #c9d1d9;
    border: 1px solid #30363d;
    border-radius: 6px;
    padding: 3px 6px;
  }
}

.meta-label,
.project-label {
  text-transform: uppercase;
  letter-spacing: 0;
  font-size: 11px;
}

.project-banner,
.project-confirmation,
.followup-panel {
  padding: 12px 20px;
  border-bottom: 1px solid #30363d;
  background-color: #111827;
}

.project-banner.idle {
  background-color: #0f172a;
}

.project-banner-copy,
.followup-panel {
  display: flex;
  align-items: center;
  gap: 10px;
  color: #c9d1d9;
}

.project-source {
  font-size: 12px;
  color: #8b949e;
}

.project-path {
  margin-top: 6px;
  color: #8b949e;
  font-size: 12px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.followup-panel {
  justify-content: space-between;
  gap: 16px;
}

.followup-copy {
  min-width: 0;

  p {
    margin: 6px 0 0;
    color: #8b949e;
    font-size: 12px;
  }
}

.followup-btn {
  flex-shrink: 0;
}

.confirmation-copy {
  p {
    margin: 6px 0 0;
    color: #8b949e;
    font-size: 12px;
  }
}

.candidate-list {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 10px;
}

.candidate-btn {
  margin: 0;
}

.messages {
  flex: 1;
  padding: 20px;
  overflow-y: auto;

  .empty-state {
    height: 100%;
    display: flex;
    flex-direction: column;
    justify-content: center;
    align-items: center;
    color: #8b949e;

    p {
      margin-top: 20px;
    }
  }
}

.message-item {
  display: flex;
  margin-bottom: 20px;

  &.user {
    flex-direction: row-reverse;

    .content {
      align-items: flex-end;

      .bubble {
        background-color: #409eff;
        color: #fff;
        border-top-right-radius: 0;
      }
    }

    .avatar {
      margin-left: 10px;
      margin-right: 0;
    }
  }

  &.assistant {
    .content {
      align-items: flex-start;

      .bubble {
        background-color: #1f242d;
        color: #c9d1d9;
        border: 1px solid #30363d;
        border-top-left-radius: 0;
      }
    }

    .avatar {
      margin-right: 10px;
    }
  }
}

.content {
  display: flex;
  flex-direction: column;
  max-width: 76%;

  .bubble {
    padding: 10px 15px;
    border-radius: 12px;
    line-height: 1.5;
    font-size: 14px;
    word-break: break-word;

    :deep(p) {
      margin: 0 0 10px 0;

      &:last-child {
        margin-bottom: 0;
      }
    }

    :deep(a) {
      color: #58a6ff;
      text-decoration: none;

      &:hover {
        text-decoration: underline;
      }
    }

    :deep(ul),
    :deep(ol) {
      padding-left: 20px;
      margin: 5px 0;
    }

    :deep(code) {
      background-color: rgba(110, 118, 129, 0.4);
      padding: 0.2em 0.4em;
      border-radius: 6px;
      font-family: monospace;
    }
  }
}

.message-project-tag {
  margin-top: 8px;
  color: #8b949e;
  font-size: 11px;
}

.input-area {
  padding: 16px 20px;
  background-color: #0d1117;
  border-top: 1px solid #30363d;
}

.input-wrapper {
  display: flex;
  flex-direction: column;
  gap: 0;
  border: 1px solid #30363d;
  border-radius: 12px;
  overflow: hidden;
  transition: border-color 0.2s;

  &:focus-within {
    border-color: #409eff;
  }

  :deep(.el-textarea__inner) {
    background-color: #161b22;
    border: none;
    border-radius: 0;
    color: #c9d1d9;
    box-shadow: none;
    padding: 12px 14px;
    font-size: 14px;
    line-height: 1.6;
    resize: none;

    &:focus {
      box-shadow: none;
    }
  }
}

.input-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 10px 8px 14px;
  background: #111827;
  border-top: 1px solid #1e2d40;
}

.input-hint {
  font-size: 11px;
  color: #334155;
}

.send-btn {
  padding: 6px 18px;
  height: auto;
  border-radius: 8px;
}

.typing-indicator {
  display: flex;
  align-items: flex-start;
  flex-direction: column;
  gap: 4px;

  .process-steps {
    width: 100%;
    display: flex;
    flex-direction: column;
    gap: 6px;
    margin-bottom: 6px;
  }

  .process-step {
    display: flex;
    align-items: center;
    gap: 8px;
    color: #8b949e;
    font-size: 12px;
    line-height: 1.4;

    .step-dot {
      width: 8px;
      height: 8px;
      border-radius: 999px;
      background-color: #6b7280;
      flex: 0 0 auto;
    }

    &.completed .step-dot {
      background-color: #10b981;
    }

    &.in_progress .step-dot {
      background-color: #409eff;
    }
  }

  > span {
    display: inline-block;
    width: 6px;
    height: 6px;
    background-color: #8b949e;
    border-radius: 50%;
    margin: 0 2px;
    animation: bounce 1.4s infinite ease-in-out both;

    &:nth-child(1) {
      animation-delay: -0.32s;
    }

    &:nth-child(2) {
      animation-delay: -0.16s;
    }
  }

  em {
    margin-left: 8px;
    font-style: normal;
    color: #8b949e;
    font-size: 12px;
    line-height: 1.4;
  }
}

@keyframes bounce {
  0%,
  80%,
  100% {
    transform: scale(0);
  }

  40% {
    transform: scale(1);
  }
}
</style>
