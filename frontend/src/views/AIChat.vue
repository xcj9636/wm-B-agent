<template>
  <div class="ai-chat-page">
    <aside
      class="chat-sidebar"
      :class="{ 'is-open': sidebarOpen }"
    >
      <div class="sidebar-heading">
        <div>
          <p class="page-kicker">
            {{ $t('Workspace copilot') }}
          </p>
          <h2>{{ $t('AI Chat') }}</h2>
        </div>
        <el-button
          circle
          type="primary"
          :aria-label="$t('New chat')"
          @click="createSession"
        >
          <el-icon><Plus /></el-icon>
        </el-button>
      </div>

      <div class="session-list">
        <button
          v-for="session in sessions"
          :key="session.id"
          type="button"
          class="session-item"
          :class="{ active: session.id === activeSessionId }"
          @click="selectSession(session.id)"
        >
          <el-icon><ChatLineRound /></el-icon>
          <span>
            <strong>{{ session.title }}</strong>
            <small>{{ formatDate(session.updated_at) }}</small>
          </span>
          <el-button
            class="delete-session"
            text
            circle
            :aria-label="$t('Delete chat')"
            @click.stop="removeSession(session.id)"
          >
            <el-icon><Delete /></el-icon>
          </el-button>
        </button>
        <el-empty
          v-if="!loadingSessions && !sessions.length"
          :image-size="72"
          :description="$t('No AI conversations yet')"
        />
      </div>
    </aside>

    <section class="chat-workspace">
      <header class="chat-header">
        <el-button
          class="mobile-session-toggle"
          text
          circle
          @click="sidebarOpen = !sidebarOpen"
        >
          <el-icon><Menu /></el-icon>
        </el-button>
        <div>
          <p class="page-kicker">
            {{ $t('Foreign trade intelligence') }}
          </p>
          <h1>{{ currentSession?.title || $t('Start a conversation') }}</h1>
        </div>
        <div class="route-badges">
          <el-tag effect="plain">
            live_reply
          </el-tag>
          <el-tag
            v-if="lastRoute"
            type="success"
            effect="plain"
          >
            {{ lastRoute }}
          </el-tag>
        </div>
      </header>

      <div
        ref="messagePane"
        class="message-pane"
      >
        <div
          v-if="!messages.length"
          class="welcome-state"
        >
          <div class="copilot-mark">
            <img
              src="/b-agent-logo.svg"
              alt=""
            >
          </div>
          <h2>{{ $t('What should we grow today?') }}</h2>
          <p>{{ $t('Ask B-agent to research a market, prepare outreach, analyze a buyer reply or plan a quotation.') }}</p>
          <div class="prompt-grid">
            <button
              v-for="prompt in promptSuggestions"
              :key="prompt"
              type="button"
              @click="draft = $t(prompt)"
            >
              <el-icon><TopRight /></el-icon>
              {{ $t(prompt) }}
            </button>
          </div>
        </div>

        <article
          v-for="message in messages"
          :key="message.id"
          class="message-row"
          :class="message.role"
        >
          <div class="message-avatar">
            <img
              v-if="message.role === 'assistant'"
              src="/b-agent-logo.svg"
              alt=""
            >
            <span v-else>{{ userInitial }}</span>
          </div>
          <div class="message-content">
            <div class="message-meta">
              <strong>{{ message.role === 'assistant' ? 'B-agent' : $t('You') }}</strong>
              <span>{{ formatTime(message.created_at) }}</span>
            </div>
            <p>
              {{ message.content }}<span
                v-if="message.id === 'streaming'"
                class="stream-caret"
              />
            </p>
            <div
              v-if="message.resolved_model || message.resolved_provider"
              class="message-route"
            >
              <span>{{ message.resolved_provider }}</span>
              <span>{{ message.resolved_model }}</span>
            </div>
          </div>
        </article>
      </div>

      <form
        class="composer"
        @submit.prevent="send"
      >
        <el-input
          v-model="draft"
          type="textarea"
          autosize
          resize="none"
          :disabled="sending"
          :placeholder="$t('Ask about markets, leads, outreach, replies or quotations...')"
          @keydown.enter.exact.prevent="send"
        />
        <div class="composer-footer">
          <span>
            <el-icon><Lock /></el-icon>
            {{ $t('Routed securely through the B-agent backend') }}
          </span>
          <el-button
            type="primary"
            circle
            native-type="submit"
            :loading="sending"
            :disabled="!draft.trim()"
            :aria-label="$t('Send message')"
          >
            <el-icon><Promotion /></el-icon>
          </el-button>
        </div>
      </form>
    </section>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import dayjs from 'dayjs'
import { chatApi, type AIChatMessage, type AIChatSession, type AIStreamEvent } from '@/api/ai'
import { useAuthStore } from '@/stores/auth'
import { translate } from '@/i18n'

const authStore = useAuthStore()
const sessions = ref<AIChatSession[]>([])
const currentSession = ref<AIChatSession | null>(null)
const messages = ref<AIChatMessage[]>([])
const draft = ref('')
const sending = ref(false)
const loadingSessions = ref(false)
const sidebarOpen = ref(false)
const messagePane = ref<HTMLElement | null>(null)
let activeStreamController: AbortController | null = null

const activeSessionId = computed(() => currentSession.value?.id)
const userInitial = computed(() => authStore.user?.username?.charAt(0).toUpperCase() || 'U')
const lastAssistant = computed(() => [...messages.value].reverse().find((item) => item.role === 'assistant'))
const lastRoute = computed(() => lastAssistant.value?.resolved_provider || lastAssistant.value?.resolved_model || '')
const promptSuggestions = [
  'Compare Germany and the Netherlands for our first EU distributor market.',
  'Draft a concise English follow-up for a buyer who requested a sample.',
  'Analyze this buyer reply and recommend the next sales action.',
  'Build a discovery checklist for a new overseas distributor.',
]

function formatDate(value: string) {
  return dayjs(value).format('MM/DD HH:mm')
}

function formatTime(value: string) {
  return dayjs(value).format('HH:mm')
}

async function scrollToBottom() {
  await nextTick()
  messagePane.value?.scrollTo({ top: messagePane.value.scrollHeight, behavior: 'smooth' })
}

async function loadSessions() {
  loadingSessions.value = true
  try {
    sessions.value = await chatApi.listSessions()
    if (sessions.value.length) await selectSession(sessions.value[0].id)
  } catch {
    ElMessage.error(translate('AI conversations could not be loaded.'))
  } finally {
    loadingSessions.value = false
  }
}

async function createSession() {
  try {
    const session = await chatApi.createSession()
    sessions.value.unshift(session)
    currentSession.value = session
    messages.value = []
    sidebarOpen.value = false
  } catch {
    ElMessage.error(translate('AI conversation could not be created.'))
  }
}

async function selectSession(id: string) {
  try {
    const session = await chatApi.getSession(id)
    currentSession.value = session
    messages.value = session.messages
    sidebarOpen.value = false
    await scrollToBottom()
  } catch {
    ElMessage.error(translate('AI conversation could not be opened.'))
  }
}

async function removeSession(id: string) {
  try {
    await ElMessageBox.confirm(translate('Delete this AI conversation?'), translate('Delete chat'), { type: 'warning' })
    await chatApi.deleteSession(id)
    sessions.value = sessions.value.filter((item) => item.id !== id)
    if (currentSession.value?.id === id) {
      currentSession.value = null
      messages.value = []
      if (sessions.value[0]) await selectSession(sessions.value[0].id)
    }
  } catch (error) {
    if (error !== 'cancel' && error !== 'close') ElMessage.error(translate('AI conversation could not be deleted.'))
  }
}

async function send() {
  const content = draft.value.trim()
  if (!content || sending.value) return
  if (!currentSession.value) await createSession()
  if (!currentSession.value) return

  sending.value = true
  draft.value = ''
  const now = new Date().toISOString()
  messages.value.push({
    id: `local-${Date.now()}`,
    session_id: currentSession.value.id,
    role: 'user',
    content,
    usage: {},
    created_at: now,
  })
  const streaming: AIChatMessage = {
    id: 'streaming',
    session_id: currentSession.value.id,
    role: 'assistant',
    content: '',
    usage: {},
    created_at: now,
  }
  messages.value.push(streaming)
  await scrollToBottom()
  const idempotencyKey = crypto.randomUUID()
  activeStreamController?.abort()
  const controller = new AbortController()
  activeStreamController = controller

  try {
    await chatApi.streamMessage(currentSession.value.id, content, idempotencyKey, (event) => {
      applyStreamEvent(streaming, event)
    }, controller.signal)
    const refreshed = await chatApi.listSessions()
    sessions.value = refreshed
  } catch {
    messages.value = messages.value.filter((item) => item.id !== 'streaming')
    ElMessage.error(translate('B-agent could not complete this response. Check the AI route configuration.'))
  } finally {
    if (activeStreamController === controller) activeStreamController = null
    sending.value = false
  }
}

function applyStreamEvent(streaming: AIChatMessage, event: AIStreamEvent) {
  if (event.event === 'stream.reset') streaming.content = event.data.content
  if ((event.event === 'delta' || event.event === 'message.delta') && streaming.id === 'streaming') {
    streaming.content += event.data.delta
  }
  if (event.event === 'done' || event.event === 'run.completed') Object.assign(streaming, event.data)
  if (event.event === 'error' || event.event === 'run.failed') {
    throw new Error('detail' in event.data ? event.data.detail : event.data.error_code)
  }
  void scrollToBottom()
}

async function resumePendingRun() {
  const pending = chatApi.pendingRun()
  if (!pending) return
  await selectSession(pending.sessionId)
  if (!currentSession.value) return
  let streaming = messages.value[messages.value.length - 1]
  if (!streaming || streaming.role !== 'assistant') {
    streaming = {
      id: 'streaming',
      session_id: pending.sessionId,
      role: 'assistant',
      content: '',
      usage: {},
      created_at: new Date().toISOString(),
    }
    messages.value.push(streaming)
  }
  sending.value = true
  const controller = new AbortController()
  activeStreamController = controller
  try {
    await chatApi.resumePendingMessage(
      (event) => applyStreamEvent(streaming, event),
      controller.signal,
    )
    await selectSession(pending.sessionId)
  } catch (error) {
    if (!(error instanceof DOMException && error.name === 'AbortError')) {
      ElMessage.error(translate('B-agent could not resume the previous response.'))
    }
  } finally {
    if (activeStreamController === controller) activeStreamController = null
    sending.value = false
  }
}

onMounted(async () => {
  await loadSessions()
  await resumePendingRun()
})

onBeforeUnmount(() => activeStreamController?.abort())
</script>

<style scoped lang="scss">
.ai-chat-page {
  display: grid;
  height: calc(100dvh - 110px);
  min-height: 580px;
  grid-template-columns: 250px minmax(0, 1fr);
  overflow: hidden;
  border: 1px solid var(--border-hairline);
  border-radius: var(--radius-card);
  background: var(--surface-canvas);
}

.chat-sidebar {
  display: flex;
  min-width: 0;
  flex-direction: column;
  padding: 14px 10px;
  border-right: 1px solid var(--border-hairline);
  background: var(--surface-sidebar);
}

.sidebar-heading,
.chat-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
}

.sidebar-heading { padding: 2px 6px 14px; }
.sidebar-heading h2,
.chat-header h1,
.welcome-state h2 { margin: 2px 0 0; }
.sidebar-heading h2 { font-size: 16px; font-weight: 600; }
.page-kicker { margin: 0; color: var(--text-tertiary); font-size: 11px; font-weight: 500; }

.session-list {
  display: grid;
  overflow-y: auto;
  align-content: start;
  gap: 2px;
}

.session-item {
  display: grid;
  width: 100%;
  min-height: 44px;
  grid-template-columns: 24px minmax(0, 1fr) 28px;
  align-items: center;
  gap: 7px;
  padding: 7px 6px;
  border: 0;
  border-radius: 8px;
  color: var(--el-text-color-regular);
  background: transparent;
  text-align: left;
  cursor: pointer;
}

.session-item:hover,
.session-item.active { color: var(--text-primary); background: var(--surface-selected); }
.session-item span { display: grid; min-width: 0; gap: 2px; }
.session-item strong,
.session-item small { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.session-item strong { font-size: 13px; font-weight: 550; }
.session-item small { color: var(--text-tertiary); font-size: 10px; }
.delete-session { opacity: 0; }
.session-item:hover .delete-session,
.session-item:focus-within .delete-session { opacity: 1; }

.chat-workspace {
  display: grid;
  min-width: 0;
  grid-template-rows: auto minmax(0, 1fr) auto;
  background: var(--surface-canvas);
}

.chat-header {
  min-height: 58px;
  padding: 8px 20px;
  border-bottom: 1px solid var(--border-hairline);
  background: var(--surface-canvas);
}

.chat-header h1 { font-size: 15px; font-weight: 600; }
.route-badges { display: flex; gap: 6px; }
.mobile-session-toggle { display: none; }
.message-pane { overflow-y: auto; padding: 26px clamp(18px, 6vw, 72px); }

.welcome-state {
  max-width: 720px;
  margin: clamp(40px, 10vh, 100px) auto 0;
  text-align: center;
}

.welcome-state h2 { font-size: clamp(24px, 3vw, 30px); font-weight: 650; letter-spacing: -0.025em; }
.welcome-state > p { max-width: 560px; margin: 10px auto 30px; color: var(--text-secondary); line-height: 1.6; }

.copilot-mark {
  display: grid;
  width: 52px;
  height: 52px;
  margin: 0 auto 20px;
  place-items: center;
  border: 1px solid var(--border-hairline);
  border-radius: 14px;
  background: var(--surface-elevated);
}

.copilot-mark img { width: 38px; height: 38px; }
.prompt-grid { display: grid; grid-template-columns: repeat(2, 1fr); gap: 9px; }

.prompt-grid button {
  display: flex;
  min-height: 64px;
  align-items: flex-start;
  gap: 9px;
  padding: 13px;
  border: 1px solid var(--border-hairline);
  border-radius: 12px;
  color: var(--text-primary);
  background: var(--surface-elevated);
  text-align: left;
  line-height: 1.45;
  cursor: pointer;
  transition: background-color 160ms ease, border-color 160ms ease;
}

.prompt-grid button:hover,
.prompt-grid button:focus-visible { border-color: var(--border-color); background: var(--surface-hover); }

.message-row {
  display: grid;
  max-width: 800px;
  grid-template-columns: 32px minmax(0, 1fr);
  gap: 12px;
  margin: 0 auto 26px;
}

.message-avatar {
  display: grid;
  width: 32px;
  height: 32px;
  place-items: center;
  overflow: hidden;
  border: 1px solid var(--border-hairline);
  border-radius: 9px;
  color: var(--text-primary);
  background: var(--surface-elevated);
  font-size: 12px;
  font-weight: 650;
}

.message-avatar img { width: 26px; height: 26px; }
.message-content { min-width: 0; }
.message-meta { display: flex; align-items: center; gap: 9px; margin-bottom: 7px; }
.message-meta strong { font-size: 13px; font-weight: 600; }
.message-meta span { color: var(--text-tertiary); font-size: 10px; }
.message-content p { margin: 0; color: var(--text-primary); white-space: pre-wrap; overflow-wrap: anywhere; line-height: 1.72; }

.message-row.user {
  grid-template-columns: minmax(0, 1fr);
  justify-items: end;
}

.message-row.user .message-avatar { display: none; }
.message-row.user .message-content { max-width: min(78%, 620px); padding: 10px 14px; border-radius: 18px; background: var(--surface-sunken); }
.message-row.user .message-meta { justify-content: flex-end; margin-bottom: 4px; }
.message-route { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 10px; color: var(--text-tertiary); font-size: 10px; }
.message-route span { padding: 3px 7px; border-radius: 6px; background: var(--surface-muted); }
.stream-caret { display: inline-block; width: 6px; height: 15px; margin-left: 3px; vertical-align: -2px; border-radius: 2px; background: var(--brand-accent); animation: blink 1s infinite; }

.composer {
  max-width: 800px;
  width: calc(100% - clamp(28px, 10vw, 144px));
  margin: 0 auto 18px;
  padding: 9px 10px 9px 16px;
  border: 1px solid var(--border-color);
  border-radius: 24px;
  background: var(--surface-elevated);
  box-shadow: 0 2px 10px rgb(0 0 0 / 0.06);
}

.composer:focus-within { border-color: color-mix(in srgb, var(--text-tertiary) 65%, var(--border-color)); }
.composer :deep(.el-textarea__inner) { min-height: 42px !important; max-height: 150px; padding: 9px 0; border: 0; box-shadow: none; background: transparent; }
.composer-footer { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.composer-footer > span { display: flex; align-items: center; gap: 5px; color: var(--text-tertiary); font-size: 10px; }

@keyframes blink { 50% { opacity: 0.2; } }

@media (max-width: 860px) {
  .ai-chat-page { position: relative; height: calc(100dvh - 92px); grid-template-columns: 1fr; border-radius: 10px; }
  .chat-sidebar { position: absolute; inset: 0 auto 0 0; z-index: 5; width: min(82vw, 300px); transform: translateX(-105%); transition: transform 180ms ease; box-shadow: var(--shadow-floating); }
  .chat-sidebar.is-open { transform: translateX(0); }
  .mobile-session-toggle { display: inline-flex; }
  .message-pane { padding: 24px 16px; }
  .composer { width: calc(100% - 24px); margin-bottom: 12px; }
  .prompt-grid { grid-template-columns: 1fr; }
  .route-badges { display: none; }
  .chat-header { padding: 8px 12px; }
  .message-row.user .message-content { max-width: 88%; }
  .composer-footer > span { display: none; }
}
</style>
