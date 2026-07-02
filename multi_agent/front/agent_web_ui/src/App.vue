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
          <button class="primary-button" @click="createNewSession"><svg xmlns="http://www.w3.org/2000/svg" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="#0d0d0d" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>新建会话</button>
          <button class="ghost-button" @click="fetchUserSessions"><svg xmlns="http://www.w3.org/2000/svg" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="#0d0d0d" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12a9 9 0 1 1-9-9c2.52 0 4.93 1 6.74 2.74L21 8"/><polyline points="21 3 21 8 16 8"/></svg>刷新列表</button>
          <button class="ghost-button" @click="handleLogout"><svg xmlns="http://www.w3.org/2000/svg" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="#0d0d0d" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><polyline points="16 17 21 12 16 7"/><line x1="21" y1="12" x2="9" y2="12"/></svg>退出登录</button>
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

      <main class="main-panel" :class="{ landing: !chatTimeline.length && workspaceView === 'chat' }">
        <section class="workspace-header workspace-panel" :class="{ compact: workspaceView === 'chat' && chatTimeline.length || workspaceView === 'analysis' }">
          <div class="workspace-header-main">
            <div v-if="(workspaceView === 'chat' && chatTimeline.length) || workspaceView === 'analysis'" class="header-project-summary">
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

        <section v-if="workspaceView === 'chat' && !chatTimeline.length" class="project-context-panel workspace-panel">
          <div class="project-context-head">
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
          v-show="workspaceView === 'chat'"
          ref="messageListRef"
          class="workspace-body content-scroll"
          :class="{ 'empty-state': !chatTimeline.length }"
          @scroll="handleMessageScroll"
        >
          <div v-if="!chatTimeline.length" class="chat-empty-state">
            <strong>有什么可以帮您？</strong>
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
                  <span class="thinking-title">{{ processTitle }}</span>
                  <span class="thinking-meta-count">{{ compactStageMeta }}</span>
                </div>

                <div v-if="stageTimeline.length" class="thinking-timeline">
                  <div
                    v-for="stage in compactStageTrail"
                    :key="stage.stage"
                    class="tl-item"
                  >
                    <div class="tl-left">
                      <span class="tl-dot" :class="stage.status"></span>
                      <span class="tl-line"></span>
                    </div>
                    <span class="tl-label">{{ stage.label }}</span>
                  </div>

                  <div v-if="compactActiveStage" class="tl-item last">
                    <div class="tl-left">
                      <span class="tl-dot" :class="compactActiveStage.status"></span>
                    </div>
                    <div class="tl-body">
                      <span class="tl-label" :class="{ active: compactActiveStage.status === 'in_progress' }">{{ compactActiveStage.label }}</span>
                      <span v-if="compactActiveStage.text" class="tl-sub">{{ compactActiveStage.text }}</span>
                    </div>
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

            <template v-else-if="item.type === 'image_chart'">
              <div class="message-card chart-card">
                <div class="message-label">
                  <span>R ggplot2 图表</span>
                  <a
                    :href="item.imageUrl"
                    download
                    class="chart-download-btn"
                    title="下载 PNG"
                  >↓ 下载</a>
                </div>
                <div class="image-chart-wrap">
                  <img
                    :src="item.imageUrl"
                    :alt="`${item.metric || ''} 图表`"
                    class="chart-img"
                    @click="openImageFull(item.imageUrl)"
                  />
                </div>
              </div>
            </template>
          </article>
        </section>

        <section v-show="workspaceView !== 'chat'" class="workspace-body content-scroll analysis-view">
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

        <footer v-show="workspaceView === 'chat'" class="workspace-composer" :class="{ compact: chatTimeline.length }">
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
                  @keydown.enter.exact.prevent="handleSend"
                />

                <div class="composer-side">
                  <button class="primary-button send-button" :disabled="!isProcessing && !userInput.trim()" @click="isProcessing ? handleCancel() : handleSend()">
                    {{ isProcessing ? '停止' : '发送' }}
                  </button>
                </div>
              </div>

            </div>
          </div>

          <!-- 快捷芯片始终在 pill 框外部下方 -->
          <div class="composer-shortcuts landing-chips">
            <button class="mini-chip" @click="applyQuickAction('请分析当前项目的质控指标，重点关注比对率和 FRiP')">质控分析</button>
            <button class="mini-chip" @click="applyQuickAction('请帮我排查这个实验中可能存在的问题')">问题排查</button>
            <button class="mini-chip" @click="applyQuickAction('请介绍 CUT&Tag 实验的原理和关键注意事项')">知识问答</button>
          </div>
        </footer>
      </main>
    </section>

    <!-- 图片全屏预览 overlay -->
    <div v-if="fullscreenImageUrl" class="img-fullscreen-mask" @click="closeImageFull">
      <img :src="fullscreenImageUrl" alt="图表全屏预览" @click.stop />
    </div>
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
    // 把 ```chart\n{chartId}\n``` 替换为提示语，避免将原始 chart_id 暴露为代码块
    const processed = normalizeMarkdownText(text)
      .replace(/```chart\n[\w-]+\n```/g, '\n> *(↓ 交互图表已在下方渲染)*\n')
    return marked.parse(processed)
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
      const newId = `session_${Date.now()}`
      selectedSessionId.value = newId
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

    // ── 图表持久化恢复 ─────────────────────────────────────────────────────────
    // 从 assistant 消息里提取内嵌的 ```chart\n{chartId}\n``` 块
    const extractChartIds = (content) => {
      const ids = []
      const re = /```chart\n([\w-]+)\n```/g
      let m
      while ((m = re.exec(content || '')) !== null) ids.push(m[1])
      return ids
    }

    // 从服务器会话（非 localStorage）恢复图表卡片：
    // 找到所有包含 chart 块的 assistant 消息 → 请求后端 /api/project_chart_spec/{id}
    // → 在对应 assistant 消息后插入 type==='chart' 消息，并写入 localStorage 草稿
    const restoreChartMessagesAsync = async (sessionId) => {
      if (selectedSessionId.value !== sessionId) return
      const messages = chatMessages.value
      const existingChartIds = new Set(
        messages
          .filter((m) => m.type === 'chart')
          .map((m) => m.spec?.chart_id)
          .filter(Boolean),
      )
      const toFetch = []
      messages.forEach((msg, idx) => {
        if (msg.type !== 'assistant') return
        extractChartIds(msg.content || '').forEach((chartId) => {
          if (!existingChartIds.has(chartId)) toFetch.push({ chartId, afterIdx: idx })
        })
      })
      if (!toFetch.length) return
      const results = await Promise.allSettled(
        toFetch.map(async ({ chartId, afterIdx }) => {
          try {
            const resp = await fetch(
              `${API_BASE}/project_chart_spec/${encodeURIComponent(chartId)}`,
              { headers: getAuthHeaders() },
            )
            if (!resp.ok) return null
            const data = await resp.json()
            return { chartId, afterIdx, spec: data.plotly_spec }
          } catch {
            return null
          }
        }),
      )
      if (selectedSessionId.value !== sessionId) return
      const toInsert = results
        .filter((r) => r.status === 'fulfilled' && r.value?.spec)
        .map((r) => r.value)
        .sort((a, b) => b.afterIdx - a.afterIdx)   // 倒序，splice 时下标不偏移
      if (!toInsert.length) return
      const newMessages = [...chatMessages.value]
      toInsert.forEach(({ chartId, afterIdx, spec }) => {
        const frontendId = `chart_${Date.now()}_${Math.random().toString(36).slice(2, 7)}`
        newMessages.splice(afterIdx + 1, 0, {
          id: frontendId,
          type: 'chart',
          spec: { chart_id: chartId, spec },
        })
      })
      chatMessages.value = newMessages
      scheduleVisibleSessionPersist(sessionId)   // 写入 localStorage，下次刷新直接用
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
      const merged = mergeLocalDraftSessions(data.sessions || [])
      const sorted = merged.sort((a, b) =>
        new Date(b.updated_at || 0) - new Date(a.updated_at || 0)
      )
      sessions.value = sorted
    }

    const upsertLocalDraftSession = (sessionId, state) => {
      if (!sessionId || !state) return
      const existing = sessions.value.find((item) => item.session_id === sessionId)
      if (hasUsableServerSession(existing)) return
      const draftSession = draftStateToSession(sessionId, state)
      if (!(draftSession.memory || []).length) return
      sessions.value = [
        draftSession,
        ...sessions.value.filter((item) => item.session_id !== sessionId),
      ].sort((a, b) => new Date(b.updated_at || 0) - new Date(a.updated_at || 0))
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
      upsertLocalDraftSession(sessionId, state)
    }

    // ── 图片全屏预览 ─────────────────────────────────────────────────────────
    const fullscreenImageUrl = ref('')
    const openImageFull = (url) => { fullscreenImageUrl.value = url }
    const closeImageFull = () => { fullscreenImageUrl.value = '' }

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
      if (!sessionId) return
      if (pendingSessionStates.value[sessionId]) {
        const next = { ...pendingSessionStates.value }
        delete next[sessionId]
        pendingSessionStates.value = next
      }
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
        // 非 localStorage 路径：异步重新拉取并注入图表卡片（不阻塞会话加载）
        restoreChartMessagesAsync(sessionId)
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
      const wasSelected = selectedSessionId.value === sessionId
      const response = await fetch(`${API_BASE}/user_sessions/${sessionId}?user_id=${encodeURIComponent(currentUser.value)}`, {
        method: 'DELETE',
        headers: getAuthHeaders(),
      })
      if (!response.ok && response.status !== 404) {
        window.alert('删除失败，请稍后重试。')
        return
      }
      clearPendingSessionState(sessionId)
      if (localStorage.getItem(lastSessionStorageKey()) === sessionId) {
        localStorage.removeItem(lastSessionStorageKey())
      }
      sessions.value = sessions.value.filter((item) => item.session_id !== sessionId)
      if (wasSelected) {
        selectedSessionId.value = ''
        chatMessages.value = []
        stageTimeline.value = []
        projectContext.value = { ...EMPTY_PROJECT_CONTEXT }
        latestAnalysisRef.value = null
        createNewSession()
      } else {
        await fetchUserSessions()
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

      // ── R codegen 静态 PNG 图片 ───────────────────────────────────────────
      if (normalizedKind === 'image_chart') {
        try {
          const payload = JSON.parse(text)
          const msgId = `img_chart_${Date.now()}_${Math.random().toString(36).slice(2, 7)}`
          chatMessages.value.push({
            id: msgId,
            type: 'image_chart',
            imageUrl: payload.image_url,
            metric: payload.metric || '',
            chartId: payload.chart_id || '',
          })
          scheduleVisibleSessionPersist(sessionId)
        } catch (e) {
          console.warn('image_chart parse error', e)
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
            // msg.spec 的结构为 {chart_id, spec: {data, layout}}，需取 .spec 层
            const plotlySpec = msg.spec.spec || msg.spec
            // 强制百分比指标 Y 轴从 0 开始，防止 LLM 自选 range 导致柱高视觉夸张
            const layout = { ...plotlySpec.layout, autosize: true, font: { family: 'Arial, sans-serif', color: '#334155' } }
            const yTitle = ((layout.yaxis?.title?.text ?? layout.yaxis?.title) || '').toString().toLowerCase()
            const isPercent = yTitle.includes('%') || yTitle.includes('percent') || yTitle.includes('rate')
            if (isPercent && layout.yaxis && !layout.yaxis.range) {
              layout.yaxis = { ...layout.yaxis, range: [0, 110] }
            }
            await window.Plotly.react(
              el,
              plotlySpec.data || [],
              layout,
              {
                responsive: true,
                displaylogo: false,
                displayModeBar: 'hover',          // 只在悬停时显示工具栏，减少视觉噪音
                modeBarButtonsToRemove: [
                  'sendDataToCloud', 'lasso2d', 'select2d', 'autoScale2d',
                ],
                toImageButtonOptions: { format: 'png', scale: 2 }, // 2x 高清导出
              },
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
      fullscreenImageUrl,
      openImageFull,
      closeImageFull,
    }
  },
}
</script>

<style scoped>
/* ══════════════════════════════════════════
   设计体系 — ChatGPT 同级亮色风格
   色盘：
     bg-app   #f9f9f9   极浅灰页面底
     bg-panel #ffffff   白色卡片/面板
     border   #e5e7eb   Tailwind gray-200
     text-pri #0d0d0d   近黑正文
     text-sec #6b7280   灰色辅助
     accent   #2563eb   蓝色强调
     green    #10b981   绿色 AI 标识
   ══════════════════════════════════════════ */

:global(:root) {
  --bg-app:            #f9f9f9;
  --bg-panel:          #ffffff;
  --bg-sidebar:        #f7f7f8;
  --border:            #e5e7eb;
  --border-strong:     #d1d5db;
  --text-pri:          #0d0d0d;
  --text-sec:          #6b7280;
  --text-muted:        #9ca3af;
  --accent:            #4e90ff;
  --accent-light:      #eef4ff;
  --accent-border:     #c7d9ff;
  --green:             #10b981;
  --green-light:       #ecfdf5;
  --red:               #ef4444;
  --yellow:            #f59e0b;
  --shadow-sm:         0 1px 3px rgba(0,0,0,.06), 0 1px 2px rgba(0,0,0,.04);
  --shadow-md:         0 4px 12px rgba(0,0,0,.08);
  --shadow-lg:         0 8px 24px rgba(0,0,0,.08), 0 2px 8px rgba(0,0,0,.04);
  --radius-sm:         8px;
  --radius-md:         12px;
  --radius-lg:         18px;
  --radius-xl:         24px;
  --chat-track-width:  min(1320px, calc(100vw - 180px));
  --assistant-width:   min(1040px, calc(100vw - 300px));
  --user-max-width:    min(620px, 70%);
}

:global(body) {
  margin: 0;
  min-width: 1240px;
  background: var(--bg-app);
  color: var(--text-pri);
  font-family: "Segoe UI Variable Display", "PingFang SC", "Microsoft YaHei UI",
               -apple-system, BlinkMacSystemFont, sans-serif;
  font-size: 15px;
  line-height: 1.6;
  -webkit-font-smoothing: antialiased;
}

* { box-sizing: border-box; }

/* ─── 页面外壳 ─── */
.app-shell {
  position: relative;
  min-height: 100vh;
  background: var(--bg-app);
  overflow: visible;
}

.backdrop-grid { display: none; }  /* 去掉网格纹 */

.login-shell,
.workspace {
  position: relative;
  z-index: 1;
}

/* ─── 登录页 ─── */
.login-shell {
  min-height: 100vh;
  display: grid;
  place-items: center;
  padding: 32px;
  background: linear-gradient(135deg, #eff6ff 0%, #f9f9f9 50%, #f0fdf4 100%);
}

.login-panel {
  width: min(1100px, 100%);
  display: grid;
  grid-template-columns: 1.35fr 400px;
  gap: 24px;
}

/* 面板通用边框/阴影 */
.login-copy,
.login-card {
  background: var(--bg-panel);
  border: 1px solid var(--border);
  box-shadow: var(--shadow-lg);
  border-radius: var(--radius-xl);
  padding: 36px;
}

.eyebrow {
  margin: 0 0 10px;
  font-size: 12px;
  font-weight: 600;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: var(--accent);
}

.login-copy h1 {
  margin: 0 0 14px;
  font-size: 40px;
  font-weight: 700;
  line-height: 1.1;
  color: var(--text-pri);
  letter-spacing: -0.02em;
}

.lead {
  margin: 0;
  max-width: 540px;
  line-height: 1.8;
  color: var(--text-sec);
}

.hero-cards {
  margin-top: 28px;
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 12px;
}

.hero-cards article {
  padding: 18px;
  border-radius: var(--radius-md);
  border: 1px solid var(--border);
  background: var(--bg-app);
}

.hero-cards strong {
  display: block;
  margin-bottom: 6px;
  color: var(--text-pri);
  font-size: 14px;
}

.hero-cards p {
  margin: 0;
  color: var(--text-sec);
  line-height: 1.65;
  font-size: 13px;
}

.login-card {
  display: flex;
  flex-direction: column;
  justify-content: center;
}

.brand-logo      { width: 56px; height: 56px; object-fit: contain; }
.brand-logo.small{ width: 36px; height: 36px; }

.card-tag {
  margin: 16px 0 4px;
  color: var(--accent);
  font-size: 11px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.08em;
}

.login-card h2 {
  margin: 0 0 16px;
  color: var(--text-pri);
  font-size: 24px;
  font-weight: 700;
}

.field-label {
  display: block;
  margin: 14px 0 6px;
  color: var(--text-pri);
  font-size: 14px;
  font-weight: 500;
}

.text-input,
.composer-input {
  width: 100%;
  border: 1px solid var(--border-strong);
  background: var(--bg-panel);
  color: var(--text-pri);
  border-radius: var(--radius-md);
  padding: 11px 14px;
  font-size: 15px;
  transition: border-color .15s, box-shadow .15s;
  outline: none;
}

.text-input::placeholder,
.composer-input::placeholder {
  color: var(--text-muted);
}

.text-input:focus,
.composer-input:focus {
  border-color: var(--accent);
  box-shadow: 0 0 0 3px rgba(37,99,235,.12);
}

/* ─── 按钮 ─── */
.primary-button,
.ghost-button,
.mode-button,
.mini-chip,
.quick-card,
.session-item {
  border-radius: var(--radius-lg);
  transition: background .15s, border-color .15s, box-shadow .15s, transform .12s;
  cursor: pointer;
  font-size: 14px;
}

.primary-button {
  border: none;
  background: var(--accent);
  color: #fff;
  padding: 10px 18px;
  font-weight: 600;
  box-shadow: 0 1px 3px rgba(37,99,235,.3);
}

.primary-button:hover  { background: #3b7cf8; box-shadow: var(--shadow-md); }
.primary-button:active { transform: translateY(1px); }

.ghost-button,
.mode-button {
  border: 1px solid var(--border);
  background: var(--bg-panel);
  color: var(--text-sec);
  padding: 8px 14px;
}

.ghost-button:hover,
.mode-button:hover { background: var(--bg-app); border-color: var(--border-strong); }

.mini-chip {
  border: 1px solid var(--border);
  background: var(--bg-panel);
  color: var(--text-sec);
  padding: 7px 14px;
  font-size: 13px;
}

.mini-chip:hover { background: var(--accent-light); border-color: var(--accent-border); color: var(--accent); }

.primary-button:disabled,
.ghost-button:disabled,
.mode-button:disabled {
  opacity: .45;
  cursor: not-allowed;
  box-shadow: none;
  transform: none;
}

.primary-button.wide { width: 100%; margin-top: 18px; }

.error-text   { color: var(--red);   font-size: 13px; margin: 6px 0 0; }
.success-text { color: var(--green); font-size: 13px; margin: 6px 0 0; }

.auth-switch {
  margin-top: 14px;
  text-align: center;
  font-size: 13px;
  color: var(--text-sec);
}

.switch-link { color: var(--accent); cursor: pointer; margin-left: 4px; text-decoration: underline; }
.switch-link:hover { color: #3b7cf8; }

/* ─── 工作区布局 ─── */
.workspace {
  min-height: 100vh;
  display: grid;
  grid-template-columns: 260px minmax(0, 1fr);
}

/* ─── 侧边栏 ─── */
.sidebar {
  background: var(--bg-sidebar);
  border-right: 1px solid var(--border);
  min-height: 100vh;
  padding: 16px 12px;
  display: flex;
  flex-direction: column;
  gap: 12px;
  overflow: clip;   /* 阻止侧向溢出但不截断子滚动条 */
}

/* 侧边栏继承自上面通用规则覆盖 */
.sidebar {
  box-shadow: none;
  border: none;
  border-right: 1px solid var(--border);
  border-radius: 0;
}

.sidebar-brand {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 10px 8px 14px;
  border-bottom: 1px solid var(--border);
}

.sidebar-brand-copy strong {
  display: block;
  color: var(--text-pri);
  font-size: 15px;
  font-weight: 700;
  line-height: 1.2;
}

.sidebar-brand-copy p {
  margin: 4px 0 0;
  font-size: 12px;
  color: var(--text-muted);
}

.sidebar-actions {
  display: flex;
  flex-direction: column;
  gap: 1px;
  padding: 2px 0 10px;
  border-bottom: 1px solid var(--border);
}

/* 侧边栏操作按钮：ChatGPT 风格扁平导航项 */
.sidebar-actions .primary-button,
.sidebar-actions .ghost-button {
  border: none !important;
  background: transparent !important;
  color: #0d0d0d !important;
  padding: 9px 10px;
  font-weight: 600;
  font-size: 14px;
  box-shadow: none !important;
  text-align: left;
  border-radius: 8px;
  width: 100%;
  display: flex;
  align-items: center;
  gap: 10px;
  transform: none !important;
}

.sidebar-actions .primary-button:hover,
.sidebar-actions .ghost-button:hover {
  background: rgba(0,0,0,.06) !important;
  color: var(--text-pri) !important;
}

/* 无图标前缀 */
.sidebar-actions .primary-button::before,
.sidebar-actions .ghost-button::before { content: none; }

.session-section {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-height: 0;
  flex: 1;
  overflow-y: scroll;   /* 始终显示滚动条轨道 */
  overflow-x: hidden;
  padding-right: 4px;
  scrollbar-width: thin;
  scrollbar-color: #c8cbd2 #f0f0f1;
}

.session-section::-webkit-scrollbar       { width: 5px; }
.session-section::-webkit-scrollbar-track { background: #f0f0f1; border-radius: 4px; }
.session-section::-webkit-scrollbar-thumb { background: #c8cbd2; border-radius: 4px; }
.session-section::-webkit-scrollbar-thumb:hover { background: #a8acb5; }

.section-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  color: var(--text-sec);
  font-size: 12px;
  font-weight: 600;
  padding: 4px 6px;
  text-transform: uppercase;
  letter-spacing: .06em;
}

.section-count {
  min-width: 24px;
  padding: 2px 7px;
  border-radius: 999px;
  background: var(--border);
  color: var(--text-sec);
  text-align: center;
  font-size: 11px;
}

.empty-side,
.empty-analysis {
  display: grid;
  place-items: center;
  min-height: 160px;
  padding: 20px;
  text-align: center;
  border-radius: var(--radius-md);
  border: 1px dashed var(--border-strong);
  color: var(--text-muted);
  font-size: 13px;
}

.empty-side::before {
  content: "◌";
  display: block;
  margin-bottom: 8px;
  color: var(--text-muted);
  font-size: 20px;
}

.session-item {
  border: 1px solid transparent;
  background: transparent;
  padding: 10px 10px 9px;
  text-align: left;
  cursor: pointer;
  border-radius: var(--radius-sm);
  position: relative;
}

.session-item:hover {
  background: var(--border);
  border-color: transparent;
}

.session-item.active {
  background: #e8e8e8;
  border-color: transparent;
  box-shadow: none;
}

.session-topline {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 6px;
  min-width: 0;
  overflow: hidden;
  width: 100%;
}

.topbar-actions,
.project-strip-main,
.project-strip-side,
.project-actions {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.session-preview {
  display: block;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  color: var(--text-pri);
  font-size: 13px;
  font-weight: 500;
  line-height: 1.4;
  min-width: 0;
  flex: 1;
}

.session-delete {
  border: 0;
  background: transparent;
  color: var(--text-muted);
  padding: 2px 6px;
  cursor: pointer;
  font-size: 11px;
  border-radius: 4px;
  flex: 0 0 auto;
  white-space: nowrap;
  opacity: 0;
  transition: opacity .15s, background .15s, color .15s;
}

.session-item:hover .session-delete { opacity: 1; }
.session-delete:hover { background: #fee2e2; color: var(--red); }

/* ─── 主面板 ─── */
.main-panel {
  min-height: 100vh;
  height: 100vh;
  display: flex;
  flex-direction: column;
  gap: 0;
  background: var(--bg-panel);
  border: none;
  border-radius: 0;
  box-shadow: none;
  padding: 0;
  overflow: hidden;
}

/* ─── 工作区顶部 / 头部 ─── */
.workspace-panel {
  border: none;
  border-bottom: 1px solid var(--border);
  background: var(--bg-panel);
  box-shadow: none;
  backdrop-filter: none;
}

.workspace-header {
  padding: 16px 24px;
  display: grid;
  gap: 0;
}

.workspace-header.compact { padding: 12px 20px; }

.workspace-header-main {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
}

.workspace-header.compact .workspace-header-main { align-items: center; }

.topbar-copy { display: grid; gap: 4px; max-width: 680px; }

.topbar-copy h2 {
  margin: 0;
  font-size: 22px;
  font-weight: 700;
  color: var(--text-pri);
  letter-spacing: -0.02em;
  line-height: 1.25;
}

.topbar-copy p {
  margin: 0;
  font-size: 13px;
  color: var(--text-sec);
  line-height: 1.7;
}

.topbar-kicker {
  display: inline-flex;
  align-items: center;
  margin-bottom: 6px;
  padding: 4px 10px;
  border-radius: 999px;
  background: var(--accent-light);
  border: 1px solid var(--accent-border);
  color: var(--accent);
  font-size: 11px;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  font-weight: 600;
}

.topbar-actions {
  align-items: center;
  gap: 10px;
  margin-left: auto;
}

.session-chip {
  padding: 4px 10px;
  border-radius: 999px;
  background: var(--bg-app);
  border: 1px solid var(--border);
  color: var(--text-muted);
  font-size: 11px;
}

.view-switch {
  display: flex;
  gap: 4px;
  padding: 3px;
  border-radius: 10px;
  background: var(--bg-app);
  border: 1px solid var(--border);
}

.mode-button {
  border-radius: 7px;
  border: 1px solid transparent;
  background: transparent;
  padding: 6px 14px;
  font-size: 13px;
  font-weight: 500;
  color: var(--text-sec);
}

.mode-button:hover { background: var(--bg-panel); color: var(--text-pri); }

.mode-button.active {
  background: var(--bg-panel);
  border-color: var(--border);
  color: var(--accent);
  font-weight: 600;
  box-shadow: var(--shadow-sm);
}

/* ─── 项目上下文面板 ─── */
.project-context-panel {
  padding: 12px 20px;
  display: grid;
  gap: 10px;
  background: var(--bg-panel);
  border-bottom: 1px solid var(--border);
}

.project-context-panel.compact { padding: 8px 16px; gap: 0; }

.project-context-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.project-context-copy    { display: grid; gap: 2px; }
.project-context-kicker  { color: var(--accent); font-size: 11px; font-weight: 600; letter-spacing: .06em; text-transform: uppercase; }

.project-context-copy strong {
  color: var(--text-pri);
  font-size: 15px;
  font-weight: 600;
}

.project-context-state {
  padding: 4px 12px;
  border-radius: 999px;
  border: 1px solid var(--border);
  background: var(--bg-app);
  color: var(--text-sec);
  font-size: 12px;
  white-space: nowrap;
}

.project-context-state.active {
  background: var(--accent-light);
  border-color: var(--accent-border);
  color: var(--accent);
  font-weight: 500;
}

.project-context-state.inline { padding: 3px 8px; font-size: 11px; }

.project-strip {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 10px 14px;
  border-radius: var(--radius-md);
  background: var(--bg-app);
  border: 1px solid var(--border);
}

.project-strip.compact  { padding: 8px 12px; }
.project-strip.idle     { border-style: dashed; }

.project-strip-main,
.project-strip-side { display: flex; align-items: center; gap: 8px; }
.project-strip-main { flex-wrap: wrap; }
.project-strip-side { flex: 1; justify-content: flex-end; min-width: 0; }

.project-strip-main strong { color: var(--text-pri); font-size: 16px; font-weight: 600; }

.project-label {
  padding: 3px 8px;
  border-radius: 999px;
  background: var(--bg-app);
  border: 1px solid var(--border);
  color: var(--text-sec);
  font-size: 11px;
}

.project-source { font-size: 11px; color: var(--text-muted); }

.project-path {
  max-width: 520px;
  text-align: right;
  word-break: break-all;
  font-size: 11px;
  color: var(--text-muted);
  line-height: 1.5;
}

.header-project-summary {
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 0;
  flex-wrap: wrap;
}

.header-project-summary strong { color: var(--text-pri); font-size: 17px; font-weight: 600; }
.header-clear-button { margin-left: 4px; padding-inline: 12px; }
.toolbar-button      { white-space: nowrap; }

/* ─── 消息滚动区域 ─── */
.workspace-body {
  flex: 1 1 auto;
  min-height: 0;
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
  gap: 4px;
  align-content: start;
  grid-auto-rows: max-content;
  padding: 24px 18px 16px;
  scrollbar-gutter: stable;
}

.content-scroll.empty-state { place-content: center; }

.content-scroll::-webkit-scrollbar        { width: 5px; }
.content-scroll::-webkit-scrollbar-track  { background: transparent; }
.content-scroll::-webkit-scrollbar-thumb  { background: #d8dce3; border-radius: 4px; }
.content-scroll::-webkit-scrollbar-thumb:hover { background: #b8bdc6; }

.chat-empty-state {
  display: grid;
  justify-items: center;
  gap: 8px;
  color: var(--text-sec);
  text-align: center;
}

.chat-empty-state strong { color: var(--text-pri); font-size: 18px; }
.chat-empty-state p      { margin: 0; font-size: 13px; }

/* ─── 时间线消息 ─── */
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

/* 消息卡片 */
.message-card {
  position: relative;
  border-radius: var(--radius-md);
  padding: 10px 14px;
  min-width: 0;
}

/* 用户消息：蓝色渐变气泡，右对齐 */
.message-card.user {
  border: none;
  background: #f4f4f4;
  color: var(--text-pri);
  width: fit-content;
  min-width: auto;
  max-width: min(var(--user-max-width), 100%);
  margin-left: auto;
  border-radius: 18px 4px 18px 18px;
  box-shadow: none;
}

.message-card.user .message-bubble {
  overflow-wrap: break-word;
  word-break: normal;
  color: var(--text-pri);
}

.message-card.user .markdown-body,
.message-card.user .markdown-body :deep(p) {
  overflow-wrap: break-word;
  word-break: normal;
  margin: 0;
}

.message-card.user .markdown-body :deep(> :last-child) {
  margin-bottom: 0;
}

/* 用户气泡内文字深色 */
.message-card.user .markdown-body,
.message-card.user .markdown-body :deep(p),
.message-card.user .markdown-body :deep(li),
.message-card.user .markdown-body :deep(h1),
.message-card.user .markdown-body :deep(h2),
.message-card.user .markdown-body :deep(h3),
.message-card.user .markdown-body :deep(strong),
.message-card.user .markdown-body :deep(b) {
  color: var(--text-pri);
}

.message-card.user .markdown-body :deep(code) {
  background: #e8e8e8;
  color: var(--text-pri);
}

/* AI 回复：白底，无彩色左边框 */
.message-card.assistant {
  border: 1px solid var(--border);
  border-radius: 4px 18px 18px 18px;
  padding: 16px 20px 18px 72px;
  width: var(--assistant-width);
  max-width: 100%;
  background: var(--bg-panel);
  box-shadow: var(--shadow-sm);
}

/* Vazyme 标识 */
.message-card.assistant::before {
  content: "Vazyme";
  position: absolute;
  left: 14px;
  top: 15px;
  display: grid;
  place-items: center;
  width: auto;
  padding: 0 8px;
  height: 24px;
  border-radius: 6px;
  color: #fff;
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 0.4px;
  background: linear-gradient(145deg, #4e90ff, #3b7cf8);
  box-shadow: 0 2px 6px rgba(78,144,255,.28);
}

@keyframes vazyme-pulse {
  0%, 100% { opacity: 1; box-shadow: 0 2px 8px rgba(78,144,255,.35); }
  50%       { opacity: .7; box-shadow: 0 2px 14px rgba(78,144,255,.6); }
}

@keyframes thinking-spin {
  from { transform: rotate(0deg); }
  to   { transform: rotate(360deg); }
}

/* 思考中：保持 Vazyme 文字，加呼吸脉冲动画 */
.message-card.assistant.thinking::before {
  content: "Vazyme";
  animation: vazyme-pulse 1.4s ease-in-out infinite;
}

/* 移除旋转时的中心点 */
.message-card.assistant.thinking::after { display: none; }

/* 过程卡片 */
.message-card.process {
  border-left: 2px solid var(--yellow);
  width: 100%;
  max-width: 100%;
  background: #fffbeb;
  border-radius: 4px 12px 12px 4px;
  padding: 10px 14px;
}

/* 图表卡片 */
.message-card.chart-card {
  width: 100%;
  max-width: 100%;
  background: var(--bg-panel);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  padding: 14px 16px 10px;
  box-shadow: var(--shadow-sm);
}

.message-card.chart-card .message-label span { color: var(--accent); }

.plotly-container { width: 100%; min-height: 380px; border-radius: 6px; overflow: visible; }

.image-chart-wrap { width: 100%; text-align: center; }
.chart-img { max-width: 100%; border-radius: 6px; cursor: zoom-in; transition: opacity .15s; }
.chart-img:hover { opacity: .88; }
.chart-download-btn { margin-left: 10px; font-size: 12px; color: var(--accent); text-decoration: none; opacity: .8; }
.chart-download-btn:hover { opacity: 1; }
.img-fullscreen-mask { position: fixed; inset: 0; background: rgba(0,0,0,.82); z-index: 9999;
  display: flex; align-items: center; justify-content: center; cursor: zoom-out; }
.img-fullscreen-mask img { max-width: 92vw; max-height: 92vh; border-radius: 8px; }

.message-label {
  margin-bottom: 6px;
  color: var(--text-muted);
  font-size: 11px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: .06em;
}

.message-card.assistant .message-label { margin-bottom: 10px; }
.message-card.user .message-label { color: var(--text-muted); }

.process-label {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.process-badge {
  padding: 3px 9px;
  border-radius: 999px;
  background: var(--bg-app);
  border: 1px solid var(--border);
  color: var(--text-muted);
  font-size: 11px;
}

.process-badge.active {
  background: var(--accent-light);
  border-color: var(--accent-border);
  color: var(--accent);
}

.message-bubble { padding: 2px 0; min-width: 0; max-width: 100%; }

/* 思考状态 */
.thinking-state {
  display: flex;
  align-items: center;
  gap: 10px;
  min-height: 46px;
  padding: 10px 14px;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  background: var(--bg-app);
}

.thinking-spinner {
  position: relative;
  flex: 0 0 auto;
  width: 18px; height: 18px;
  border-radius: 50%;
  background:
    radial-gradient(circle, #ecfdf5 48%, transparent 50%),
    conic-gradient(from 0deg, rgba(16,185,129,0), #10b981, #34d399, rgba(16,185,129,0));
  animation: thinking-spin 0.9s linear infinite;
}

.thinking-spinner::after {
  content: '';
  position: absolute;
  top: 0; left: 50%;
  width: 4px; height: 4px;
  border-radius: 50%;
  background: #059669;
  box-shadow: 0 0 8px rgba(16,185,129,.6);
  transform: translateX(-50%);
}

.thinking-copy          { min-width: 0; }
.thinking-copy strong   { display: block; color: var(--text-pri); font-size: 14px; margin-bottom: 2px; }
.thinking-copy p        { margin: 0; color: var(--text-sec); line-height: 1.5; font-size: 12px; }

/* 过程面板 */
.process-panel {
  border-radius: var(--radius-lg);
  padding: 14px;
  background: var(--bg-app);
  border: 1px solid var(--border);
  box-shadow: none;
}

.process-core { display: flex; align-items: center; gap: 12px; }
.process-core strong { display: block; margin-bottom: 3px; }
.process-core p { margin: 0; }

.process-orb {
  width: 12px; height: 12px;
  border-radius: 50%;
  background: var(--border-strong);
  flex: 0 0 auto;
}

@keyframes pulse {
  0%   { box-shadow: 0 0 0 0 rgba(37,99,235,.4); }
  70%  { box-shadow: 0 0 0 10px rgba(37,99,235,0); }
  100% { box-shadow: 0 0 0 0 rgba(37,99,235,0); }
}

.process-orb.active { background: var(--accent); animation: pulse 1.6s infinite; }
.process-meta { color: var(--text-muted); font-size: 12px; }

.stage-compact { margin-top: 10px; display: grid; gap: 8px; }

.stage-current {
  display: grid;
  grid-template-columns: 10px 1fr auto;
  gap: 10px;
  align-items: center;
  padding: 8px 12px;
  border-radius: var(--radius-sm);
  background: var(--bg-panel);
  border: 1px solid var(--border);
}

.stage-current .stage-dot { margin-top: 0; }

.stage-current.in_progress {
  border-color: var(--accent-border);
  background: var(--accent-light);
}

.stage-current.error { border-color: #fca5a5; background: #fef2f2; }

.stage-trail { display: flex; flex-wrap: wrap; gap: 6px; }

.stage-chip {
  max-width: 190px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  padding: 3px 9px;
  border-radius: 999px;
  font-size: 11.5px;
  background: var(--bg-app);
  border: 1px solid var(--border);
  color: var(--text-muted);
}

.stage-chip.completed {
  color: #059669;
  border-color: #a7f3d0;
  background: #ecfdf5;
}

.stage-chip.in_progress {
  color: var(--accent);
  border-color: var(--accent-border);
  background: var(--accent-light);
}

.stage-list  { display: grid; gap: 8px; margin-top: 12px; }

.stage-item {
  display: grid;
  grid-template-columns: 12px 1fr auto;
  gap: 12px;
  align-items: start;
  padding: 8px 12px;
  border-radius: var(--radius-sm);
  background: var(--bg-panel);
  border: 1px solid var(--border);
}

.stage-dot {
  width: 8px; height: 8px;
  border-radius: 50%;
  margin-top: 5px;
  background: var(--border-strong);
  flex: 0 0 auto;
}

.stage-item.in_progress .stage-dot { background: var(--accent); }
.stage-item.completed   .stage-dot { background: var(--green); }

.stage-copy strong  { display: block; margin-bottom: 3px; color: var(--text-pri); font-size: 14px; }
.stage-copy p       { margin: 0; color: var(--text-sec); font-size: 13px; line-height: 1.5; }

.process-log        { margin-top: 12px; }
.process-log-header { font-size: 14px; color: var(--text-pri); }

/* ─── 分析视图 ─── */
.analysis-view { display: grid; gap: 14px; padding: 20px; }

.analysis-overview {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 10px;
}

.overview-card {
  padding: 18px;
  border-radius: var(--radius-lg);
  border: 1px solid var(--border);
  background: var(--bg-panel);
  box-shadow: var(--shadow-sm);
}

.overview-card.highlight {
  border-color: var(--accent-border);
  background: var(--accent-light);
}

.overview-card span   { display: block; color: var(--text-sec); font-size: 12px; margin-bottom: 8px; }
.overview-card strong { display: block; color: var(--text-pri); font-size: 22px; line-height: 1.1; margin-bottom: 6px; }
.overview-card p      { margin: 0; color: var(--text-muted); font-size: 12px; }

.analysis-grid  { display: grid; grid-template-columns: repeat(2, 1fr); gap: 12px; }

.analysis-panel {
  border-radius: var(--radius-lg);
  padding: 18px;
  background: var(--bg-panel);
  border: 1px solid var(--border);
  box-shadow: var(--shadow-sm);
}

.analysis-panel.full-width { grid-column: 1 / -1; }

.ai-report-panel { display: grid; gap: 14px; }

.ai-report-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 14px;
  padding-bottom: 12px;
  border-bottom: 1px solid var(--border);
}

.ai-report-head h3 { margin: 0; color: var(--text-pri); font-size: 16px; font-weight: 700; }
.ai-report-head p  { margin: 4px 0 0; color: var(--text-sec); font-size: 13px; }

.report-time {
  flex: 0 0 auto;
  color: var(--text-muted);
  font-size: 11px;
  padding: 4px 10px;
  border-radius: 999px;
  background: var(--bg-app);
  border: 1px solid var(--border);
}

.analysis-kv {
  padding: 8px 0;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  border-bottom: 1px solid var(--border);
}

.analysis-kv:last-child { border-bottom: none; }
.analysis-kv span       { color: var(--text-sec); font-size: 13px; }
.analysis-kv strong     { color: var(--text-pri); text-align: right; word-break: break-word; }

.analysis-list { display: grid; gap: 6px; padding-left: 18px; color: var(--text-pri); }

/* ─── Composer (输入区) ─── */
.workspace-composer {
  flex: 0 0 auto;
  position: static;
  z-index: 2;
  width: var(--chat-track-width);
  max-width: 100%;
  margin-inline: auto;
  padding: 0 18px 16px;
}

.workspace-composer.compact { padding-top: 2px; }

.composer-shell {
  border-radius: var(--radius-xl);
  padding: 10px;
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  gap: 10px;
  align-items: stretch;
  background: var(--bg-panel);
  border: 1px solid var(--border-strong);
  box-shadow: var(--shadow-md);
}

/* compact 状态：pill 搜索栏 */
.composer-shell.compact {
  border-radius: 999px;
  padding: 6px 6px 6px 22px;
  gap: 0;
}

.composer-main  { display: grid; gap: 8px; }

/* compact entry：flex 单行 */
.composer-shell.compact .composer-main { gap: 0; }

.composer-entry {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 90px;
  gap: 8px;
  align-items: stretch;
}

.composer-shell.compact .composer-entry {
  display: flex;
  align-items: center;
  gap: 8px;
  grid-template-columns: unset;
}

.composer-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 4px 4px 0;
}

.composer-head strong {
  color: var(--text-pri);
  font-size: 17px;
  font-weight: 700;
  letter-spacing: -0.01em;
}

.composer-head span { color: var(--text-muted); font-size: 11px; }

.composer-input {
  min-height: 52px;
  resize: none;
  padding: 13px 14px;
  background: var(--bg-app);
  border-color: var(--border);
  line-height: 1.55;
  border-radius: var(--radius-md);
  font-size: 15px;
}

/* compact 单行输入 */
.composer-shell.compact .composer-input {
  flex: 1;
  min-height: unset;
  height: 42px;
  max-height: 42px;
  padding: 0;
  border: none;
  border-radius: 0;
  background: transparent;
  box-shadow: none;
  resize: none;
  overflow: hidden;
  line-height: 42px;
}

.composer-shell.compact .composer-input:focus {
  border: none;
  box-shadow: none;
}

.composer-side {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  flex-shrink: 0;
}

.send-button {
  min-width: 80px;
  min-height: 44px;
  border-radius: var(--radius-md);
  font-size: 14px;
  font-weight: 600;
}

/* compact / landing 共用：黑色圆形发送按钮 */
.composer-shell.compact .send-button {
  flex-shrink: 0;
  width: 40px;
  height: 40px;
  min-width: unset;
  min-height: unset;
  padding: 0;
  border-radius: 50%;
  background: #0d0d0d;
  color: #ffffff;
  font-size: 0;
  box-shadow: none;
}

.composer-shell.compact .send-button::after {
  content: "↑";
  font-size: 18px;
  font-weight: 700;
  line-height: 1;
  color: #ffffff;
}

.composer-shell.compact .send-button:disabled {
  background: #e8e8e8;
}

.composer-shell.compact .send-button:disabled::after {
  color: #aaa;
}

.composer-shell.compact .send-button:hover:not(:disabled) {
  background: #222;
  box-shadow: none;
  transform: none;
}

.composer-shortcuts {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
  padding: 0 2px;
}

.project-actions .ghost-button { padding: 6px 12px; font-size: 13px; }

/* ─── Markdown 渲染 ─── */
.markdown-body {
  color: var(--text-pri);
  font-size: 15px;
  line-height: 1.75;
  word-break: break-word;
  overflow-wrap: anywhere;
  max-width: 100%;
  overflow-x: hidden;
  min-width: 0;
}

.message-bubble.markdown-body { overflow-x: visible; overflow-y: hidden; }

.markdown-body :deep(p),
.markdown-body :deep(li),
.markdown-body :deep(blockquote) { max-width: 100%; overflow-wrap: anywhere; }

.markdown-body :deep(p) { margin: 0 0 10px; }

.markdown-body :deep(strong),
.markdown-body :deep(b) { font-size: inherit; line-height: inherit; font-weight: 650; }

.markdown-body :deep(h1),
.markdown-body :deep(h2),
.markdown-body :deep(h3),
.markdown-body :deep(h4) {
  margin: 16px 0 8px;
  color: var(--text-pri);
  font-weight: 700;
  line-height: 1.3;
}

.markdown-body :deep(h1) { font-size: 20px; }
.markdown-body :deep(h2) { font-size: 17px; }
.markdown-body :deep(h3) { font-size: 15.5px; }
.markdown-body :deep(h4) { font-size: 14.5px; }

.markdown-body :deep(ul),
.markdown-body :deep(ol) { margin: 6px 0 10px; padding-left: 22px; }

.markdown-body :deep(li + li) { margin-top: 4px; }

.markdown-body :deep(pre) {
  overflow: auto;
  max-width: 100%;
  padding: 14px 16px;
  border-radius: var(--radius-md);
  background: #f3f4f6;
  border: 1px solid var(--border);
  margin: 8px 0 12px;
}

.markdown-body :deep(code) {
  font-family: Consolas, "Fira Code", Monaco, monospace;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  word-break: break-word;
  font-size: 0.9em;
  background: #f3f4f6;
  padding: 1px 5px;
  border-radius: 4px;
}

.markdown-body :deep(pre code) { background: none; padding: 0; }

.markdown-body :deep(blockquote) {
  border-left: 3px solid var(--border-strong);
  margin: 8px 0;
  padding: 8px 14px;
  color: var(--text-sec);
  background: var(--bg-app);
  border-radius: 0 var(--radius-sm) var(--radius-sm) 0;
}

.markdown-body :deep(img) {
  display: block;
  max-width: min(100%, 720px);
  max-height: 520px;
  object-fit: contain;
  margin: 12px 0;
  border-radius: var(--radius-md);
  border: 1px solid var(--border);
  box-shadow: var(--shadow-sm);
}

/* ─── 表格 ─── */
.markdown-body :deep(table) {
  width: 100%;
  min-width: max(100%, 560px);
  table-layout: fixed;
  border-collapse: collapse;
  border-spacing: 0;
}

.markdown-body :deep(.markdown-table-scroll > .markdown-data-table) {
  width: 100% !important;
  min-width: max(100%, 560px) !important;
  table-layout: fixed;
}

.markdown-body :deep(.markdown-table-scroll) {
  display: block;
  width: 100%;
  max-width: 100%;
  overflow-x: auto;
  overflow-y: hidden;
  margin: 12px 0 14px;
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  background: var(--bg-panel);
  box-shadow: var(--shadow-sm);
}

.markdown-body :deep(th),
.markdown-body :deep(td) {
  padding: 10px 14px;
  font-size: 13.5px;
  white-space: normal;
  overflow-wrap: anywhere;
  word-break: break-word;
  vertical-align: top;
  border: 1px solid var(--border);
}

.markdown-body :deep(th) {
  color: var(--text-pri) !important;
  font-weight: 650;
  background: #f3f4f6 !important;
  font-size: 13px;
  text-align: left;
}

.markdown-body :deep(td) {
  color: var(--text-pri) !important;
  background: var(--bg-panel) !important;
}

.markdown-body :deep(tr:nth-child(even) td) {
  background: #fafafa !important;
}

.markdown-body :deep(tr:hover td) {
  background: var(--accent-light) !important;
}

/* ─── Thinking track（处理链路 · 时间线）─── */
.thinking-track {
  width: 100%;
  padding: 10px 2px;
  display: grid;
  gap: 8px;
}

.thinking-head {
  display: flex;
  align-items: center;
  gap: 8px;
}

.thinking-title { color: var(--text-sec); font-size: 12px; }
.thinking-meta-count { color: var(--text-muted); font-size: 11px; margin-left: auto; }

.thinking-timeline { display: flex; flex-direction: column; }

.tl-item {
  display: flex;
  gap: 10px;
}

.tl-left {
  display: flex;
  flex-direction: column;
  align-items: center;
  width: 14px;
  flex-shrink: 0;
}

.tl-dot {
  width: 8px; height: 8px;
  border-radius: 50%;
  background: var(--border-strong);
  flex-shrink: 0;
  margin-top: 6px;
  transition: background .3s, box-shadow .3s;
}

.tl-dot.completed  { background: #059669; }
.tl-dot.in_progress { background: var(--accent); box-shadow: 0 0 0 3px rgba(37,99,235,.12); animation: pulse 1.6s infinite; }
.tl-dot.error      { background: var(--red); }

.tl-line {
  width: 1px;
  flex: 1;
  background: var(--border);
  margin-top: 4px;
  min-height: 12px;
}

.tl-label {
  font-size: 14px;
  color: var(--text-muted);
  line-height: 1.6;
  padding-bottom: 10px;
}

.tl-label.active { color: var(--text-pri); }

.tl-item.last .tl-label { padding-bottom: 2px; }

.tl-body {
  display: flex;
  flex-direction: column;
  gap: 3px;
  padding-bottom: 2px;
}

.tl-sub { font-size: 13px; color: var(--text-muted); line-height: 1.5; }

.thinking-footer {
  color: var(--text-muted);
  font-size: 12px;
  line-height: 1.5;
  padding-top: 2px;
}

/* ─── 英雄区 / 快速卡片 ─── */
.hero-board {
  position: relative;
  border-radius: var(--radius-xl);
  padding: 28px;
  background: var(--bg-panel);
  border: 1px solid var(--border);
  box-shadow: var(--shadow-sm);
  min-height: 0;
  display: grid;
  grid-template-columns: minmax(250px, 0.9fr) minmax(0, 1.2fr);
  gap: 24px;
  align-items: stretch;
  width: min(1200px, 100%);
  margin-inline: auto;
}

.launchpad-copy {
  display: flex;
  flex-direction: column;
  justify-content: center;
  padding: 8px 16px 8px 4px;
  border-right: 1px solid var(--border);
  position: relative;
  z-index: 1;
}

.launchpad-kicker {
  display: inline-flex;
  align-items: center;
  width: fit-content;
  margin-bottom: 14px;
  padding: 5px 12px;
  border-radius: 999px;
  background: var(--accent-light);
  border: 1px solid var(--accent-border);
  color: var(--accent);
  font-size: 11px;
  font-weight: 600;
  letter-spacing: .07em;
  text-transform: uppercase;
}

.launchpad-copy h3 {
  margin: 0 0 10px;
  color: var(--text-pri);
  font-size: 24px;
  line-height: 1.28;
  letter-spacing: -0.02em;
  font-weight: 700;
}

.launchpad-copy p {
  margin: 0;
  max-width: 32ch;
  color: var(--text-sec);
  font-size: 13px;
  line-height: 1.68;
}

.launchpad-tags { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 16px; }

.launchpad-tags span {
  padding: 6px 12px;
  border-radius: 999px;
  background: var(--bg-app);
  border: 1px solid var(--border);
  color: var(--text-sec);
  font-size: 12px;
}

.quick-actions {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 12px;
  align-items: stretch;
  align-self: center;
  position: relative;
  z-index: 1;
}

.quick-card {
  position: relative;
  border: 1px solid var(--border);
  background: var(--bg-panel);
  padding: 20px;
  text-align: left;
  cursor: pointer;
  min-height: 180px;
  border-radius: var(--radius-lg);
  overflow: hidden;
  box-shadow: var(--shadow-sm);
  transition: transform .18s, border-color .18s, box-shadow .18s;
}

.quick-card::before {
  content: "";
  position: absolute;
  top: 0; left: 0; right: 0;
  height: 3px;
  background: linear-gradient(90deg, var(--card-accent), rgba(255,255,255,0));
}

.quick-card:nth-child(1) { --card-accent: #3b82f6; }
.quick-card:nth-child(2) { --card-accent: #0ea5e9; }
.quick-card:nth-child(3) { --card-accent: #8b5cf6; }

.quick-card-head  { display: flex; align-items: center; gap: 8px; margin-bottom: 8px; }

.quick-card-icon {
  display: inline-grid;
  place-items: center;
  width: 26px; height: 26px;
  border-radius: 6px;
  background: color-mix(in srgb, var(--card-accent) 10%, transparent);
  border: 1px solid color-mix(in srgb, var(--card-accent) 25%, transparent);
  color: var(--card-accent);
  font-size: 12px;
}

.quick-card-type {
  display: inline-block;
  color: var(--card-accent);
  font-size: 11px;
  font-weight: 600;
  letter-spacing: .07em;
  text-transform: uppercase;
}

.quick-card strong {
  display: block;
  margin-bottom: 8px;
  color: var(--text-pri);
  font-size: 17px;
  line-height: 1.28;
  font-weight: 700;
}

.quick-card p {
  margin: 0;
  color: var(--text-sec);
  line-height: 1.65;
  font-size: 13px;
}

.quick-card-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-top: 16px;
  padding-top: 12px;
  border-top: 1px solid var(--border);
  color: var(--text-sec);
  font-size: 12px;
}

.quick-card-foot span { font-size: 14px; color: var(--card-accent); }

.quick-card:hover {
  transform: translateY(-2px);
  border-color: var(--border-strong);
  box-shadow: var(--shadow-md);
}

.quick-card:active { transform: translateY(0); }

/* ─── Hero panel ─── */
.hero-panel {
  border-radius: var(--radius-xl);
  padding: 22px;
  display: grid;
  gap: 18px;
  min-height: 320px;
}

.hero-panel-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 18px;
}

.hero-copy          { display: grid; gap: 8px; max-width: 680px; }
.hero-copy h3       { margin: 0; font-size: 28px; line-height: 1.15; color: var(--text-pri); font-weight: 700; }
.hero-copy p        { margin: 0; color: var(--text-sec); line-height: 1.7; }

.hero-signal {
  min-width: 160px;
  padding: 12px 14px;
  border-radius: var(--radius-md);
  background: var(--bg-app);
  border: 1px solid var(--border);
  display: grid;
  gap: 4px;
}

.hero-signal span,
.launchpad-kicker,
.capability-type,
.hero-hint-label {
  color: var(--text-muted);
  font-size: 11px;
  font-weight: 600;
  letter-spacing: .07em;
  text-transform: uppercase;
}

.hero-signal strong { color: var(--text-pri); font-size: 17px; line-height: 1.2; }

.capability-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; }

.action-card {
  border: 1px solid var(--border);
  background: var(--bg-panel);
  box-shadow: var(--shadow-sm);
}

.capability-card {
  border-radius: var(--radius-xl);
  padding: 18px;
  display: grid;
  gap: 14px;
  text-align: left;
  min-height: 200px;
}

.capability-card-top,
.capability-card-foot { display: flex; align-items: center; justify-content: space-between; gap: 10px; }

.capability-icon {
  width: 34px; height: 34px;
  border-radius: var(--radius-sm);
  display: grid;
  place-items: center;
  background: var(--accent-light);
  color: var(--accent);
  font-size: 17px;
  border: 1px solid var(--accent-border);
}

.capability-card-body       { display: grid; gap: 6px; }
.capability-card-body strong{ color: var(--text-pri); font-size: 20px; line-height: 1.2; font-weight: 700; }
.capability-card-body p,
.hero-hint p,
.composer-side-meta p       { margin: 0; color: var(--text-sec); line-height: 1.65; }

.capability-card-foot { color: var(--text-sec); font-size: 13px; }
.capability-arrow     { color: var(--accent); font-size: 17px; }

.hero-panel-foot {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 16px;
  margin-top: auto;
}

.hero-hint  { display: grid; gap: 6px; max-width: 420px; }

.hero-tags,
.composer-shortcuts { display: flex; gap: 6px; flex-wrap: wrap; }

.hero-tags span {
  padding: 6px 12px;
  border-radius: 999px;
  border: 1px solid var(--border);
  background: var(--bg-app);
  color: var(--text-sec);
  font-size: 12px;
}

/* ─── 内联标签 / 芯片 ─── */
.session-chip,
.process-badge,
.stage-status,
.project-label { white-space: nowrap; }

/* ─── 空状态 ─── */
.empty-analysis {
  grid-column: 1 / -1;
  padding: 40px;
  min-height: 200px;
}

.empty-analysis h3 { margin: 0 0 8px; color: var(--text-pri); font-size: 18px; }
.empty-analysis p  { margin: 0; color: var(--text-sec); font-size: 14px; }

/* ────────────────────────────────────────────────────────────────
   Landing 起始页（空会话）— 居中搜索框风格，参考图1
   ──────────────────────────────────────────────────────────────── */

/* 主容器：垂直居中 */
.main-panel.landing {
  justify-content: center;
  gap: 0;
}

/* 隐藏顶部标题栏和项目上下文面板 */
.main-panel.landing .workspace-header,
.main-panel.landing .project-context-panel { display: none; }

/* 消息区域：不占 flex 空间，仅包裹标题 */
.main-panel.landing .workspace-body {
  flex: 0 0 auto;
  overflow: visible;
  min-height: 0;
  padding: 0 0 28px;
  width: min(720px, 92%);
  margin-inline: auto;
  display: block;
  place-content: unset;
  scrollbar-gutter: unset;
}

/* 标题文字 */
.main-panel.landing .chat-empty-state {
  justify-items: center;
  gap: 0;
  padding: 0;
}

.main-panel.landing .chat-empty-state strong {
  font-size: 26px;
  font-weight: 600;
  color: var(--text-pri);
  letter-spacing: -0.02em;
}

/* 输入框区域：居中、搜索栏风格 */
.main-panel.landing .workspace-composer {
  position: relative;
  width: min(720px, 92%);
  padding: 0;
  margin-inline: auto;
}

/* pill 形外壳 */
.main-panel.landing .composer-shell {
  border-radius: 999px;
  padding: 6px 6px 6px 22px;
  background: #ffffff;
  border: 1px solid var(--border-strong);
  box-shadow: 0 2px 14px rgba(0,0,0,.08);
  gap: 0;
}

.main-panel.landing .composer-main { gap: 0; }
.main-panel.landing .composer-head { display: none; }

/* 输入行：flex 单行 */
.main-panel.landing .composer-entry {
  display: flex;
  align-items: center;
  gap: 8px;
  grid-template-columns: unset;
}

/* textarea 改为单行风格 */
.main-panel.landing .composer-input {
  flex: 1;
  min-height: unset;
  height: 42px;
  max-height: 42px;
  padding: 0;
  border: none;
  border-radius: 0;
  background: transparent;
  box-shadow: none;
  resize: none;
  overflow: hidden;
  line-height: 42px;
  font-size: 15px;
}

.main-panel.landing .composer-input:focus {
  border: none;
  box-shadow: none;
}

/* 发送按钮：黑色圆形 */
.main-panel.landing .send-button {
  flex-shrink: 0;
  width: 40px;
  height: 40px;
  min-width: unset;
  min-height: unset;
  padding: 0;
  border-radius: 50%;
  background: #0d0d0d;
  color: #ffffff;
  font-size: 0;        /* 隐藏"发送"文字 */
  box-shadow: none;
}

.main-panel.landing .send-button::after {
  content: "↑";
  font-size: 18px;
  font-weight: 700;
  line-height: 1;
  color: #ffffff;
}

.main-panel.landing .send-button:disabled {
  background: #e8e8e8;
  color: #999;
}

.main-panel.landing .send-button:disabled::after {
  color: #aaa;
}

.main-panel.landing .send-button:hover:not(:disabled) {
  background: #222;
  box-shadow: none;
  transform: none;
}

/* 快捷芯片（landing + compact 共用） */
.landing-chips {
  justify-content: flex-start;
  padding: 10px 4px 0;
  flex-wrap: wrap;
  gap: 6px;
}

.main-panel.landing .landing-chips {
  justify-content: center;
  padding: 14px 0 0;
  flex-wrap: nowrap;
}

.landing-chips .mini-chip {
  border-radius: 999px;
  border: 1px solid var(--border-strong);
  background: var(--bg-panel);
  color: var(--text-sec);
  font-size: 13px;
  padding: 7px 16px;
}

/* ─── 响应式 ─── */
@media (prefers-reduced-motion: reduce) {
  .thinking-spinner,
  .thinking-orb.active,
  .current-dot.in_progress,
  .process-orb.active,
  .message-card.assistant.thinking::before { animation: none; opacity: 1; }
}

@media (max-width: 1360px) {
  :global(body) { min-width: 0; }

  .login-panel,
  .hero-cards,
  .analysis-overview,
  .analysis-grid,
  .hero-board,
  .quick-actions,
  .capability-grid { grid-template-columns: 1fr; }

  .workspace-header-main,
  .hero-panel-head,
  .hero-panel-foot,
  .project-context-head,
  .project-strip,
  .project-strip-main,
  .project-strip-side,
  .project-actions,
  .composer-head {
    flex-direction: column;
    align-items: flex-start;
  }

  .composer-entry { grid-template-columns: 1fr; }
  .project-path   { max-width: none; text-align: left; }

  .timeline-item.user,
  .message-card.assistant { width: 100%; max-width: 100%; }
  .message-card.user      { max-width: min(480px, 100%); }

  .topbar-actions,
  .hero-tags,
  .composer-shortcuts { flex-direction: column; align-items: flex-start; }

  .launchpad-copy { border-right: none; border-bottom: 1px solid var(--border); padding: 0 0 16px; }
}

@media (max-width: 980px) {
  .workspace { grid-template-columns: 1fr; }
  .sidebar   { min-height: auto; border-right: none; border-bottom: 1px solid var(--border); }
  .main-panel{ min-height: auto; height: auto; }
}
</style>
