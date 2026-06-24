<template>
  <div class="kb-chat-page">
    <div class="chat-box">
      <!-- 头部 -->
      <div class="chat-header">
        <div class="header-copy">
          <h2>知识库问答</h2>
          <p>基于知识库文档检索直接回答，不经过 Agent 推理流程。</p>
        </div>
        <div class="header-tools">
          <el-button text type="warning" @click="handleClear">清空对话</el-button>
        </div>
      </div>

      <!-- 消息列表 -->
      <div ref="messagesRef" class="messages">
        <div v-if="messages.length === 0" class="empty-state">
          <el-icon :size="60" color="#30363d"><ChatDotRound /></el-icon>
          <p>输入问题，将从知识库中检索相关文档后直接回答。</p>
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
                <span></span><span></span><span></span>
                <em>正在检索知识库...</em>
              </div>
              <div v-else v-html="formatContent(msg.content)"></div>
            </div>
          </div>
        </div>
      </div>

      <!-- 输入区 -->
      <div class="input-area">
        <div class="input-wrapper">
          <el-input
            v-model="input"
            placeholder="输入问题，Enter 发送 / Shift+Enter 换行"
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
import { nextTick, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { ChatDotRound, Position, Service, User } from '@element-plus/icons-vue'
import { marked } from 'marked'
import { queryKnowledgeRag } from '@/api/knowledge'

const input = ref('')
const loading = ref(false)
const messages = ref([])
const messagesRef = ref(null)

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

function handleClear() {
  messages.value = []
}

async function handleSend() {
  if (!input.value.trim() || loading.value) return

  const question = input.value.trim()
  input.value = ''

  messages.value.push({ role: 'user', content: question })
  const assistantMessage = { role: 'assistant', content: '', loading: true }
  messages.value.push(assistantMessage)
  scrollToBottom()

  loading.value = true
  try {
    const res = await queryKnowledgeRag(question)
    assistantMessage.content = res.answer || '未检索到相关文档，无法回答。'
    assistantMessage.loading = false
  } catch (err) {
    assistantMessage.content = err.message || '请求失败，请重试。'
    assistantMessage.loading = false
    ElMessage.error(assistantMessage.content)
  } finally {
    loading.value = false
    scrollToBottom()
  }
}
</script>

<style lang="scss" scoped>
.kb-chat-page {
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

.header-tools {
  display: flex;
  align-items: center;
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
  align-items: center;
  gap: 2px;

  > span {
    display: inline-block;
    width: 6px;
    height: 6px;
    background-color: #8b949e;
    border-radius: 50%;
    margin: 0 2px;
    animation: bounce 1.4s infinite ease-in-out both;

    &:nth-child(1) { animation-delay: -0.32s; }
    &:nth-child(2) { animation-delay: -0.16s; }
  }

  em {
    margin-left: 8px;
    font-style: normal;
    color: #8b949e;
    font-size: 12px;
  }
}

@keyframes bounce {
  0%, 80%, 100% { transform: scale(0); }
  40% { transform: scale(1); }
}
</style>
