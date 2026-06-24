<template>
  <div class="app-shell">
    <div class="backdrop-grid"></div>

    <section v-if="!isLoggedIn" class="login-shell">
      <div class="login-panel">
        <div class="login-copy">
          <p class="eyebrow">诺唯赞</p>
          <h1>诺唯赞智能助手</h1>
          <p class="lead">
            面向测序项目的 AI 分析平台，支持项目质控解读、实验问题排查与专业知识问答。
          </p>

          <div class="hero-cards">
            <article>
              <strong>项目分析</strong>
              <p>自动读取测序项目结果，提取关键质控指标，输出结构化分析报告。</p>
            </article>
            <article>
              <strong>问题排查</strong>
              <p>结合项目数据与实验背景，辅助定位 FRiP、比对率、重复率等异常原因。</p>
            </article>
            <article>
              <strong>知识问答</strong>
              <p>覆盖 CUT&amp;Tag、ChIP-seq、ATAC-seq 等实验原理、方法与文献解读。</p>
            </article>
          </div>
        </div>

        <div class="login-card">
          <img class="brand-logo" src="/vazyme-mark.png" alt="Vazyme Mark" />
          <p class="card-tag">安全登录</p>
          <h2>{{ authMode === 'login' ? '登录' : '注册' }}</h2>

          <label class="field-label" for="username">用户名</label>
          <input
            id="username"
            v-model="username"
            class="text-input"
            type="text"
            placeholder="请输入用户名"
            @keyup.enter="authMode === 'login' ? handleLogin() : handleRegister()"
          />

          <label class="field-label" for="password">密码</label>
          <input
            id="password"
            v-model="password"
            class="text-input"
            type="password"
            placeholder="请输入密码（至少 6 位）"
            @keyup.enter="authMode === 'login' ? handleLogin() : handleRegister()"
          />

          <template v-if="authMode === 'register'">
            <label class="field-label" for="password2">确认密码</label>
            <input
              id="password2"
              v-model="password2"
              class="text-input"
              type="password"
              placeholder="再次输入密码"
              @keyup.enter="handleRegister"
            />
          </template>

          <p v-if="loginError" class="error-text">{{ loginError }}</p>
          <p v-if="loginSuccess" class="success-text">{{ loginSuccess }}</p>

          <button v-if="authMode === 'login'" class="primary-button wide" :disabled="authLoading" @click="handleLogin">
            {{ authLoading ? '登录中…' : '登录' }}
          </button>
          <button v-else class="primary-button wide" :disabled="authLoading" @click="handleRegister">
            {{ authLoading ? '注册中…' : '注册账号' }}
          </button>

          <div class="auth-switch">
            <template v-if="authMode === 'login'">
              没有账号？<span class="switch-link" @click="switchAuthMode('register')">立即注册</span>
            </template>
            <template v-else>
              已有账号？<span class="switch-link" @click="switchAuthMode('login')">返回登录</span>
            </template>
          </div>
        </div>
      </div>
    </section>

    <section v-else class="workspace">
      <aside class="sidebar">
        <div class="sidebar-brand">
          <img class="brand-logo small" src="/vazyme-mark.png" alt="Vazyme Mark" />
          <div class="sidebar-brand-copy">
            <strong>诺唯赞智能助手</strong>
            <p>{{ currentUser }} · 内部工作台</p>
          </div>
        </div>

        <div class="sidebar-actions">
          <button class="primary-button" @click="createNewSession">新建会话</button>
          <button class="ghost-button" @click="fetchUserSessions">刷新列表</button>
          <button class="ghost-button" @click="handleLogout">退出登录</button>
        </div>

        <section class="session-section">
          <div class="section-header">
            <span>历史会话</span>
            <span class="section-count">{{ sessions.length }}</span>
          </div>

          <div v-if="!sessions.length" class="empty-side">暂无历史会话</div>

          <button
            v-for="session in sessions"
            :key="session.session_id"
            class="session-item"
            :class="{ active: session.session_id === selectedSessionId }"
            @click="selectSession(session.session_id)"
          >
            <div class="session-topline">
              <strong class="session-preview">{{ sessionPreview(session) }}</strong>
              <button class="session-delete" @click.stop="deleteSession(session.session_id)">删除</button>
            </div>
          </button>
        </section>
      </aside>

      <main class="main-panel">
        <section class="workspace-header workspace-panel" :class="{ compact: workspaceView === 'chat' && chatTimeline.length }">
          <div class="workspace-header-main">
            <div v-if="workspaceView !== 'chat' || !chatTimeline.length" class="topbar-copy">
              <span class="topbar-kicker">诺唯赞智能助手</span>
              <h2>今天想处理什么？</h2>
              <p>从项目分析、实验排查或知识库问答开始，也可以直接在下方输入问题。</p>
            </div>
            <div v-else class="header-project-summary">
              <span class="project-label">项目</span>
              <strong>{{ projectContext.project_context_locked ? (projectContext.active_project_id || '-') : '未绑定' }}</strong>
              <span
                class="project-context-state inline"
                :class="{ active: projectContext.project_context_locked }"
              >
                {{ projectContext.project_context_locked ? '已绑定' : '未绑定' }}
              </span>
              <span v-if="formatProjectSource(projectContext.project_context_source)" class="project-source">{{ formatProjectSource(projectContext.project_context_source) }}</span>
              <button
                class="ghost-button toolbar-button header-clear-button"
                :disabled="!projectContext.project_context_locked || isProcessing"
                @click="clearProjectContext"
              >
                清空项目
              </button>
            </div>
            <div class="topbar-actions">
              <span class="session-chip">会话 {{ shortSessionId }}</span>
              <div class="view-switch">
                <button class="mode-button" :class="{ active: workspaceView === 'chat' }" @click="switchWorkspaceView('chat')">对话</button>
                <button class="mode-button" :class="{ active: workspaceView === 'analysis' }" @click="switchWorkspaceView('analysis')">AI报告总结</button>
              </div>
            </div>
          </div>
        </section>

        <section v-if="workspaceView !== 'chat' || !chatTimeline.length" class="project-context-panel workspace-panel" :class="{ compact: workspaceView === 'chat' && chatTimeline.length }">
          <div v-if="workspaceView !== 'chat' || !chatTimeline.length" class="project-context-head">
            <div class="project-context-copy">
              <span class="project-context-kicker">项目上下文</span>
              <strong>当前项目绑定状态</strong>
            </div>
            <span class="project-context-state" :class="{ active: projectContext.project_context_locked }">
              {{ projectContext.project_context_locked ? '已绑定' : '未绑定' }}
            </span>
          </div>
          <div class="project-strip" :class="{ idle: !projectContext.project_context_locked, compact: workspaceView === 'chat' && chatTimeline.length }">
            <div class="project-strip-main">
              <span class="project-label">{{ workspaceView === 'chat' && chatTimeline.length ? '项目' : '当前项目' }}</span>
              <strong>{{ projectContext.project_context_locked ? (projectContext.active_project_id || '-') : '未绑定' }}</strong>
              <span
                v-if="workspaceView === 'chat' && chatTimeline.length"
                class="project-context-state inline"
                :class="{ active: projectContext.project_context_locked }"
              >
                {{ projectContext.project_context_locked ? '已绑定' : '未绑定' }}
              </span>
              <span v-if="formatProjectSource(projectContext.project_context_source)" class="project-source">{{ formatProjectSource(projectContext.project_context_source) }}</span>
            </div>
            <div class="project-strip-side">
              <span class="project-path">{{ projectContext.active_project_root || '当前会话还没有绑定项目。' }}</span>
              <div class="project-actions">
                <button
                  class="ghost-button toolbar-button"
                  :disabled="!projectContext.project_context_locked || isProcessing"
                  @click="clearProjectContext"
                >
                  清空项目
                </button>
              </div>
            </div>
          </div>
        </section>

        <section
          v-if="workspaceView === 'chat'"
          ref="messageListRef"
          class="workspace-body content-scroll"
          :class="{ 'empty-state': !chatTimeline.length }"
          @scroll="handleMessageScroll"
        >
          <div v-if="!chatTimeline.length" class="chat-empty-state">
            <strong>有什么可以帮您？</strong>
            <p>可以输入项目编号开始质控分析，也可以直接提问实验问题或知识点。</p>
          </div>

          <article
            v-for="item in chatTimeline"
            :key="item.id"
            class="timeline-item"
            :class="item.type"
            :data-message-id="item.id"
          >
            <template v-if="item.type === 'user'">
              <div class="message-card user">
                <div class="message-label"><span>用户</span></div>
                <div class="message-bubble markdown-body" v-html="renderMarkdown(item.content)"></div>
              </div>
            </template>

            <template v-else-if="item.type === 'process_bundle'">
              <div class="thinking-track" :class="processRouteType">
                <div class="thinking-head">
                  <div class="thinking-route">
                    <span class="thinking-orb" :class="{ active: isProcessing }"></span>
                    <span class="thinking-title">{{ processTitle }}</span>
                  </div>
                  <div class="thinking-right">
                    <span class="thinking-meta-count">{{ compactStageMeta }}</span>
                    <span class="thinking-badge" :class="{ active: isProcessing }">{{ processStateText }}</span>
                  </div>
                </div>

                <div v-if="stageTimeline.length" class="thinking-body">
                  <div v-if="compactStageTrail.length" class="thinking-trail">
                    <span
                      v-for="stage in compactStageTrail"
                      :key="stage.stage"
                      class="thinking-chip"
                      :class="stage.status"
                    >
                      <span class="chip-dot"></span>
                      {{ stage.label }}
                    </span>
                  </div>

                  <div v-if="compactActiveStage" class="thinking-current" :class="compactActiveStage.status">
                    <span class="current-dot" :class="compactActiveStage.status"></span>
                    <div class="current-body">
                      <span class="current-label">{{ compactActiveStage.label }}</span>
                      <span v-if="compactActiveStage.text" class="current-text">{{ compactActiveStage.text }}</span>
                    </div>
                    <span class="current-badge" :class="compactActiveStage.status">
                      {{ stageStatusText(compactActiveStage.status) }}
                    </span>
                  </div>
                </div>

                <div v-if="!isProcessing && stageTimeline.length" class="thinking-footer">
                  {{ processSummaryText }}
                </div>
              </div>
            </template>

            <template v-else-if="item.type === 'assistant'">
              <div class="message-card assistant" :class="{ thinking: isAssistantThinking(item) }">
                <div class="message-label"><span>专家回复</span></div>
                <div v-if="isAssistantThinking(item)" class="thinking-state">
                  <span class="thinking-spinner" aria-hidden="true"></span>
                  <div class="thinking-copy">
                    <strong>模型正在思考</strong>
                    <p>{{ processSummaryText }}</p>
                  </div>
                </div>
                <div
                  v-else
                  class="message-bubble markdown-body"
                  v-html="item.renderedContent || renderMarkdown(item.content || '正在回复...')"
                ></div>
              </div>
            </template>

            <template v-else-if="item.type === 'chart'">
              <div class="message-card chart-card">
                <div class="message-label"><span>交互图表</span></div>
                <div
                  :id="`plotly-${item.id}`"
                  class="plotly-container"
                  style="width:100%;min-height:380px;"
                ></div>
              </div>
            </template>
          </article>
        </section>

        <section v-else class="workspace-body content-scroll analysis-view">
          <div v-if="!latestAnalysis" class="empty-analysis">
            <h3>{{ projectContext.ai_report_summary_status === 'running' ? 'AI报告总结生成中' : '暂无AI报告总结' }}</h3>
            <p v-if="projectContext.ai_report_summary_status === 'running'">项目已绑定，后台正在基于 HTML 项目报告生成总结。</p>
            <p v-else-if="projectContext.ai_report_summary_status === 'failed'">{{ projectContext.ai_report_summary_error || 'AI报告总结生成失败。' }}</p>
            <p v-else>选定项目后，系统会自动基于项目 HTML 报告生成 AI 总结。</p>
          </div>

          <template v-else>
            <section class="analysis-panel full-width ai-report-panel">
              <div class="ai-report-head">
                <div>
                  <h3>AI报告总结</h3>
                  <p>基于项目 HTML 报告自动生成</p>
                </div>
                <span v-if="projectContext.ai_report_summary_updated_at" class="report-time">{{ projectContext.ai_report_summary_updated_at }}</span>
              </div>
              <div class="markdown-body" v-html="renderMarkdown(latestAnalysis.answer || latestAnalysis.result_payload?.answer || latestAnalysis.report || '')"></div>
            </section>
          </template>
        </section>

        <footer v-if="workspaceView === 'chat'" class="workspace-composer" :class="{ compact: chatTimeline.length }">
          <div class="composer-shell workspace-panel" :class="{ compact: chatTimeline.length }">
            <div class="composer-main">
              <div v-if="!chatTimeline.length" class="composer-head">
                <strong>输入您的问题</strong>
                <span>Enter 发送 · Shift+Enter 换行</span>
              </div>

              <div class="composer-entry" :class="{ compact: chatTimeline.length }">
                <textarea
                  v-model="userInput"
                  class="composer-input"
                  :placeholder="inputPlaceholder"
                  :disabled="isProcessing"
                  @keyup.enter.exact.prevent="handleSend"
                />

                <div class="composer-side">
                  <button class="primary-button send-button" :disabled="!isProcessing && !userInput.trim()" @click="isProcessing ? handleCancel() : handleSend()">
                    {{ isProcessing ? '停止' : '发送' }}
                  </button>
                </div>
              </div>

              <div class="composer-shortcuts">
                <button class="mini-chip" @click="applyQuickAction('请分析当前项目的质控指标，重点关注比对率和 FRiP')">质控分析</button>
                <button class="mini-chip" @click="applyQuickAction('请帮我排查这个实验中可能存在的问题')">问题排查</button>
                <button class="mini-chip" @click="applyQuickAction('请介绍 CUT&Tag 实验的原理和关键注意事项')">知识问答</button>
              </div>
            </div>
          </div>
        </footer>
      </main>
    </section>
  </div>
</template>

<script>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { marked } from 'marked'

marked.setOptions({ breaks: true, gfm: true })

const API_BASE = '/api'
const AUTH_BASE = '/auth'
const DEFAULT_PROJECT_EVIDENCE_FILES = 12
const STREAM_RENDER_INTERVAL_MS = 160
const STREAM_SCROLL_INTERVAL_MS = 120
const SESSION_DRAFT_PERSIST_INTERVAL_MS = 300
const STREAM_PLACEHOLDER_HTML = '<p>正在回复...</p>'

const STAGE_LABELS = {
  identify_project: '识别项目',
  consult_start: '咨询路由',
  consult_generate: '生成回答',
  workflow_start: '建立上下文',
  planning: '制定计划',
  read_reads_qc: '读取 ReadsQC',
  read_alignment_qc: '读取 AlignmentQC',
  read_spikein: '读取 Spike-in',
  read_frip: '读取 FRiP',
  read_peak: '读取 Peak 统计',
  read_correlation: '读取相关性矩阵',
  read_diff: '读取差异结果',
  read_metric_guide: '读取指标说明',
  read_evidence_file: '读取证据文件',
  read_chart_data: '读取图表数据',
  generate_chart: '生成图表',
  retrieve_knowledge: '检索知识',
  analyze_project_data: '分析数据',
  harness_guard: '回答规则校验',
  compose_response: '汇总结论',
  synthesis: '汇总结论',
  memory_update: '写入记忆',
  generic: '处理中',
}

const STAGE_STATUS_TEXT = {
  in_progress: '进行中',
  completed: '已完成',
  needs_confirmation: '待确认',
  skipped: '已跳过',
  error: '失败',
}

const EMPTY_PROJECT_CONTEXT = {
  active_project_id: null,
  active_project_root: null,
  project_context_locked: false,
  project_context_source: null,
  recent_project_questions: [],
  pending_project_confirmation: null,
  pending_followup_action: null,
  last_identified_at: null,
}

const NEXT_ACTION_HEADING = '下一步建议'
const LAST_SESSION_PREFIX = 'agent_web_ui.last_session.'
const SESSION_DRAFT_PREFIX = 'agent_web_ui.session_draft.'
const SESSION_DRAFT_INDEX_PREFIX = 'agent_web_ui.session_draft_index.'

const stripMalformedNextActionSection = (value) => {
  if (typeof value !== 'string' || !value.includes(NEXT_ACTION_HEADING)) return value
  const tail = value.slice(value.lastIndexOf(NEXT_ACTION_HEADING) + NEXT_ACTION_HEADING.length)
  const hasListItem = /(^|\n)\s*(?:[-*]|\d+[.、])\s+/.test(tail)
  const hasDangingMarkdown = /(?:\*\*|\*|[:：])\s*$/.test(value)
  const tooShortTail = tail.trim().length < 16
  const boldMarkerCount = (tail.match(/\*\*/g) || []).length
  const hasUnbalancedBold = boldMarkerCount % 2 === 1
  const finalLine = tail
    .trim()
    .split('\n')
    .map((line) => line.trim())
    .filter(Boolean)
    .at(-1) || ''
  const finalLineIsBullet = /^\s*(?:[-*]|\d+[.、])\s+/.test(finalLine)
  const finalLineLooksIncomplete = finalLineIsBullet
    && (hasUnbalancedBold || finalLine.length < 28 || !/[。！？.!?；;）)\]】%]$/.test(finalLine))
  if (!hasDangingMarkdown && !hasUnbalancedBold && !finalLineLooksIncomplete && (!tooShortTail || hasListItem)) return value
  return value
    .replace(new RegExp(`\\n+\\s*#{1,6}\\s*${NEXT_ACTION_HEADING}[^\\n]*[\\s\\S]*$`), '')
    .replace(new RegExp(`\\n+\\s*${NEXT_ACTION_HEADING}\\s*[:：]?[^\\n]*[\\s\\S]*$`), '')
    .trim()
}

const sanitizeDisplayText = (value) => {
  if (typeof value !== 'string') return value ?? ''
  return stripMalformedNextActionSection(value.replace(/\uFFFD+/g, '').trim())
}

const stripUnbalancedStrongMarkers = (value) => {
  if (typeof value !== 'string') return value ?? ''
  const markers = value.match(/\*\*/g) || []
  if (markers.length % 2 === 0) return value
  const lastMarkerIndex = value.lastIndexOf('**')
  if (lastMarkerIndex < 0) return value
  return `${value.slice(0, lastMarkerIndex)}${value.slice(lastMarkerIndex + 2)}`
}

const sanitizeStreamChunk = (value) => {
  if (typeof value !== 'string') return value ?? ''
  return value.replace(/\uFFFD+/g, '')
}

const normalizeMarkdownText = (value) => stripUnbalancedStrongMarkers(sanitizeDisplayText(value))
  .replace(/(\d(?:[\d,.]*\d)?%?)~(?=\d)/g, '$1～')
  .replace(/(\d(?:[\d,.]*\d)?%?)\s*~\s*(?=\d)/g, '$1～')

const renderMarkdown = (text) => {
  if (!text) return ''
  try {
    return marked.parse(normalizeMarkdownText(text))
      .replace(/<table>/g, '<div class="markdown-table-scroll"><table class="markdown-data-table">')
      .replace(/<\/table>/g, '</table></div>')
  } catch {
    return normalizeMarkdownText(text)
  }
}

const normalizeAnalysisPayload = (analysis) => {
  if (!analysis || typeof analysis !== 'object') return null
  const resultPayload = analysis.result_payload || {}
  const data = analysis.data || {}
  return {
    ...analysis,
    result_payload: resultPayload,
    answer: analysis.answer || resultPayload.answer || '',
    report: analysis.report || resultPayload.report || data.report || '',
    knowledge_retrieval: analysis.knowledge_retrieval || resultPayload.knowledge_retrieval || {},
    used_knowledge: Boolean(analysis.used_knowledge ?? resultPayload.used_knowledge),
  }
}

const extractProjectIdFromPrompt = (text) => {
  const raw = String(text || '')
  const matches = raw.match(/[A-Za-z0-9][A-Za-z0-9._-]{2,}/g) || []
  const ignored = new Set(['api', 'agent', 'cut', 'tag', 'cut_tag', 'cuttag', 'cut&tag', 'atac'])
  return matches.find((item) => !ignored.has(item.toLowerCase()) && /\d/.test(item) && item.length >= 5) || null
}

export default {
  name: 'App',
  setup() {
    const isLoggedIn = ref(false)
    const username = ref('')
    const password = ref('')
    const password2 = ref('')
    const currentUser = ref('')
    const authToken = ref('')
    const loginError = ref('')
    const loginSuccess = ref('')
    const authLoading = ref(false)

    const getAuthHeaders = () => {
      const headers = { 'Content-Type': 'application/json' }
      if (authToken.value) {
        headers['Authorization'] = `Bearer ${authToken.value}`
      }
      return headers
    }
    const authMode = ref('login') // 'login' | 'register'

    const switchAuthMode = (mode) => {
      authMode.value = mode
      loginError.value = ''
      loginSuccess.value = ''
      username.value = ''
      password.value = ''
      password2.value = ''
    }

    const sessions = ref([])
    const selectedSessionId = ref('')
    const chatMessages = ref([])
    const stageTimeline = ref([])
    const userInput = ref('')
    const workspaceView = ref('chat')
    const isProcessing = ref(false)
    const messageListRef = ref(null)
    const abortController = ref(null)
    const autoScrollEnabled = ref(true)
    const projectContext = ref({ ...EMPTY_PROJECT_CONTEXT })
    const latestAnalysisRef = ref(null)
    const activeAssistantId = ref('')
    const activeUserMessageId = ref('')
    const streamingSessionId = ref('')
    const pendingSessionStates = ref({})
    let assistantRenderTimer = null
    let streamScrollTimer = null
    let draftPersistTimer = null
    let aiReportPollTimer = null
    let lastAssistantRenderAt = 0

    const shortSessionId = computed(() => (selectedSessionId.value || 'new_session').slice(-12))
    const latestAnalysis = computed(() => latestAnalysisRef.value)
    const processStateText = computed(() => {
      if (!stageTimeline.value.length) return isProcessing.value ? '执行中' : '待命'
      const active = [...stageTimeline.value].reverse().find((item) => item.status !== 'completed')
      return STAGE_STATUS_TEXT[active?.status || 'completed'] || '处理中'
    })
    const processRouteType = computed(() => {
      if (stageTimeline.value.some((item) => String(item.stage || '').startsWith('consult_'))) return 'consult'
      if (stageTimeline.value.some((item) => [
        'workflow_start',
        'planning',
        'analyze_project_data',
        'read_reads_qc',
        'read_alignment_qc',
        'read_spikein',
        'read_frip',
        'read_peak',
        'read_correlation',
        'read_diff',
        'read_metric_guide',
        'read_evidence_file',
        'read_chart_data',
        'generate_chart',
        'harness_guard',
        'compose_response',
        'synthesis',
        'memory_update',
      ].includes(item.stage))) return 'project'
      return 'generic'
    })
    const processTitle = computed(() => {
      if (processRouteType.value === 'consult') return isProcessing.value ? '正在执行咨询链路' : '本轮咨询已结束'
      if (processRouteType.value === 'project') return isProcessing.value ? '正在执行分析链路' : '本轮分析已结束'
      return isProcessing.value ? '正在处理请求' : '本轮过程已结束'
    })
    const processSummaryText = computed(() => {
      const active = [...stageTimeline.value].reverse().find((item) => item.status !== 'completed')
      if (active?.text) return active.text
      if (stageTimeline.value.length && processRouteType.value === 'consult') return '本轮咨询已完成，可以继续追问。'
      if (stageTimeline.value.length) return '本轮流程已完成，可以继续追问或切换到 AI报告总结 视图查看项目报告总结。'
      return '等待新的问题进入执行链路。'
    })
    const compactActiveStage = computed(() => {
      const stages = stageTimeline.value
      if (!stages.length) return null
      return [...stages].reverse().find((item) => item.status !== 'completed') || stages.at(-1)
    })
    const compactStageTrail = computed(() => {
      const activeStage = compactActiveStage.value?.stage
      return stageTimeline.value
        .filter((item) => item.stage !== activeStage)
        .slice(-5)
    })
    const compactStageMeta = computed(() => {
      const total = stageTimeline.value.length
      if (!total) return '等待开始'
      const completed = stageTimeline.value.filter((item) => item.status === 'completed').length
      const failed = stageTimeline.value.filter((item) => item.status === 'error').length
      if (failed) return `${completed}/${total} 完成 · ${failed} 失败`
      if (isProcessing.value) return `${completed}/${total} 完成`
      return `${total} 个动作`
    })
    const inputPlaceholder = computed(() => '输入项目编号开始质控分析，或直接提问实验问题、知识点…')
    const chatTimeline = computed(() => {
      const processItems = chatMessages.value.filter((item) => item.type === 'process')
      const hasProcessBundle = processItems.length || stageTimeline.value.length
      const processBundle = hasProcessBundle
        ? {
            id: 'process_bundle',
            type: 'process_bundle',
            content: processItems.map((entry) => entry.content).filter(Boolean).join('\n\n'),
          }
        : null

      const timeline = []
      let insertedProcessBundle = false

      chatMessages.value.forEach((item) => {
        if (item.type === 'process') return
        if (item.type === 'analysis') return

        const isActiveAssistant = item.type === 'assistant' && item.id === activeAssistantId.value
        if (isActiveAssistant && processBundle && !insertedProcessBundle) {
          timeline.push(processBundle)
          insertedProcessBundle = true
        }

        timeline.push(item)
      })

      if (processBundle && !insertedProcessBundle) {
        const lastUserIndex = [...timeline].map((item, index) => (item.type === 'user' ? index : -1)).filter((index) => index >= 0).pop()
        if (lastUserIndex === undefined) {
          timeline.push(processBundle)
        } else {
          timeline.splice(lastUserIndex + 1, 0, processBundle)
        }
      }

      return timeline
    })
    const stageStatusText = (status) => STAGE_STATUS_TEXT[status] || '处理中'
    const isAssistantThinking = (item) => Boolean(
      item?.type === 'assistant'
      && isProcessing.value
      && item.id === activeAssistantId.value
      && !sanitizeDisplayText(item.content)
    )
    const messageLabel = (type) => ({
      user: '用户',
      assistant: '专家回复',
      process: '系统过程',
    }[type] || '消息')

    const formatConfidence = (value) => {
      if (value === undefined || value === null || value === '') return '-'
      const num = Number(value)
      return Number.isNaN(num) ? String(value) : num.toFixed(2)
    }

    const formatProjectSource = (source) => ({
      user_explicit: '用户显式指定',
      active_context: '沿用当前窗口',
      request: '请求参数',
      question: '问题识别',
      session_memory: '会话记忆',
      inferred: '推断',
      no_context: '未绑定',
    }[source] || '')

    const diagnosisSummary = (analysis) => analysis?.data?.diagnosis_summary || null
    const topFindings = (analysis) => (analysis?.data?.automatic_findings || []).slice(0, 8)

    const getSessionPayload = (message) => {
      const content = message?.content
      if (content && typeof content === 'object') return content
      if (typeof content === 'string' && content.trim().startsWith('{')) {
        try {
          return JSON.parse(content)
        } catch {
          return null
        }
      }
      return null
    }

    const sessionPreview = (session) => {
      if (session.preview) return sanitizeDisplayText(session.preview)
      const analysisItem = (session.memory || []).find((item) => item.role === 'analysis' || getSessionPayload(item)?.workflow_trace)
      if (analysisItem) {
        const payload = getSessionPayload(analysisItem) || analysisItem.content
        return sanitizeDisplayText(payload?.identified_project?.project_id || payload?.data?.project_id || '项目分析')
      }
      const firstUser = (session.memory || []).find((item) => item.role === 'user')
      return sanitizeDisplayText(firstUser?.content || '新会话')
    }

    const shortTime = (value) => {
      if (!value) return '--'
      return sanitizeDisplayText(String(value)).slice(5, 16)
    }

    const normalizeStoredMessage = (item, index) => {
      const payload = getSessionPayload(item)
      if (item.role === 'analysis' || (payload && (payload.workflow_trace || payload.identified_project || payload.data))) {
        return {
          id: `analysis_${index}`,
          type: 'analysis',
          content: '',
          analysis: payload || item.content,
        }
      }

      const roleType = item.role === 'assistant' ? 'assistant' : item.role === 'user' ? 'user' : null
      if (!roleType) return null
      return {
        id: `${roleType}_${index}`,
        type: roleType,
        content: sanitizeDisplayText(typeof item.content === 'string' ? item.content : JSON.stringify(item.content || '', null, 2)),
        streaming: false,
        renderedContent: roleType === 'assistant'
          ? renderMarkdown(typeof item.content === 'string' ? item.content : JSON.stringify(item.content || '', null, 2))
          : '',
      }
    }

    const performScrollToBottom = async (force = false) => {
      await nextTick()
      const container = messageListRef.value
      if (container && (force || autoScrollEnabled.value)) {
        container.scrollTop = container.scrollHeight
      }
    }

    const scrollToBottom = async (force = false) => {
      if (force) {
        if (streamScrollTimer) {
          window.clearTimeout(streamScrollTimer)
          streamScrollTimer = null
        }
        await performScrollToBottom(true)
        return
      }
      if (streamScrollTimer) return
      streamScrollTimer = window.setTimeout(() => {
        streamScrollTimer = null
        performScrollToBottom(false).catch(() => {})
      }, STREAM_SCROLL_INTERVAL_MS)
    }

    const scrollToLatestQuestion = async (force = false) => {
      await nextTick()
      const container = messageListRef.value
      if (!container) return
      const latestUserMessage = activeUserMessageId.value
        ? container.querySelector(`[data-message-id="${activeUserMessageId.value}"]`)
        : [...container.querySelectorAll('.timeline-item.user')].at(-1)
      if (!latestUserMessage) return
      if (force) {
        latestUserMessage.scrollIntoView({ block: 'center', inline: 'nearest', behavior: 'auto' })
      } else {
        const containerRect = container.getBoundingClientRect()
        const messageRect = latestUserMessage.getBoundingClientRect()
        const isVisible = messageRect.top >= containerRect.top && messageRect.top <= containerRect.bottom
        if (!isVisible) {
          latestUserMessage.scrollIntoView({ block: 'center', inline: 'nearest', behavior: 'auto' })
        }
      }
      autoScrollEnabled.value = false
    }

    const handleMessageScroll = () => {
      const container = messageListRef.value
      if (!container) return
      const distance = container.scrollHeight - container.scrollTop - container.clientHeight
      autoScrollEnabled.value = distance < 80
    }

    const refreshProjectContext = async () => {
      if (!currentUser.value || !selectedSessionId.value) {
        projectContext.value = { ...EMPTY_PROJECT_CONTEXT }
        return
      }
      const userId = currentUser.value
      const sessionId = selectedSessionId.value
      const response = await fetch(`${API_BASE}/project_context`, {
        method: 'POST',
        headers: getAuthHeaders(),
        body: JSON.stringify({
          user_id: userId,
          session_id: sessionId,
        }),
      })
      const data = await response.json()
      if (currentUser.value !== userId || selectedSessionId.value !== sessionId) return
      projectContext.value = data.project_context || { ...EMPTY_PROJECT_CONTEXT }
    }

    const refreshLatestAnalysis = async () => {
      if (!currentUser.value || !selectedSessionId.value) {
        latestAnalysisRef.value = null
        return
      }
      const userId = currentUser.value
      const sessionId = selectedSessionId.value
      const response = await fetch(`${API_BASE}/latest_project_analysis`, {
        method: 'POST',
        headers: getAuthHeaders(),
        body: JSON.stringify({
          user_id: userId,
          session_id: sessionId,
        }),
      })
      const data = await response.json()
      if (currentUser.value !== userId || selectedSessionId.value !== sessionId) return
      latestAnalysisRef.value = normalizeAnalysisPayload(data.analysis)
    }

    const stopAiReportPolling = () => {
      if (aiReportPollTimer) {
        window.clearTimeout(aiReportPollTimer)
        aiReportPollTimer = null
      }
    }

    const pollAiReportSummary = async (remaining = 40) => {
      if (remaining <= 0 || !currentUser.value || !selectedSessionId.value) {
        stopAiReportPolling()
        return
      }
      await refreshProjectContext()
      if (projectContext.value.project_context_locked) {
        await refreshLatestAnalysis()
      }
      const status = projectContext.value.ai_report_summary_status
      if (status === 'running' || status === 'pending') {
        stopAiReportPolling()
        aiReportPollTimer = window.setTimeout(() => {
          pollAiReportSummary(remaining - 1).catch(() => {})
        }, 3000)
      } else {
        stopAiReportPolling()
      }
    }

    const startAiReportPolling = () => {
      stopAiReportPolling()
      pollAiReportSummary().catch(() => {})
    }

    const switchWorkspaceView = async (view) => {
      workspaceView.value = view
      if (view === 'analysis') {
        await Promise.all([refreshProjectContext(), refreshLatestAnalysis()])
        if (projectContext.value.ai_report_summary_status === 'running') {
          startAiReportPolling()
        }
      }
    }

    const clearProjectContext = async () => {
      if (!currentUser.value || !selectedSessionId.value || !projectContext.value.project_context_locked) return
      stopAiReportPolling()
      const response = await fetch(`${API_BASE}/project_context/clear`, {
        method: 'POST',
        headers: getAuthHeaders(),
        body: JSON.stringify({
          user_id: currentUser.value,
          session_id: selectedSessionId.value,
        }),
      })
      const data = await response.json()
      projectContext.value = data.project_context || { ...EMPTY_PROJECT_CONTEXT }
      latestAnalysisRef.value = null
    }

    const createNewSession = () => {
      stopAiReportPolling()
      if (selectedSessionId.value && chatMessages.value.length) {
        saveVisibleSessionState(selectedSessionId.value)
      }
      selectedSessionId.value = `session_${Date.now()}`
      chatMessages.value = []
      stageTimeline.value = []
      workspaceView.value = 'chat'
      userInput.value = ''
      projectContext.value = { ...EMPTY_PROJECT_CONTEXT }
      latestAnalysisRef.value = null
      activeAssistantId.value = ''
      activeUserMessageId.value = ''
    }

    const lastSessionStorageKey = () => `${LAST_SESSION_PREFIX}${currentUser.value || 'anonymous'}`
    const draftIndexStorageKey = () => `${SESSION_DRAFT_INDEX_PREFIX}${currentUser.value || 'anonymous'}`
    const draftStorageKey = (sessionId) => `${SESSION_DRAFT_PREFIX}${currentUser.value || 'anonymous'}.${sessionId}`

    const rememberSelectedSession = () => {
      if (currentUser.value && selectedSessionId.value) {
        localStorage.setItem(lastSessionStorageKey(), selectedSessionId.value)
      }
    }

    const cloneMessage = (item) => ({ ...item })
    const cloneStage = (item) => ({ ...item })
    const serializeDraftMessage = (item) => {
      const { renderedContent, renderVersion, ...rest } = item || {}
      return { ...rest }
    }

    const readDraftIndex = () => {
      try {
        const parsed = JSON.parse(localStorage.getItem(draftIndexStorageKey()) || '[]')
        return Array.isArray(parsed) ? parsed.filter(Boolean) : []
      } catch {
        return []
      }
    }

    const writeDraftIndex = (ids) => {
      try {
        localStorage.setItem(draftIndexStorageKey(), JSON.stringify([...new Set(ids.filter(Boolean))]))
      } catch (error) {
        console.warn('Failed to persist session draft index.', error)
      }
    }

    const loadPersistedSessionState = (sessionId) => {
      if (!sessionId) return null
      try {
        const parsed = JSON.parse(localStorage.getItem(draftStorageKey(sessionId)) || 'null')
        if (!parsed?.state || !Array.isArray(parsed.state.chatMessages)) return null
        return {
          chatMessages: parsed.state.chatMessages.map(cloneMessage),
          stageTimeline: Array.isArray(parsed.state.stageTimeline) ? parsed.state.stageTimeline.map(cloneStage) : [],
          activeAssistantId: parsed.state.activeAssistantId || '',
          activeUserMessageId: parsed.state.activeUserMessageId || '',
        }
      } catch {
        return null
      }
    }

    const persistSessionDraft = (sessionId, state) => {
      if (!sessionId || !state) return
      const hasContent = (state.chatMessages || []).some((item) => (
        (item.type === 'user' || item.type === 'assistant' || item.type === 'process')
        && sanitizeDisplayText(item.content || '')
      )) || (state.stageTimeline || []).length
      if (!hasContent) return
      const draft = {
        sessionId,
        updatedAt: Date.now(),
        state: {
          chatMessages: (state.chatMessages || []).map(serializeDraftMessage),
          stageTimeline: (state.stageTimeline || []).map(cloneStage),
          activeAssistantId: state.activeAssistantId || '',
          activeUserMessageId: state.activeUserMessageId || '',
        },
      }
      try {
        localStorage.setItem(draftStorageKey(sessionId), JSON.stringify(draft))
        writeDraftIndex([...readDraftIndex(), sessionId])
      } catch (error) {
        console.warn('Failed to persist session draft.', error)
      }
    }

    const clearPersistedSessionDraft = (sessionId) => {
      if (!sessionId) return
      try {
        localStorage.removeItem(draftStorageKey(sessionId))
      } catch (error) {
        console.warn('Failed to remove session draft.', error)
      }
      writeDraftIndex(readDraftIndex().filter((id) => id !== sessionId))
    }

    const draftStateToSession = (sessionId, state) => ({
      session_id: sessionId,
      updated_at: new Date().toISOString(),
      memory: (state?.chatMessages || [])
        .filter((item) => item.type === 'user' || item.type === 'assistant')
        .map((item) => ({
          role: item.type,
          content: sanitizeDisplayText(item.content || ''),
        }))
        .filter((item) => item.content),
      local_draft: true,
    })

    const hasUsableServerSession = (session) => (
      Boolean(session)
      && !session.local_draft
      && !session.error
      && (
        Number(session.total_messages || 0) > 0
        || (session.memory || []).some((msg) => msg.role === 'user' || msg.role === 'assistant')
      )
    )

    const mergeLocalDraftSessions = (serverSessions) => {
      const serverById = new Map((serverSessions || []).map((item) => [item.session_id, item]))
      readDraftIndex().forEach((sessionId) => {
        if (hasUsableServerSession(serverById.get(sessionId))) {
          clearPersistedSessionDraft(sessionId)
          return
        }
        const draftState = loadPersistedSessionState(sessionId)
        if (draftState) {
          serverSessions.push(draftStateToSession(sessionId, draftState))
        }
      })
      return serverSessions
    }

    const fetchUserSessions = async () => {
      if (!currentUser.value) return
      const response = await fetch(`${API_BASE}/user_sessions`, {
        method: 'POST',
        headers: getAuthHeaders(),
        body: JSON.stringify({ user_id: currentUser.value }),
      })
      const data = await response.json()
      sessions.value = mergeLocalDraftSessions(data.sessions || [])
    }

    // 恢复草稿时不同步渲染 markdown，避免消息多时阻塞主线程
    // renderedContent 留空，由 scheduleLazyRender 在 nextTick 后批量填充
    const renderDraftMessages = (messages) => messages.map((item) => {
      if (item.type !== 'assistant') return cloneMessage(item)
      return {
        ...item,
        renderedContent: '',
        renderVersion: 0,
      }
    })

    // 在当前帧结束后批量渲染所有尚未 render 的 assistant 消息
    const scheduleLazyRender = () => {
      nextTick(() => {
        const now = Date.now()
        chatMessages.value.forEach((item) => {
          if (item.type === 'assistant' && !item.streaming && item.content && !item.renderedContent) {
            item.renderedContent = renderMarkdown(item.content)
            item.renderVersion = now
          }
        })
      })
    }

    const loadStoredSessionMessages = (sessionId) => {
      const session = sessions.value.find((item) => item.session_id === sessionId)
      return (session?.memory || []).map(normalizeStoredMessage).filter(Boolean)
    }

    const fetchSessionMessages = async (sessionId, userId = currentUser.value) => {
      if (!userId || !sessionId) return []
      const response = await fetch(`${API_BASE}/session_messages`, {
        method: 'POST',
        headers: getAuthHeaders(),
        body: JSON.stringify({
          user_id: userId,
          session_id: sessionId,
        }),
      })
      const data = await response.json()
      return (data.messages || []).map(normalizeStoredMessage).filter(Boolean)
    }

    const snapshotVisibleSessionState = () => ({
      chatMessages: chatMessages.value.map(cloneMessage),
      stageTimeline: stageTimeline.value.map(cloneStage),
      activeAssistantId: activeAssistantId.value,
      activeUserMessageId: activeUserMessageId.value,
    })

    const saveVisibleSessionState = (sessionId) => {
      if (!sessionId) return
      const state = snapshotVisibleSessionState()
      pendingSessionStates.value = {
        ...pendingSessionStates.value,
        [sessionId]: state,
      }
      persistSessionDraft(sessionId, state)
    }

    const scheduleVisibleSessionPersist = (sessionId = selectedSessionId.value, force = false) => {
      if (!sessionId || sessionId !== selectedSessionId.value) return
      if (force) {
        if (draftPersistTimer) {
          window.clearTimeout(draftPersistTimer)
          draftPersistTimer = null
        }
        saveVisibleSessionState(sessionId)
        return
      }
      if (draftPersistTimer) return
      draftPersistTimer = window.setTimeout(() => {
        draftPersistTimer = null
        if (selectedSessionId.value === sessionId) {
          saveVisibleSessionState(sessionId)
        }
      }, SESSION_DRAFT_PERSIST_INTERVAL_MS)
    }

    const ensurePendingSessionState = (sessionId) => {
      const existing = pendingSessionStates.value[sessionId] || loadPersistedSessionState(sessionId)
      if (existing) return {
        chatMessages: existing.chatMessages.map(cloneMessage),
        stageTimeline: existing.stageTimeline.map(cloneStage),
        activeAssistantId: existing.activeAssistantId,
        activeUserMessageId: existing.activeUserMessageId,
      }
      return {
        chatMessages: loadStoredSessionMessages(sessionId),
        stageTimeline: [],
        activeAssistantId: '',
        activeUserMessageId: '',
      }
    }

    const commitPendingSessionState = (sessionId, state) => {
      const nextState = {
        chatMessages: state.chatMessages.map(cloneMessage),
        stageTimeline: state.stageTimeline.map(cloneStage),
        activeAssistantId: state.activeAssistantId,
        activeUserMessageId: state.activeUserMessageId,
      }
      pendingSessionStates.value = {
        ...pendingSessionStates.value,
        [sessionId]: nextState,
      }
      persistSessionDraft(sessionId, nextState)
    }

    const restorePendingSessionState = (sessionId) => {
      const pending = pendingSessionStates.value[sessionId] || loadPersistedSessionState(sessionId)
      if (!pending) return false
      chatMessages.value = renderDraftMessages(pending.chatMessages)
      stageTimeline.value = pending.stageTimeline.map(cloneStage)
      activeAssistantId.value = pending.activeAssistantId
      activeUserMessageId.value = pending.activeUserMessageId
      if (activeAssistantId.value) scheduleAssistantRender(true)
      scheduleLazyRender()
      return true
    }

    const clearPendingSessionState = (sessionId) => {
      if (!sessionId || !pendingSessionStates.value[sessionId]) return
      const next = { ...pendingSessionStates.value }
      delete next[sessionId]
      pendingSessionStates.value = next
      clearPersistedSessionDraft(sessionId)
    }

    const restoreInitialSession = async () => {
      const savedSessionId = localStorage.getItem(lastSessionStorageKey())
      const savedExists = savedSessionId && sessions.value.some((item) => item.session_id === savedSessionId)
      const savedDraftExists = savedSessionId && Boolean(loadPersistedSessionState(savedSessionId))
      const fallbackSessionId = (
        sessions.value.find((item) => (item.memory || []).some((msg) => msg.role === 'user' || msg.role === 'assistant'))
        || sessions.value[0]
      )?.session_id
      const sessionId = savedExists || savedDraftExists ? savedSessionId : fallbackSessionId
      if (sessionId) {
        await selectSession(sessionId)
        return
      }
      createNewSession()
    }

    const selectSession = async (sessionId) => {
      stopAiReportPolling()
      const userAtSelection = currentUser.value
      if (selectedSessionId.value && chatMessages.value.length) {
        saveVisibleSessionState(selectedSessionId.value)
      }
      selectedSessionId.value = sessionId
      rememberSelectedSession()
      stageTimeline.value = []
      latestAnalysisRef.value = null
      if (!restorePendingSessionState(sessionId)) {
        const storedMessages = loadStoredSessionMessages(sessionId)
        if (storedMessages.length) {
          chatMessages.value = storedMessages
        } else {
          const fetchedMessages = await fetchSessionMessages(sessionId, userAtSelection)
          if (selectedSessionId.value !== sessionId || currentUser.value !== userAtSelection) return
          chatMessages.value = fetchedMessages
        }
        activeAssistantId.value = ''
        activeUserMessageId.value = ''
      }
      await Promise.all([refreshProjectContext(), refreshLatestAnalysis()])
      if (selectedSessionId.value === sessionId && projectContext.value.ai_report_summary_status === 'running') {
        startAiReportPolling()
      }
      await scrollToLatestQuestion(true)
    }

    const deleteSession = async (sessionId) => {
      if (!currentUser.value || !sessionId) return
      if (!window.confirm('确定删除这条历史会话吗？')) return
      await fetch(`${API_BASE}/user_sessions/${sessionId}?user_id=${encodeURIComponent(currentUser.value)}`, {
        method: 'DELETE',
        headers: getAuthHeaders(),
      })
      clearPendingSessionState(sessionId)
      await fetchUserSessions()
      if (selectedSessionId.value === sessionId) {
        createNewSession()
      }
    }

    const handleLogin = async () => {
      loginError.value = ''
      loginSuccess.value = ''
      if (!username.value.trim() || !password.value) {
        loginError.value = '请填写用户名和密码'
        return
      }
      authLoading.value = true
      try {
        const res = await fetch(`${AUTH_BASE}/login`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ username: username.value.trim(), password: password.value }),
        })
        const data = await res.json()
        if (!data.ok) {
          loginError.value = data.message || '用户名或密码错误'
          return
        }
        localStorage.setItem('currentUserId', data.userId)
        localStorage.setItem('authToken', data.authToken || '')
        authToken.value = data.authToken || ''
        currentUser.value = data.username
        isLoggedIn.value = true
        username.value = ''
        password.value = ''
        await fetchUserSessions()
        await restoreInitialSession()
      } catch (e) {
        loginError.value = '网络错误，请稍后重试'
      } finally {
        authLoading.value = false
      }
    }

    const handleRegister = async () => {
      loginError.value = ''
      loginSuccess.value = ''
      if (!username.value.trim() || !password.value) {
        loginError.value = '请填写用户名和密码'
        return
      }
      if (password.value !== password2.value) {
        loginError.value = '两次密码不一致'
        return
      }
      authLoading.value = true
      try {
        const res = await fetch(`${AUTH_BASE}/register`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ username: username.value.trim(), password: password.value }),
        })
        const data = await res.json()
        if (!data.ok) {
          loginError.value = data.message || '注册失败'
          return
        }
        loginSuccess.value = '注册成功！请登录'
        switchAuthMode('login')
      } catch (e) {
        loginError.value = '网络错误，请稍后重试'
      } finally {
        authLoading.value = false
      }
    }

    const handleLogout = () => {
      localStorage.removeItem('currentUserId')
      localStorage.removeItem('authToken')
      authToken.value = ''
      currentUser.value = ''
      isLoggedIn.value = false
      sessions.value = []
      selectedSessionId.value = ''
      chatMessages.value = []
      stageTimeline.value = []
      streamingSessionId.value = ''
      pendingSessionStates.value = {}
      workspaceView.value = 'chat'
      projectContext.value = { ...EMPTY_PROJECT_CONTEXT }
      latestAnalysisRef.value = null
      activeAssistantId.value = ''
    }

    const handleCancel = () => {
      const sessionId = streamingSessionId.value || selectedSessionId.value
      abortController.value?.abort()
      abortController.value = null
      isProcessing.value = false
      finalizeAssistantMessage(sessionId)
      if (streamingSessionId.value === sessionId) {
        streamingSessionId.value = ''
      }
    }

    const completeStreamingUiState = (sessionId) => {
      abortController.value = null
      isProcessing.value = false
      if (streamingSessionId.value === sessionId) {
        streamingSessionId.value = ''
      }
    }

    const applyQuickAction = (prompt) => {
      workspaceView.value = 'chat'
      userInput.value = prompt
    }

    const normalizeStagePayload = (payload) => ({
      stage: payload.stage || 'generic',
      status: payload.status || 'in_progress',
      label: STAGE_LABELS[payload.stage] || STAGE_LABELS.generic,
      text: sanitizeDisplayText(payload.text || ''),
    })

    const upsertStageInList = (stages, payload) => {
      const nextStage = normalizeStagePayload(payload)
      const index = stages.findIndex((item) => item.stage === nextStage.stage)
      if (index >= 0) {
        stages.splice(index, 1, nextStage)
      } else {
        stages.push(nextStage)
      }
      if (nextStage.stage === 'memory_update' && nextStage.status === 'completed') {
        return stages.map((item) => ({ ...item, status: 'completed' }))
      }
      return stages
    }

    const upsertStage = (payload) => {
      stageTimeline.value = upsertStageInList(stageTimeline.value, payload)
      scheduleVisibleSessionPersist()
    }

    const ensureAssistantInState = (state) => {
      if (
        state.activeAssistantId
        && state.chatMessages.some((item) => item.id === state.activeAssistantId)
      ) return state.activeAssistantId
      const id = `assistant_${Date.now()}_${Math.random()}`
      state.chatMessages.push({
        id,
        type: 'assistant',
        content: '',
        streaming: true,
        renderedContent: STREAM_PLACEHOLDER_HTML,
      })
      state.activeAssistantId = id
      return id
    }

    const finalizeAssistantInState = (state) => {
      const assistantId = state.activeAssistantId
      if (!assistantId) return
      const index = state.chatMessages.findIndex((item) => item.id === assistantId)
      const target = state.chatMessages[index]
      if (target) {
        const content = sanitizeDisplayText(target.content || '')
        if (!content) {
          state.chatMessages.splice(index, 1)
        } else {
          state.chatMessages.splice(index, 1, {
            ...target,
            content,
            streaming: false,
            renderedContent: renderMarkdown(content),
            renderVersion: Date.now(),
          })
        }
      }
      state.activeAssistantId = ''
    }

    const appendStreamMessageToPendingSession = (sessionId, kind, text) => {
      const state = ensurePendingSessionState(sessionId)
      const normalizedKind = String(kind || '').toLowerCase()
      if (normalizedKind === 'process') {
        try {
          const payload = JSON.parse(text)
          if (payload?.type === 'project_stage') {
            state.stageTimeline = upsertStageInList(state.stageTimeline, payload)
            commitPendingSessionState(sessionId, state)
            return
          }
        } catch {
          // keep raw process log
        }
      }

      if (normalizedKind === 'answer') {
        const assistantId = ensureAssistantInState(state)
        const target = state.chatMessages.find((item) => item.id === assistantId)
        if (target) {
          target.content += sanitizeStreamChunk(text)
        }
        commitPendingSessionState(sessionId, state)
        return
      }

      const safeText = sanitizeDisplayText(text)
      const last = state.chatMessages.filter((item) => item.type === 'process').at(-1)
      if (last) {
        last.content += `\n\n${safeText}`
      } else {
        state.chatMessages.push({
          id: `process_${Date.now()}_${Math.random()}`,
          type: 'process',
          content: safeText,
        })
      }
      commitPendingSessionState(sessionId, state)
    }

    const finalizePendingSessionAssistant = (sessionId) => {
      const state = ensurePendingSessionState(sessionId)
      finalizeAssistantInState(state)
      commitPendingSessionState(sessionId, state)
    }

    const ensureAssistantPlaceholder = () => {
      if (activeAssistantId.value) return activeAssistantId.value
      const id = `assistant_${Date.now()}`
      chatMessages.value.push({
        id,
        type: 'assistant',
        content: '',
        streaming: true,
        renderedContent: STREAM_PLACEHOLDER_HTML,
      })
      activeAssistantId.value = id
      return id
    }

    const renderActiveAssistant = () => {
      assistantRenderTimer = null
      lastAssistantRenderAt = Date.now()
      const assistantId = activeAssistantId.value
      if (!assistantId) return
      const index = chatMessages.value.findIndex((item) => item.id === assistantId)
      const target = chatMessages.value[index]
      if (!target) return
      target.renderedContent = target.content ? renderMarkdown(target.content) : ''
      target.renderVersion = lastAssistantRenderAt
    }

    const scheduleAssistantRender = (force = false) => {
      if (force) {
        if (assistantRenderTimer) {
          window.clearTimeout(assistantRenderTimer)
          assistantRenderTimer = null
        }
        renderActiveAssistant()
        return
      }
      if (assistantRenderTimer) return
      const elapsed = Date.now() - lastAssistantRenderAt
      const delay = Math.max(0, STREAM_RENDER_INTERVAL_MS - elapsed)
      assistantRenderTimer = window.setTimeout(renderActiveAssistant, delay)
    }

    const finalizeAssistantMessage = (sessionId = selectedSessionId.value) => {
      if (sessionId && selectedSessionId.value !== sessionId) {
        finalizePendingSessionAssistant(sessionId)
        return
      }
      const assistantId = activeAssistantId.value
      if (!assistantId) return
      scheduleAssistantRender(true)
      const index = chatMessages.value.findIndex((item) => item.id === assistantId)
      const target = chatMessages.value[index]
      if (target) {
        const content = sanitizeDisplayText(target.content || '')
        if (!content) {
          chatMessages.value.splice(index, 1)
        } else {
          chatMessages.value.splice(index, 1, {
            ...target,
            content,
            streaming: false,
            renderedContent: renderMarkdown(content),
            renderVersion: Date.now(),
          })
        }
      }
      activeAssistantId.value = ''
      scheduleVisibleSessionPersist(sessionId, true)
    }

    const appendStreamMessage = (kind, text, sessionId = selectedSessionId.value) => {
      if (sessionId && selectedSessionId.value !== sessionId) {
        appendStreamMessageToPendingSession(sessionId, kind, text)
        return
      }
      const normalizedKind = String(kind || '').toLowerCase()
      if (normalizedKind === 'process') {
        try {
          const payload = JSON.parse(text)
          if (payload?.type === 'project_stage') {
            upsertStage(payload)
            return
          }
        } catch {
          // keep raw process log
        }
      }

      if (normalizedKind === 'answer') {
        const assistantId = ensureAssistantPlaceholder()
        const target = chatMessages.value.find((item) => item.id === assistantId)
        if (target) {
          target.content += sanitizeStreamChunk(text)
          scheduleAssistantRender()
          scheduleVisibleSessionPersist(sessionId)
        }
        return
      }

      // ── Plotly 交互图 spec ────────────────────────────────────────────────
      if (normalizedKind === 'chart_spec') {
        try {
          const spec = JSON.parse(text)
          const chartId = `chart_${Date.now()}_${Math.random().toString(36).slice(2, 7)}`
          chatMessages.value.push({
            id: chartId,
            type: 'chart',
            spec,
          })
          scheduleVisibleSessionPersist(sessionId)
        } catch (e) {
          console.warn('chart_spec parse error', e)
        }
        return
      }

      const safeText = sanitizeDisplayText(text)
      const last = chatMessages.value.filter((item) => item.type === 'process').at(-1)
      if (last) {
        last.content += `\n\n${safeText}`
      } else {
        chatMessages.value.push({
          id: `process_${Date.now()}_${Math.random()}`,
          type: 'process',
          content: safeText,
        })
      }
      scheduleVisibleSessionPersist(sessionId)
    }

    const sendChatStream = async (query, sessionId) => {
      const lockedProjectId = projectContext.value.project_context_locked ? projectContext.value.active_project_id : null
      const lockedProjectRoot = projectContext.value.project_context_locked ? projectContext.value.active_project_root : null
      const response = await fetch(`${API_BASE}/query`, {
        method: 'POST',
        headers: getAuthHeaders(),
        body: JSON.stringify({
          query,
          context: {
            user_id: currentUser.value,
            session_id: sessionId,
          },
          mode: 'agent',
          project_id: lockedProjectId || extractProjectIdFromPrompt(query),
          project_root: lockedProjectRoot || null,
          max_evidence_files: DEFAULT_PROJECT_EVIDENCE_FILES,
        }),
        signal: abortController.value.signal,
      })

      if (!response.ok) {
        let detail = `请求失败（${response.status}）`
        try {
          const err = await response.json()
          detail = err?.detail || detail
        } catch (_) {
          detail = (await response.text()) || detail
        }
        throw new Error(detail)
      }

      const reader = response.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''

      const processSseChunk = (chunk) => {
        const line = chunk.split('\n').find((item) => item.startsWith('data: '))
        if (!line) return
        const packet = JSON.parse(line.slice(6))
        if (packet.content?.contentType === 'sagegpt/finish') {
          finalizeAssistantMessage(sessionId)
          return
        }
        appendStreamMessage(packet.content.kind, packet.content.text, sessionId)
      }

      while (true) {
        const { done, value } = await reader.read()
        if (done) break

        buffer += decoder.decode(value, { stream: true })
        const chunks = buffer.split('\n\n')
        buffer = chunks.pop() || ''

        for (const chunk of chunks) {
          processSseChunk(chunk)
        }
      }

      buffer += decoder.decode()
      if (buffer.trim()) {
        for (const chunk of buffer.split('\n\n').filter((item) => item.trim())) {
          processSseChunk(chunk)
        }
      }
      finalizeAssistantMessage(sessionId)
    }

    const handleSend = async () => {
      const query = userInput.value.trim()
      if (!query || isProcessing.value || !currentUser.value) return
      if (!selectedSessionId.value) {
        selectedSessionId.value = `session_${Date.now()}`
      }
      const sessionId = selectedSessionId.value
      rememberSelectedSession()

      workspaceView.value = 'chat'
      autoScrollEnabled.value = true
      stageTimeline.value = []
      chatMessages.value = chatMessages.value.filter((item) => item.type !== 'process')
      const userMessageId = `user_${Date.now()}`
      activeUserMessageId.value = userMessageId
      chatMessages.value.push({ id: userMessageId, type: 'user', content: query })
      saveVisibleSessionState(sessionId)
      userInput.value = ''
      isProcessing.value = true
      abortController.value = new AbortController()
      streamingSessionId.value = sessionId
      activeAssistantId.value = ''
      ensureAssistantPlaceholder()
      saveVisibleSessionState(sessionId)
      await scrollToLatestQuestion(true)

      try {
        await sendChatStream(query, sessionId)
        completeStreamingUiState(sessionId)
        await fetchUserSessions()
        const persistedOnServer = sessions.value.some((item) => item.session_id === sessionId && hasUsableServerSession(item))
        if (selectedSessionId.value === sessionId) {
          if (persistedOnServer) {
            clearPendingSessionState(sessionId)
          } else {
            saveVisibleSessionState(sessionId)
          }
          await Promise.all([refreshProjectContext(), refreshLatestAnalysis()])
        }
        if (selectedSessionId.value === sessionId && projectContext.value.ai_report_summary_status === 'running') {
          startAiReportPolling()
        }
      } catch (error) {
        if (error.name !== 'AbortError') {
          appendStreamMessage('process', '请求失败，请检查后端服务是否已经启动。', sessionId)
        }
      } finally {
        finalizeAssistantMessage(sessionId)
        completeStreamingUiState(sessionId)
      }
    }

    onMounted(async () => {
      const savedUserId = localStorage.getItem('currentUserId')
      const savedToken = localStorage.getItem('authToken')
      if (!savedUserId) return

      currentUser.value = savedUserId
      authToken.value = savedToken || ''
      isLoggedIn.value = true

      // 1. 立刻从 localStorage 恢复上次会话内容，不等任何 API
      const savedSessionId = localStorage.getItem(lastSessionStorageKey())
      const hasDraft = savedSessionId && Boolean(loadPersistedSessionState(savedSessionId))
      if (hasDraft) {
        selectedSessionId.value = savedSessionId
        restorePendingSessionState(savedSessionId)
        await scrollToLatestQuestion(true)
      }

      // 2. 并行拉取会话列表和项目上下文，不阻塞界面显示
      const sessionsFetch = fetchUserSessions()
      const contextFetch = hasDraft
        ? Promise.all([refreshProjectContext(), refreshLatestAnalysis()])
        : Promise.resolve()

      await Promise.all([sessionsFetch, contextFetch])

      // 3. 如果没有本地草稿，走完整 restoreInitialSession（需要 sessions 列表）
      if (!hasDraft) {
        await restoreInitialSession()
      } else if (projectContext.value.ai_report_summary_status === 'running') {
        startAiReportPolling()
      }
    })

    // ── Plotly 图表渲染 ───────────────────────────────────────────────────────
    // 监听 chatMessages，当出现 type==='chart' 的新消息时，
    // 等 DOM 就绪后调用 window.Plotly.react() 渲染交互图。
    watch(
      () => chatMessages.value.filter((m) => m.type === 'chart').map((m) => m.id),
      async (chartIds) => {
        if (!chartIds.length || !window.Plotly) return
        await nextTick()
        for (const id of chartIds) {
          const el = document.getElementById(`plotly-${id}`)
          if (!el || el.dataset.plotlyRendered) continue
          const msg = chatMessages.value.find((m) => m.id === id)
          if (!msg?.spec) continue
          try {
            await window.Plotly.react(
              el,
              msg.spec.data || [],
              {
                ...msg.spec.layout,
                autosize: true,
                font: { family: 'Arial, sans-serif', color: '#334155' },
              },
              { responsive: true, displaylogo: false, modeBarButtonsToRemove: ['sendDataToCloud'] },
            )
            el.dataset.plotlyRendered = '1'
          } catch (e) {
            console.warn('Plotly render error', e)
          }
        }
      },
      { deep: false },
    )

    onBeforeUnmount(() => {
      if (selectedSessionId.value && chatMessages.value.length) {
        scheduleVisibleSessionPersist(selectedSessionId.value, true)
      }
      abortController.value?.abort()
      if (assistantRenderTimer) {
        window.clearTimeout(assistantRenderTimer)
        assistantRenderTimer = null
      }
      if (streamScrollTimer) {
        window.clearTimeout(streamScrollTimer)
        streamScrollTimer = null
      }
      if (draftPersistTimer) {
        window.clearTimeout(draftPersistTimer)
        draftPersistTimer = null
      }
      stopAiReportPolling()
    })

    return {
      applyQuickAction,
      chatTimeline,
      clearProjectContext,
      compactActiveStage,
      compactStageMeta,
      compactStageTrail,
      createNewSession,
      currentUser,
      deleteSession,
      diagnosisSummary,
      fetchUserSessions,
      formatConfidence,
      formatProjectSource,
      handleCancel,
      authLoading,
      authMode,
      handleLogin,
      handleLogout,
      handleMessageScroll,
      handleRegister,
      handleSend,
      inputPlaceholder,
      isAssistantThinking,
      isLoggedIn,
      isProcessing,
      latestAnalysis,
      loginError,
      loginSuccess,
      messageLabel,
      messageListRef,
      password,
      password2,
      switchAuthMode,
      processStateText,
      processSummaryText,
      processTitle,
      projectContext,
      renderMarkdown,
      selectedSessionId,
      selectSession,
      sessionPreview,
      sessions,
      shortSessionId,
      shortTime,
      stageStatusText,
      stageTimeline,
      switchWorkspaceView,
      topFindings,
      userInput,
      username,
      workspaceView,
    }
  },
}
</script>

<style scoped>
:global(:root) {
  --bg-app: #0a1020;
  --bg-shell: rgba(8, 14, 26, 0.82);
  --bg-sidebar: rgba(13, 19, 34, 0.78);
  --bg-panel: rgba(14, 21, 37, 0.82);
  --bg-panel-strong: rgba(12, 18, 32, 0.94);
  --bg-soft: rgba(255, 255, 255, 0.04);
  --line-soft: rgba(142, 160, 255, 0.12);
  --line-strong: rgba(142, 160, 255, 0.2);
  --text-primary: #f5f8ff;
  --text-secondary: #9eabc4;
  --text-tertiary: #7886a5;
  --accent-blue: #7aa2ff;
  --accent-cyan: #70d6ff;
  --accent-violet: #9d8dff;
  --shadow-panel: 0 24px 60px rgba(3, 8, 20, 0.24);
  --chat-track-width: min(1320px, calc(100vw - 180px));
  --assistant-card-width: min(1040px, calc(100vw - 300px));
  --user-bubble-max-width: min(620px, 70%);
}

:global(body) {
  margin: 0;
  min-width: 1240px;
  background: var(--bg-app);
  color: #dbe4f0;
  font-family: "Segoe UI Variable Display", "PingFang SC", "Microsoft YaHei UI", sans-serif;
}

* {
  box-sizing: border-box;
}

.app-shell {
  position: relative;
  min-height: 100vh;
  background:
    radial-gradient(circle at 18% 12%, rgba(108, 129, 255, 0.22), transparent 24%),
    radial-gradient(circle at 86% 14%, rgba(62, 191, 255, 0.14), transparent 18%),
    radial-gradient(circle at 72% 74%, rgba(92, 108, 255, 0.12), transparent 24%),
    linear-gradient(180deg, #0b1120 0%, #0a1020 44%, #090f1c 100%);
  overflow: visible;
}

.app-shell::before,
.app-shell::after {
  content: "";
  position: absolute;
  border-radius: 999px;
  filter: blur(56px);
  pointer-events: none;
  opacity: 0.6;
}

.app-shell::before {
  width: 360px;
  height: 360px;
  top: 72px;
  right: 10%;
  background: rgba(81, 105, 235, 0.26);
}

.app-shell::after {
  width: 280px;
  height: 280px;
  left: -36px;
  bottom: 120px;
  background: rgba(33, 173, 227, 0.16);
}

.backdrop-grid {
  position: absolute;
  inset: 0;
  background-image:
    linear-gradient(rgba(148, 163, 184, 0.06) 1px, transparent 1px),
    linear-gradient(90deg, rgba(148, 163, 184, 0.06) 1px, transparent 1px);
  background-size: 34px 34px;
  mask-image: radial-gradient(circle at center, black 48%, transparent 100%);
  pointer-events: none;
}

.login-shell,
.workspace {
  position: relative;
  z-index: 1;
}

.login-shell {
  min-height: 100vh;
  display: grid;
  place-items: center;
  padding: 32px;
}

.login-panel {
  width: min(1180px, 100%);
  display: grid;
  grid-template-columns: 1.35fr 420px;
  gap: 24px;
}

.login-copy,
.login-card,
.sidebar,
.main-panel,
.project-bar,
.message-card,
.analysis-panel,
.composer,
.hero-board,
.process-panel {
  background: var(--bg-shell);
  border: 1px solid var(--line-soft);
  box-shadow:
    var(--shadow-panel),
    inset 0 1px 0 rgba(255, 255, 255, 0.03);
  backdrop-filter: blur(18px);
}

.login-copy,
.login-card {
  border-radius: 22px;
  padding: 34px;
}

.eyebrow,
.lead,
.login-hint,
.sidebar-brand-copy p,
.topbar-copy p,
.muted,
.project-source,
.project-path {
  color: #94a3b8;
}

.eyebrow {
  margin: 0 0 10px;
  font-size: 12px;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}

.login-copy h1 {
  margin: 0 0 12px;
  font-size: 42px;
  line-height: 1.08;
  color: #f8fafc;
}

.lead {
  margin: 0;
  max-width: 560px;
  line-height: 1.8;
}

.hero-cards {
  margin-top: 28px;
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 14px;
}

.hero-cards article {
  padding: 18px;
  border-radius: 16px;
  border: 1px solid rgba(148, 163, 184, 0.14);
  background: rgba(2, 6, 23, 0.34);
}

.hero-cards strong {
  display: block;
  margin-bottom: 8px;
  color: #f8fafc;
}

.hero-cards p {
  margin: 0;
  color: #9fb0c4;
  line-height: 1.7;
  font-size: 13px;
}

.login-card {
  display: flex;
  flex-direction: column;
  justify-content: center;
}

.brand-logo {
  width: 68px;
  height: 68px;
  object-fit: contain;
}

.brand-logo.small {
  width: 42px;
  height: 42px;
}

.card-tag {
  margin: 16px 0 6px;
  color: #60a5fa;
  font-size: 12px;
  text-transform: uppercase;
}

.login-card h2 {
  margin: 0 0 12px;
  color: #f8fafc;
}

.field-label {
  display: block;
  margin: 16px 0 8px;
  color: #dbe4f0;
  font-size: 13px;
}

.text-input,
.composer-input {
  width: 100%;
  border: 1px solid rgba(148, 163, 184, 0.24);
  background: rgba(2, 6, 23, 0.66);
  color: #f8fafc;
  border-radius: 14px;
  padding: 12px 14px;
  transition: border-color 0.2s ease, box-shadow 0.2s ease;
}

.text-input:focus,
.composer-input:focus {
  outline: none;
  border-color: rgba(96, 165, 250, 0.66);
  box-shadow: 0 0 0 4px rgba(37, 99, 235, 0.16);
}

.primary-button,
.ghost-button,
.mode-button,
.mini-chip,
.quick-card,
.session-item {
  border-radius: 18px;
  transition: transform 0.18s ease, border-color 0.18s ease, background 0.18s ease, box-shadow 0.18s ease;
}

.primary-button {
  border: 1px solid rgba(104, 131, 255, 0.72);
  background:
    linear-gradient(135deg, #6787ff, #4b6af0 56%, #374ecd 100%);
  color: #f8fafc;
  padding: 11px 18px;
  font-weight: 700;
  box-shadow: 0 14px 30px rgba(52, 78, 201, 0.22);
  cursor: pointer;
}

.ghost-button,
.mode-button,
.mini-chip {
  border: 1px solid rgba(148, 163, 184, 0.14);
  background: rgba(255, 255, 255, 0.04);
  color: #e2e8f0;
  padding: 9px 14px;
  font-size: 14px;
  cursor: pointer;
}

.primary-button:hover,
.ghost-button:hover,
.mode-button:hover,
.mini-chip:hover,
.quick-card:hover,
.session-item:hover {
  transform: translateY(-2px);
}

.primary-button:active,
.ghost-button:active,
.mode-button:active,
.mini-chip:active,
.quick-card:active,
.session-item:active {
  transform: translateY(0);
}

.primary-button:disabled,
.ghost-button:disabled,
.mode-button:disabled {
  opacity: 0.48;
  cursor: not-allowed;
  box-shadow: none;
  transform: none;
}

.primary-button.wide {
  width: 100%;
  margin-top: 20px;
}

.error-text {
  color: #f87171;
  font-size: 13px;
  margin: 6px 0 0;
}

.success-text {
  color: #4ade80;
  font-size: 13px;
  margin: 6px 0 0;
}

.auth-switch {
  margin-top: 16px;
  text-align: center;
  font-size: 13px;
  color: #94a3b8;
}

.switch-link {
  color: #6366f1;
  cursor: pointer;
  margin-left: 4px;
  text-decoration: underline;
}

.switch-link:hover {
  color: #818cf8;
}

.primary-ghost {
  border-color: rgba(96, 165, 250, 0.48);
  color: #dbeafe;
}

.workspace {
  min-height: 100vh;
  display: grid;
  grid-template-columns: 276px minmax(0, 1fr);
  gap: 20px;
  padding: 20px;
}

.sidebar,
.main-panel {
  min-height: calc(100vh - 40px);
  border-radius: 30px;
}

.sidebar {
  padding: 18px 16px 16px;
  display: flex;
  flex-direction: column;
  gap: 16px;
  background:
    linear-gradient(180deg, rgba(15, 22, 39, 0.78), rgba(10, 16, 30, 0.88)),
    radial-gradient(circle at top left, rgba(112, 129, 255, 0.18), transparent 36%);
  box-shadow:
    0 18px 40px rgba(2, 6, 23, 0.18),
    inset 0 1px 0 rgba(255, 255, 255, 0.03);
  overflow: hidden;
  border: 1px solid rgba(132, 150, 220, 0.1);
}

.sidebar-brand {
  display: flex;
  align-items: center;
  gap: 14px;
  padding: 12px 12px 14px;
  border-radius: 22px;
  background:
    linear-gradient(160deg, rgba(22, 31, 55, 0.82), rgba(10, 15, 28, 0.78)),
    radial-gradient(circle at left top, rgba(110, 132, 255, 0.14), transparent 34%);
  border: 1px solid rgba(126, 147, 255, 0.12);
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.03),
    0 10px 24px rgba(3, 8, 20, 0.12);
}

.sidebar-brand-copy strong {
  display: block;
  color: #f8fafc;
  font-size: 20px;
  line-height: 1.12;
  letter-spacing: -0.02em;
}

.sidebar-brand-copy p {
  margin: 6px 0 0;
  font-size: 13px;
  color: #8fa4c7;
}

.sidebar-actions {
  display: grid;
  gap: 10px;
  padding-bottom: 4px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.05);
}

.session-section {
  display: grid;
  gap: 10px;
  min-height: 0;
  flex: 1;
  align-content: start;
  overflow: auto;
  padding-right: 6px;
  margin-top: 4px;
}

.session-section::-webkit-scrollbar {
  width: 8px;
}

.session-section::-webkit-scrollbar-thumb {
  background: rgba(107, 126, 208, 0.46);
  border-radius: 999px;
}

.section-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  color: #dbe4f0;
  font-size: 13px;
  padding: 0 4px;
  margin-top: 4px;
}

.section-count {
  min-width: 34px;
  padding: 3px 10px;
  border-radius: 999px;
  background: rgba(28, 39, 68, 0.92);
  color: #b5c4e4;
  text-align: center;
}

.empty-side,
.empty-analysis {
  display: grid;
  place-items: center;
  min-height: 220px;
  padding: 20px;
  text-align: center;
  border-radius: 16px;
  border: 1px dashed rgba(148, 163, 184, 0.22);
  color: #94a3b8;
}

.empty-side::before {
  content: "◌";
  display: block;
  margin-bottom: 10px;
  color: rgba(122, 144, 255, 0.44);
  font-size: 18px;
}

.session-item {
  border: 1px solid rgba(129, 145, 202, 0.12);
  background:
    linear-gradient(180deg, rgba(18, 26, 46, 0.96), rgba(12, 18, 34, 0.92));
  padding: 12px 12px 11px;
  text-align: left;
  cursor: pointer;
  min-height: 62px;
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.02);
  position: relative;
}

.session-item.active {
  border-color: rgba(102, 128, 255, 0.38);
  background:
    linear-gradient(135deg, rgba(63, 94, 234, 0.18), rgba(18, 28, 52, 0.96));
  box-shadow:
    inset 0 0 0 1px rgba(110, 136, 255, 0.12),
    0 16px 30px rgba(8, 13, 24, 0.28);
}

.session-item.active::before {
  content: "";
  position: absolute;
  top: 10px;
  bottom: 10px;
  left: 0;
  width: 3px;
  border-radius: 999px;
  background: linear-gradient(180deg, var(--accent-blue), rgba(255, 255, 255, 0));
}

.session-topline,
.analysis-kv,
.topbar,
.topbar-actions,
.composer-actions,
.project-bar,
.project-main,
.project-side,
.project-actions,
.process-summary {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.session-chip,
.process-badge,
.stage-status,
.project-label {
  padding: 3px 10px;
  border-radius: 999px;
  background: rgba(30, 41, 59, 0.82);
  color: #cbd5e1;
  font-size: 11px;
}

.session-preview {
  display: block;
  margin: 0;
  color: #f8fafc;
  line-height: 1.45;
  font-size: 13px;
  font-weight: 700;
  white-space: normal;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
  max-width: 100%;
}

.session-delete {
  border: 0;
  background: rgba(246, 113, 113, 0.08);
  color: #f5a7a7;
  padding: 4px 8px;
  cursor: pointer;
  font-size: 11px;
  line-height: 1.2;
  border-radius: 999px;
  flex: 0 0 auto;
}

.main-panel {
  min-height: 0;
  height: calc(100vh - 40px);
  padding: 22px 24px 22px;
  display: flex;
  flex-direction: column;
  gap: 16px;
  background:
    linear-gradient(180deg, rgba(14, 20, 35, 0.72), rgba(9, 13, 24, 0.92)),
    radial-gradient(circle at top right, rgba(83, 104, 224, 0.1), transparent 26%);
  box-shadow:
    0 18px 44px rgba(2, 6, 23, 0.18),
    inset 0 1px 0 rgba(255, 255, 255, 0.03);
  overflow: hidden;
  border: 1px solid rgba(132, 150, 220, 0.08);
}

.topbar,
.project-bar,
.content-scroll,
.composer {
  width: min(1320px, 100%);
  margin-inline: auto;
}

.topbar {
  padding: 4px 8px 12px;
  border-bottom: 1px solid rgba(148, 163, 184, 0.08);
  justify-content: space-between;
}

.topbar-copy h2,
.hero-board h3,
.analysis-panel h3,
.empty-analysis h3,
.process-log-header {
  margin: 0 0 6px;
  color: #f8fafc;
}

.topbar-copy p {
  margin: 0;
  max-width: 560px;
  line-height: 1.72;
  font-size: 13px;
  color: var(--text-secondary);
}

.topbar-copy h2 {
  font-size: 25px;
  letter-spacing: -0.02em;
  margin-bottom: 8px;
  line-height: 1.22;
  font-weight: 700;
}

.topbar-kicker {
  display: inline-flex;
  align-items: center;
  margin-bottom: 10px;
  padding: 5px 10px;
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.04);
  border: 1px solid rgba(129, 146, 224, 0.12);
  color: #9fb5e9;
  font-size: 11px;
  letter-spacing: 0.1em;
  text-transform: uppercase;
}

.topbar-actions {
  align-items: center;
  gap: 12px;
}

.view-switch {
  display: flex;
  gap: 6px;
  padding: 4px;
  border-radius: 18px;
  background: rgba(255, 255, 255, 0.04);
  border: 1px solid rgba(129, 146, 224, 0.1);
}

.mode-button.active {
  background: linear-gradient(135deg, rgba(88, 112, 255, 0.34), rgba(70, 95, 214, 0.24));
  border-color: rgba(122, 144, 255, 0.56);
  color: #eef4ff;
  box-shadow:
    inset 0 0 0 1px rgba(122, 146, 255, 0.12),
    0 10px 24px rgba(56, 76, 176, 0.16);
}

.project-bar {
  border-radius: 20px;
  padding: 14px 16px;
  background:
    linear-gradient(160deg, rgba(20, 29, 51, 0.74), rgba(12, 18, 33, 0.82)),
    radial-gradient(circle at left top, rgba(86, 111, 255, 0.14), transparent 26%);
  box-shadow:
    0 14px 28px rgba(3, 8, 20, 0.12),
    inset 0 1px 0 rgba(255, 255, 255, 0.03);
}

.project-bar.idle {
  border-style: dashed;
}

.project-main strong {
  color: #f8fafc;
  font-size: 18px;
  letter-spacing: -0.01em;
}

.project-source {
  font-size: 11px;
}

.project-side {
  flex: 1;
  justify-content: flex-end;
}

.project-path {
  max-width: 560px;
  text-align: right;
  word-break: break-all;
  font-size: 11px;
  line-height: 1.55;
}

.hero-board {
  position: relative;
  border-radius: 32px;
  padding: 30px 30px 28px;
  background:
    linear-gradient(160deg, rgba(22, 32, 56, 0.76), rgba(12, 19, 34, 0.9)),
    radial-gradient(circle at top right, rgba(92, 118, 245, 0.16), transparent 24%),
    radial-gradient(circle at bottom left, rgba(44, 188, 255, 0.08), transparent 28%);
  overflow: hidden;
  min-height: 0;
  display: grid;
  grid-template-columns: minmax(280px, 0.92fr) minmax(0, 1.18fr);
  gap: 24px;
  align-items: stretch;
  width: min(1240px, 100%);
  margin-inline: auto;
  box-shadow:
    0 20px 38px rgba(3, 8, 20, 0.16),
    inset 0 1px 0 rgba(255, 255, 255, 0.03);
  border: 1px solid rgba(129, 146, 224, 0.12);
}

.hero-board::before {
  content: "";
  position: absolute;
  inset: auto -60px -90px auto;
  transform: none;
  width: 320px;
  height: 320px;
  border-radius: 999px;
  background: radial-gradient(circle, rgba(65, 93, 205, 0.18), transparent 72%);
  filter: blur(34px);
  pointer-events: none;
}

.launchpad-copy,
.quick-actions {
  position: relative;
  z-index: 1;
}

.launchpad-copy {
  display: flex;
  flex-direction: column;
  justify-content: center;
  padding: 10px 16px 10px 8px;
  border-right: 1px solid rgba(255, 255, 255, 0.05);
}

.launchpad-kicker {
  display: inline-flex;
  align-items: center;
  width: fit-content;
  margin-bottom: 16px;
  padding: 7px 12px;
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.05);
  border: 1px solid rgba(135, 154, 228, 0.14);
  color: #9fb5e9;
  font-size: 12px;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}

.launchpad-copy h3 {
  margin: 0 0 12px;
  color: rgba(245, 248, 255, 0.94);
  font-size: 26px;
  line-height: 1.28;
  letter-spacing: -0.03em;
  font-weight: 700;
}

.launchpad-copy p {
  margin: 0;
  max-width: 32ch;
  color: rgba(158, 171, 196, 0.9);
  font-size: 13px;
  line-height: 1.68;
}

.launchpad-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  margin-top: 18px;
}

.launchpad-tags span {
  padding: 9px 12px;
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.04);
  border: 1px solid rgba(143, 161, 220, 0.12);
  color: #d7e1f6;
  font-size: 12px;
}

.quick-actions {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 16px;
  margin-top: 0;
  width: 100%;
  margin-inline: auto;
  align-items: stretch;
  align-self: center;
}

.quick-card {
  position: relative;
  border: 1px solid rgba(126, 148, 228, 0.16);
  background:
    linear-gradient(180deg, rgba(8, 12, 24, 0.92), rgba(11, 18, 34, 0.98));
  padding: 22px 20px 22px;
  text-align: left;
  cursor: pointer;
  min-height: 196px;
  border-radius: 22px;
  overflow: hidden;
  box-shadow:
    0 14px 28px rgba(5, 10, 22, 0.16),
    inset 0 1px 0 rgba(255, 255, 255, 0.02);
  transition: transform 0.22s ease, border-color 0.22s ease, box-shadow 0.22s ease, background 0.22s ease;
}

.quick-card::before {
  content: "";
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  height: 4px;
  background: linear-gradient(90deg, var(--card-accent), rgba(255, 255, 255, 0));
}

.quick-card:nth-child(1) {
  --card-accent: #76a9ff;
}

.quick-card:nth-child(2) {
  --card-accent: #58c8ff;
}

.quick-card:nth-child(3) {
  --card-accent: #8b95ff;
}

.quick-card-head {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 10px;
}

.quick-card-icon {
  display: inline-grid;
  place-items: center;
  width: 28px;
  height: 28px;
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.05);
  border: 1px solid rgba(255, 255, 255, 0.08);
  color: var(--card-accent);
  font-size: 12px;
  line-height: 1;
}

.quick-card-type {
  display: inline-block;
  color: var(--card-accent);
  font-size: 11px;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}

.quick-card strong {
  display: block;
  margin-bottom: 12px;
  color: #f8fafc;
  font-size: 19px;
  line-height: 1.26;
  letter-spacing: -0.02em;
  font-weight: 700;
}

.quick-card p {
  margin: 0;
  color: #9fb0c4;
  line-height: 1.72;
  font-size: 13px;
  max-width: 24ch;
}

.quick-card-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  margin-top: 20px;
  padding-top: 14px;
  border-top: 1px solid rgba(255, 255, 255, 0.06);
  color: #c8d4ef;
  font-size: 12px;
  letter-spacing: 0.03em;
}

.quick-card-foot span {
  font-size: 14px;
  color: var(--card-accent);
}

.quick-card:hover {
  border-color: rgba(148, 168, 255, 0.24);
  background:
    linear-gradient(180deg, rgba(10, 15, 28, 0.98), rgba(14, 23, 43, 0.98));
  box-shadow:
    0 22px 44px rgba(6, 12, 24, 0.2),
    inset 0 1px 0 rgba(255, 255, 255, 0.03);
}

.content-scroll {
  width: var(--chat-track-width);
  max-width: 100%;
  margin-inline: auto;
  flex: 1 1 auto;
  min-height: 0;
  overflow-y: auto;
  overflow-x: hidden;
  display: grid;
  gap: 18px;
  align-content: start;
  grid-auto-rows: max-content;
  padding: 12px 18px 22px;
  scrollbar-gutter: stable;
}

.content-scroll.empty-state {
  place-content: center;
}

.chat-empty-state {
  display: grid;
  justify-items: center;
  gap: 8px;
  color: #8fa4c7;
  text-align: center;
}

.chat-empty-state strong {
  color: #e4ecff;
  font-size: 18px;
}

.chat-empty-state p {
  margin: 0;
  font-size: 13px;
}

.content-scroll::-webkit-scrollbar {
  width: 8px;
}

.content-scroll::-webkit-scrollbar-thumb {
  background: rgba(71, 85, 105, 0.6);
  border-radius: 999px;
}

.timeline-item.user {
  justify-self: start;
  width: 100%;
  display: flex;
  justify-content: flex-end;
  scroll-margin-block: 96px 140px;
}

.timeline-item.process_bundle,
.timeline-item.assistant {
  justify-self: start;
  width: 100%;
}

.message-card {
  position: relative;
  border-radius: 14px;
  padding: 12px 14px;
  border: 1px solid rgba(148, 163, 184, 0.08);
  box-shadow: none;
  min-width: 0;
}

.message-card.user {
  border: 1px solid rgba(96, 165, 250, 0.22);
  background: rgba(37, 99, 235, 0.14);
  width: fit-content;
  min-width: auto;
  max-width: min(var(--user-bubble-max-width), 100%);
  margin-left: auto;
}

.message-card.user .message-bubble {
  overflow-wrap: anywhere;
}

.message-card.assistant {
  border: 1px solid rgba(148, 163, 184, 0.12);
  border-left: 3px solid rgba(52, 211, 153, 0.72);
  border-radius: 18px;
  padding: 18px 22px 20px 58px;
  width: var(--assistant-card-width);
  max-width: 100%;
  background:
    linear-gradient(180deg, rgba(14, 21, 36, 0.88), rgba(10, 16, 29, 0.94)),
    radial-gradient(circle at top left, rgba(52, 211, 153, 0.08), transparent 32%);
  box-shadow: 0 18px 42px rgba(2, 6, 23, 0.18);
}

.message-card.assistant::before {
  content: "AI";
  position: absolute;
  left: 18px;
  top: 18px;
  display: grid;
  place-items: center;
  width: 28px;
  height: 28px;
  border-radius: 8px;
  color: #d7fbe8;
  font-size: 11px;
  font-weight: 700;
  background: linear-gradient(145deg, rgba(16, 185, 129, 0.9), rgba(20, 184, 166, 0.72));
  box-shadow: 0 8px 20px rgba(16, 185, 129, 0.16);
}

.message-card.assistant.thinking::before {
  content: "";
  background:
    radial-gradient(circle, rgba(9, 15, 28, 0.96) 48%, transparent 50%),
    conic-gradient(from 0deg, rgba(52, 211, 153, 0), #34d399, #60a5fa, rgba(52, 211, 153, 0));
  animation: thinking-spin 0.9s linear infinite;
}

.message-card.assistant.thinking::after {
  content: "";
  position: absolute;
  left: 29px;
  top: 29px;
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: #d1fae5;
  box-shadow: 0 0 14px rgba(52, 211, 153, 0.82);
}

.message-card.process {
  border-left: 2px solid rgba(245, 158, 11, 0.55);
  width: 100%;
  max-width: 100%;
  background: rgba(15, 23, 42, 0.58);
}

/* ── 交互图表卡片 ── */
.message-card.chart-card {
  width: 100%;
  max-width: 100%;
  background: #ffffff;
  border: 1px solid #e2e8f0;
  border-radius: 10px;
  padding: 16px 18px 12px;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.06);
}

.message-card.chart-card .message-label span {
  color: #1F5E9C;
}

.plotly-container {
  width: 100%;
  min-height: 380px;
  border-radius: 6px;
  overflow: hidden;
}

.message-label {
  margin-bottom: 8px;
  color: #8d9ab5;
  font-size: 12px;
  font-weight: 650;
}

.message-card.assistant .message-label {
  margin-bottom: 10px;
}

.process-label {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.process-badge.active {
  background: rgba(37, 99, 235, 0.28);
  color: #dbeafe;
}

.message-bubble {
  padding: 2px 0;
  min-width: 0;
  max-width: 100%;
}

.thinking-state {
  display: flex;
  align-items: center;
  gap: 10px;
  min-height: 46px;
  padding: 8px 12px;
  border: 1px solid rgba(148, 163, 184, 0.1);
  border-radius: 12px;
  background: rgba(15, 23, 42, 0.42);
}

.thinking-spinner {
  position: relative;
  flex: 0 0 auto;
  width: 18px;
  height: 18px;
  border-radius: 50%;
  background:
    radial-gradient(circle, rgba(15, 23, 42, 0.96) 48%, transparent 50%),
    conic-gradient(from 0deg, rgba(52, 211, 153, 0), #34d399, #60a5fa, rgba(52, 211, 153, 0));
  animation: thinking-spin 0.9s linear infinite;
}

.thinking-spinner::before {
  display: none;
}

.thinking-spinner::after {
  content: '';
  position: absolute;
  top: 0;
  left: 50%;
  width: 4px;
  height: 4px;
  border-radius: 50%;
  background: #d1fae5;
  box-shadow: 0 0 10px rgba(52, 211, 153, 0.82);
  transform: translateX(-50%);
}

.thinking-copy {
  min-width: 0;
}

.thinking-copy strong {
  display: block;
  color: #e5edf8;
  font-size: 14px;
  margin-bottom: 2px;
}

.thinking-copy p {
  margin: 0;
  color: #94a3b8;
  line-height: 1.55;
  font-size: 12px;
}

.process-panel {
  border-radius: 18px;
  padding: 16px;
  background: rgba(8, 14, 27, 0.52);
}

.process-core {
  display: flex;
  align-items: center;
  gap: 14px;
}

.process-core strong {
  display: block;
  margin-bottom: 4px;
}

.process-core p,
.stage-copy p,
.analysis-list,
.markdown-body :deep(p) {
  margin: 0;
}

.process-orb {
  width: 14px;
  height: 14px;
  border-radius: 50%;
  background: #64748b;
  box-shadow: 0 0 0 0 rgba(59, 130, 246, 0.45);
}

.process-orb.active {
  background: #3b82f6;
  animation: pulse 1.6s infinite;
}

.process-meta {
  color: #94a3b8;
  font-size: 12px;
}

.stage-compact {
  margin-top: 12px;
  display: grid;
  gap: 10px;
}

.stage-current {
  display: grid;
  grid-template-columns: 10px 1fr auto;
  gap: 10px;
  align-items: center;
  padding: 10px 12px;
  border-radius: 14px;
  background: rgba(15, 23, 42, 0.56);
  border: 1px solid rgba(148, 163, 184, 0.1);
}

.stage-current .stage-dot {
  margin-top: 0;
}

.stage-current.in_progress {
  border-color: rgba(96, 165, 250, 0.22);
  background: linear-gradient(90deg, rgba(59, 130, 246, 0.12), rgba(15, 23, 42, 0.54));
}

.stage-current.error {
  border-color: rgba(248, 113, 113, 0.24);
}

.stage-trail {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.stage-chip {
  max-width: 190px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  padding: 5px 10px;
  border-radius: 999px;
  color: #9fb0c4;
  font-size: 12px;
  background: rgba(15, 23, 42, 0.5);
  border: 1px solid rgba(148, 163, 184, 0.1);
}

.stage-chip.completed {
  color: #b9f5d4;
  border-color: rgba(16, 185, 129, 0.16);
  background: rgba(16, 185, 129, 0.08);
}

.stage-chip.in_progress {
  color: #bfdbfe;
  border-color: rgba(96, 165, 250, 0.18);
  background: rgba(59, 130, 246, 0.1);
}

.stage-list {
  display: grid;
  gap: 10px;
  margin-top: 14px;
}

.stage-item {
  display: grid;
  grid-template-columns: 12px 1fr auto;
  gap: 12px;
  align-items: start;
  padding: 10px 12px;
  border-radius: 14px;
  background: rgba(15, 23, 42, 0.58);
  border: 1px solid rgba(148, 163, 184, 0.1);
}

.stage-dot {
  width: 10px;
  height: 10px;
  border-radius: 50%;
  margin-top: 5px;
  background: #64748b;
}

.stage-item.in_progress .stage-dot {
  background: #3b82f6;
}

.stage-item.completed .stage-dot {
  background: #10b981;
}

.stage-copy strong {
  display: block;
  margin-bottom: 4px;
  color: #f8fafc;
}

.stage-copy p {
  color: #9fb0c4;
  line-height: 1.6;
}

.process-log {
  margin-top: 14px;
}

.process-log-header {
  font-size: 14px;
}

.analysis-view {
  display: grid;
  gap: 14px;
}

.analysis-overview {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 10px;
}

.overview-card {
  padding: 18px 18px 16px;
  border-radius: 20px;
  border: 1px solid rgba(148, 163, 184, 0.1);
  background: rgba(12, 19, 33, 0.82);
}

.overview-card.highlight {
  border-color: rgba(96, 165, 250, 0.38);
  background: linear-gradient(180deg, rgba(37, 99, 235, 0.14), rgba(15, 23, 42, 0.84));
}

.overview-card span {
  display: block;
  color: #8fb1d9;
  font-size: 12px;
  margin-bottom: 10px;
}

.overview-card strong {
  display: block;
  color: #f8fafc;
  font-size: 24px;
  line-height: 1.1;
  margin-bottom: 8px;
}

.overview-card p {
  margin: 0;
  color: #94a3b8;
  font-size: 12px;
}

.analysis-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}

.analysis-panel {
  border-radius: 22px;
  padding: 18px;
  background: rgba(12, 19, 33, 0.86);
}

.analysis-panel.full-width {
  grid-column: 1 / -1;
}

.ai-report-panel {
  display: grid;
  gap: 16px;
}

.ai-report-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  padding-bottom: 14px;
  border-bottom: 1px solid rgba(148, 163, 184, 0.12);
}

.ai-report-head h3 {
  margin: 0;
}

.ai-report-head p {
  margin: 6px 0 0;
  color: #94a3b8;
  font-size: 13px;
}

.report-time {
  flex: 0 0 auto;
  color: #8fb1d9;
  font-size: 12px;
  padding: 6px 10px;
  border-radius: 999px;
  background: rgba(148, 163, 184, 0.1);
}

.analysis-kv {
  padding: 10px 0;
  border-bottom: 1px solid rgba(148, 163, 184, 0.12);
}

.analysis-kv:last-child {
  border-bottom: none;
}

.analysis-kv span {
  color: #94a3b8;
}

.analysis-kv strong {
  color: #f8fafc;
  text-align: right;
  word-break: break-word;
}

.analysis-list {
  display: grid;
  gap: 8px;
  padding-left: 18px;
  color: #dbe4f0;
}

.workspace-panel {
  border: 1px solid rgba(129, 146, 224, 0.12);
  background:
    linear-gradient(180deg, rgba(16, 24, 40, 0.82), rgba(10, 15, 28, 0.92)),
    radial-gradient(circle at top left, rgba(110, 132, 255, 0.08), transparent 30%);
  box-shadow:
    0 16px 30px rgba(2, 6, 23, 0.16),
    inset 0 1px 0 rgba(255, 255, 255, 0.03);
  backdrop-filter: blur(18px);
}

.workspace-header {
  border-radius: 26px;
  padding: 22px 24px;
  display: grid;
  gap: 18px;
  background:
    linear-gradient(180deg, rgba(18, 26, 44, 0.88), rgba(11, 17, 31, 0.94)),
    radial-gradient(circle at top left, rgba(110, 132, 255, 0.12), transparent 34%);
}

.workspace-header.compact {
  padding: 12px 18px;
}

.workspace-header-main {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 18px;
}

.workspace-header.compact .workspace-header-main {
  justify-content: space-between;
  align-items: center;
}

.topbar-copy {
  display: grid;
  gap: 8px;
  max-width: 720px;
}

.header-project-summary {
  display: flex;
  align-items: center;
  gap: 12px;
  min-width: 0;
  flex-wrap: wrap;
}

.header-project-summary strong {
  color: #eef3ff;
  font-size: 18px;
  line-height: 1.2;
}

.header-clear-button {
  margin-left: 4px;
  padding-inline: 12px;
}

.topbar-actions {
  align-items: flex-end;
  gap: 10px;
}

.project-context-panel {
  border-radius: 24px;
  padding: 16px 18px 18px;
  display: grid;
  gap: 14px;
  background:
    linear-gradient(180deg, rgba(13, 20, 35, 0.86), rgba(10, 15, 28, 0.94)),
    radial-gradient(circle at top right, rgba(87, 123, 255, 0.08), transparent 36%);
}

.project-context-panel.compact {
  padding: 10px 14px;
  gap: 0;
}

.project-context-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
}

.project-context-copy {
  display: grid;
  gap: 4px;
}

.project-context-kicker {
  color: #93a8d9;
  font-size: 11px;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}

.project-context-copy strong {
  color: #eef3ff;
  font-size: 16px;
  font-weight: 650;
}

.project-context-state {
  padding: 7px 12px;
  border-radius: 999px;
  border: 1px solid rgba(129, 146, 224, 0.12);
  background: rgba(255, 255, 255, 0.04);
  color: #a7b6d8;
  font-size: 12px;
  white-space: nowrap;
}

.project-context-state.active {
  color: #dbe5ff;
  background: rgba(88, 110, 255, 0.14);
  border-color: rgba(116, 139, 255, 0.2);
}

.project-context-state.inline {
  padding: 4px 9px;
  font-size: 11px;
}

.project-strip {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 16px 18px;
  border-radius: 20px;
  background:
    linear-gradient(180deg, rgba(255, 255, 255, 0.038), rgba(255, 255, 255, 0.024)),
    radial-gradient(circle at left center, rgba(94, 122, 255, 0.08), transparent 28%);
  border: 1px solid rgba(129, 146, 224, 0.1);
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.025);
}

.project-strip.compact {
  padding: 12px 14px;
  border-radius: 18px;
  gap: 12px;
}

.project-strip.idle {
  border-style: dashed;
}

.project-strip-main,
.project-strip-side {
  display: flex;
  align-items: center;
  gap: 10px;
}

.project-strip-main {
  min-width: 0;
  flex-wrap: wrap;
}

.project-strip-main strong {
  color: #edf2ff;
  font-size: 17px;
  line-height: 1.2;
}

.project-strip-side {
  flex: 1;
  justify-content: flex-end;
  min-width: 0;
}

.toolbar-button {
  white-space: nowrap;
}

.workspace-body {
  flex: 1 1 auto;
  min-height: 0;
}

.hero-panel {
  border-radius: 28px;
  padding: 22px;
  display: grid;
  gap: 20px;
  min-height: 360px;
}

.hero-panel-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 20px;
}

.hero-copy {
  display: grid;
  gap: 10px;
  max-width: 720px;
}

.hero-copy h3 {
  margin: 0;
  font-size: 32px;
  line-height: 1.12;
  color: var(--text-primary);
}

.hero-copy p {
  margin: 0;
  color: var(--text-secondary);
  line-height: 1.7;
  max-width: 620px;
}

.hero-signal {
  min-width: 180px;
  padding: 14px 16px;
  border-radius: 20px;
  background: rgba(255, 255, 255, 0.04);
  border: 1px solid rgba(129, 146, 224, 0.12);
  display: grid;
  gap: 6px;
}

.hero-signal span,
.launchpad-kicker,
.capability-type,
.hero-hint-label,
.composer-mode {
  color: #9fb2db;
  font-size: 11px;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}

.hero-signal strong {
  color: var(--text-primary);
  font-size: 18px;
  line-height: 1.15;
}

.capability-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 14px;
}

.action-card {
  border: 1px solid rgba(129, 146, 224, 0.12);
  background:
    linear-gradient(180deg, rgba(19, 27, 47, 0.96), rgba(12, 19, 34, 0.94)),
    radial-gradient(circle at top right, rgba(102, 128, 255, 0.1), transparent 34%);
  box-shadow:
    0 16px 30px rgba(8, 13, 24, 0.18),
    inset 0 1px 0 rgba(255, 255, 255, 0.02);
}

.capability-card {
  border-radius: 24px;
  padding: 18px;
  display: grid;
  gap: 18px;
  text-align: left;
  min-height: 220px;
}

.capability-card-top,
.capability-card-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.capability-icon {
  width: 36px;
  height: 36px;
  border-radius: 14px;
  display: grid;
  place-items: center;
  background: rgba(122, 162, 255, 0.14);
  color: #dfe7ff;
  font-size: 18px;
}

.capability-card-body {
  display: grid;
  gap: 8px;
}

.capability-card-body strong {
  color: var(--text-primary);
  font-size: 22px;
  line-height: 1.2;
}

.capability-card-body p,
.hero-hint p,
.composer-side-meta p {
  margin: 0;
  color: var(--text-secondary);
  line-height: 1.65;
}

.capability-card-foot {
  color: #d5def6;
  font-size: 13px;
}

.capability-arrow {
  color: #b5c4ff;
  font-size: 18px;
}

.hero-panel-foot {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 18px;
  margin-top: auto;
}

.hero-hint {
  display: grid;
  gap: 6px;
  max-width: 420px;
}

.hero-tags,
.composer-shortcuts {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}

.hero-tags span {
  padding: 8px 12px;
  border-radius: 999px;
  border: 1px solid rgba(129, 146, 224, 0.12);
  background: rgba(255, 255, 255, 0.04);
  color: #d5def6;
  font-size: 12px;
}

.workspace-composer {
  flex: 0 0 auto;
  margin-top: 0;
  position: static;
  z-index: 2;
  width: var(--chat-track-width);
  max-width: 100%;
  margin-inline: auto;
}

.workspace-composer.compact {
  padding-top: 4px;
}

.composer-shell {
  border-radius: 22px;
  padding: 10px;
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  gap: 12px;
  align-items: stretch;
  background:
    linear-gradient(180deg, rgba(18, 26, 43, 0.92), rgba(13, 19, 33, 0.96)),
    radial-gradient(circle at top left, rgba(96, 165, 250, 0.08), transparent 34%);
}

.composer-shell.compact {
  padding: 10px;
  gap: 8px;
  border-radius: 22px;
}

.composer-main {
  display: grid;
  gap: 8px;
}

.composer-entry {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 96px;
  gap: 10px;
  align-items: stretch;
}

.composer-entry.compact {
  gap: 10px;
}

.composer-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.composer-head strong {
  color: #f8fafc;
  font-size: 18px;
  letter-spacing: -0.02em;
}

.composer-head span {
  color: #8fa4c7;
  font-size: 11px;
  letter-spacing: 0.03em;
}

.composer-input {
  min-height: 58px;
  resize: none;
  padding: 12px 14px;
  background: rgba(8, 13, 24, 0.58);
  border-color: rgba(148, 163, 184, 0.16);
  line-height: 1.6;
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.025);
}

.composer-shell.compact .composer-input {
  min-height: 52px;
  max-height: 120px;
  border-radius: 18px;
  padding: 13px 16px;
  line-height: 1.5;
}

.composer-input::placeholder {
  color: rgba(157, 170, 198, 0.58);
}

.composer-side {
  display: flex;
  align-items: stretch;
  justify-content: flex-end;
  min-width: 0;
}

.send-button {
  min-width: 96px;
  min-height: 46px;
  border-radius: 18px;
  font-size: 15px;
}

.composer-shell.compact .send-button {
  min-width: 96px;
  min-height: 52px;
  border-radius: 18px;
}

.project-actions .ghost-button {
  padding: 8px 14px;
  font-size: 13px;
}

.markdown-body {
  color: #e2e8f0;
  font-size: 14px;
  line-height: 1.72;
  word-break: break-word;
  overflow-wrap: anywhere;
  max-width: 100%;
  overflow-x: hidden;
  min-width: 0;
}

.message-bubble.markdown-body {
  overflow-x: visible;
  overflow-y: hidden;
}

.markdown-body :deep(p),
.markdown-body :deep(li),
.markdown-body :deep(blockquote) {
  max-width: 100%;
  overflow-wrap: anywhere;
}

.markdown-body :deep(p) {
  margin: 0 0 12px;
}

.markdown-body :deep(strong),
.markdown-body :deep(b) {
  font-size: inherit;
  line-height: inherit;
  font-weight: 650;
}

.markdown-body :deep(h1),
.markdown-body :deep(h2),
.markdown-body :deep(h3),
.markdown-body :deep(h4) {
  margin: 18px 0 10px;
  color: #eef4ff;
  font-weight: 700;
  line-height: 1.35;
  letter-spacing: 0;
}

.markdown-body :deep(h1) { font-size: 20px; }
.markdown-body :deep(h2) { font-size: 17px; }
.markdown-body :deep(h3) { font-size: 15.5px; }
.markdown-body :deep(h4) { font-size: 14.5px; }

.markdown-body :deep(ul),
.markdown-body :deep(ol) {
  margin: 8px 0 12px;
  padding-left: 22px;
}

.markdown-body :deep(li + li) {
  margin-top: 6px;
}

.markdown-body :deep(pre) {
  overflow: auto;
  max-width: 100%;
  padding: 12px;
  border-radius: 12px;
  background: rgba(2, 6, 23, 0.72);
}

.markdown-body :deep(code) {
  font-family: Consolas, Monaco, monospace;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  word-break: break-word;
  font-size: 0.92em;
}

.markdown-body :deep(img) {
  display: block;
  max-width: min(100%, 720px);
  max-height: 520px;
  object-fit: contain;
  margin: 14px 0;
  border-radius: 14px;
  background: #ffffff;
  box-shadow: 0 18px 42px rgba(2, 6, 23, 0.28);
}

.markdown-body :deep(table) {
  width: 100%;
  min-width: max(100%, 720px);
  max-width: 100%;
  table-layout: fixed;
  border-collapse: separate;
  border-spacing: 0;
}

.markdown-body :deep(.markdown-table-scroll > .markdown-data-table) {
  width: 100% !important;
  min-width: max(100%, 720px) !important;
  table-layout: fixed;
}

.markdown-body :deep(.markdown-table-scroll) {
  display: block;
  width: 100%;
  max-width: 100%;
  overflow-x: auto;
  overflow-y: hidden;
  margin: 14px 0 16px;
  border: 1px solid rgba(148, 163, 184, 0.16);
  border-radius: 12px;
  background: rgba(15, 23, 42, 0.72);
  box-shadow: 0 12px 28px rgba(2, 6, 23, 0.16);
}

.markdown-body :deep(th),
.markdown-body :deep(td) {
  padding: 9px 11px;
  font-size: 13.5px;
  white-space: normal;
  overflow-wrap: anywhere;
  word-break: break-word;
  vertical-align: top;
  border-color: rgba(148, 163, 184, 0.16);
}

.markdown-body :deep(th) {
  color: #eef4ff;
  font-weight: 650;
  background: rgba(30, 41, 59, 0.86);
}

.markdown-body :deep(td) {
  color: #d9e2f2;
  background: rgba(15, 23, 42, 0.74);
}

.markdown-body :deep(tr:nth-child(even) td) {
  background: rgba(24, 34, 54, 0.72);
}

.error-text {
  margin-top: 10px;
  color: #fca5a5;
}

/* ── Thinking track（思考链路）── */
.thinking-track {
  width: 100%;
  border-radius: 16px;
  border: 1px solid rgba(148, 163, 184, 0.1);
  border-left: 3px solid rgba(148, 163, 184, 0.28);
  padding: 13px 16px;
  background: linear-gradient(180deg, rgba(12, 18, 32, 0.82), rgba(9, 14, 26, 0.92));
  box-shadow: 0 10px 24px rgba(2, 6, 23, 0.1);
  display: grid;
  gap: 10px;
}

.thinking-track.consult {
  border-left-color: rgba(96, 165, 250, 0.52);
}

.thinking-track.project {
  border-left-color: rgba(157, 141, 255, 0.52);
}

.thinking-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
}

.thinking-route {
  display: flex;
  align-items: center;
  gap: 9px;
  min-width: 0;
}

.thinking-orb {
  flex: 0 0 auto;
  width: 9px;
  height: 9px;
  border-radius: 50%;
  background: #374151;
  transition: background 0.3s;
}

.thinking-orb.active {
  background: #3b82f6;
  animation: pulse 1.6s infinite;
}

.thinking-title {
  color: #cbd5e1;
  font-size: 13px;
  font-weight: 650;
  white-space: nowrap;
}

.thinking-right {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-shrink: 0;
}

.thinking-meta-count {
  color: #475569;
  font-size: 11px;
}

.thinking-badge {
  padding: 3px 9px;
  border-radius: 999px;
  font-size: 11px;
  background: rgba(30, 41, 59, 0.82);
  color: #64748b;
  white-space: nowrap;
}

.thinking-badge.active {
  background: rgba(37, 99, 235, 0.2);
  color: #93c5fd;
}

.thinking-body {
  display: grid;
  gap: 7px;
}

.thinking-trail {
  display: flex;
  flex-wrap: wrap;
  gap: 5px;
}

.thinking-chip {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 3px 9px;
  border-radius: 999px;
  font-size: 11.5px;
  background: rgba(15, 23, 42, 0.48);
  border: 1px solid rgba(148, 163, 184, 0.08);
  color: #475569;
}

.thinking-chip.completed {
  color: #6ee7b7;
  border-color: rgba(16, 185, 129, 0.18);
  background: rgba(16, 185, 129, 0.07);
}

.thinking-chip.skipped {
  color: #374151;
}

.chip-dot {
  width: 4px;
  height: 4px;
  border-radius: 50%;
  background: currentColor;
  flex: 0 0 auto;
  opacity: 0.75;
}

.thinking-current {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 9px 12px;
  border-radius: 12px;
  border: 1px solid rgba(148, 163, 184, 0.1);
  background: rgba(15, 23, 42, 0.48);
}

.thinking-current.in_progress {
  border-color: rgba(96, 165, 250, 0.2);
  background: linear-gradient(90deg, rgba(59, 130, 246, 0.08), rgba(15, 23, 42, 0.5));
}

.thinking-current.completed {
  border-color: rgba(16, 185, 129, 0.16);
  background: rgba(16, 185, 129, 0.05);
}

.thinking-current.error {
  border-color: rgba(248, 113, 113, 0.2);
  background: rgba(248, 113, 113, 0.05);
}

.current-dot {
  flex: 0 0 auto;
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #374151;
  transition: background 0.3s, box-shadow 0.3s;
}

.current-dot.in_progress {
  background: #3b82f6;
  box-shadow: 0 0 0 3px rgba(59, 130, 246, 0.16);
  animation: pulse 1.6s infinite;
}

.current-dot.completed {
  background: #10b981;
}

.current-dot.error {
  background: #ef4444;
}

.current-body {
  flex: 1 1 auto;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.current-label {
  color: #e2e8f0;
  font-size: 13px;
  font-weight: 600;
}

.current-text {
  color: #475569;
  font-size: 12px;
  line-height: 1.5;
}

.current-badge {
  flex: 0 0 auto;
  padding: 3px 8px;
  border-radius: 999px;
  font-size: 11px;
  background: rgba(30, 41, 59, 0.8);
  color: #64748b;
  white-space: nowrap;
}

.current-badge.in_progress {
  background: rgba(37, 99, 235, 0.16);
  color: #93c5fd;
}

.current-badge.completed {
  background: rgba(16, 185, 129, 0.14);
  color: #6ee7b7;
}

.current-badge.error {
  background: rgba(239, 68, 68, 0.14);
  color: #fca5a5;
}

.thinking-footer {
  padding-top: 8px;
  border-top: 1px solid rgba(148, 163, 184, 0.07);
  color: #374151;
  font-size: 12px;
  line-height: 1.5;
}

@keyframes pulse {
  0% { box-shadow: 0 0 0 0 rgba(59, 130, 246, 0.4); }
  70% { box-shadow: 0 0 0 12px rgba(59, 130, 246, 0); }
  100% { box-shadow: 0 0 0 0 rgba(59, 130, 246, 0); }
}

@keyframes thinking-spin {
  to { transform: rotate(360deg); }
}

@media (prefers-reduced-motion: reduce) {
  .thinking-spinner,
  .thinking-orb.active,
  .current-dot.in_progress,
  .process-orb.active {
    animation: none;
  }
}

@media (max-width: 1360px) {
  :global(body) { min-width: 0; }

  .login-panel,
  .hero-cards,
  .analysis-overview,
  .analysis-grid {
    grid-template-columns: 1fr;
  }

  .capability-grid {
    grid-template-columns: 1fr;
  }

  .workspace-header-main,
  .hero-panel-head,
  .hero-panel-foot,
  .project-context-head,
  .project-strip,
  .project-strip-main,
  .project-strip-side,
  .project-actions,
  .composer-shell,
  .composer-head {
    flex-direction: column;
    align-items: flex-start;
  }

  .composer-shell { grid-template-columns: 1fr; }
  .composer-entry { grid-template-columns: 1fr; }
  .project-path { max-width: none; text-align: left; }

  .timeline-item.user,
  .message-card.assistant {
    width: 100%;
    max-width: 100%;
  }

  .message-card.user {
    max-width: min(520px, 100%);
  }

  .topbar-actions,
  .hero-tags,
  .composer-shortcuts {
    flex-direction: column;
    align-items: flex-start;
  }
}

@media (max-width: 980px) {
  .workspace {
    grid-template-columns: 1fr;
    min-height: auto;
    padding: 12px;
  }

  .sidebar,
  .main-panel {
    min-height: auto;
  }

  .main-panel { padding: 12px; }
}
</style>
