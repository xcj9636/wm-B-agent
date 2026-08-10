<template>
  <div class="ai-chat-page">
    <aside
      class="chat-sidebar apple-glass"
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
          <div class="copilot-orb">
            <el-icon><MagicStick /></el-icon>
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
            <el-icon v-if="message.role === 'assistant'">
              <MagicStick />
            </el-icon>
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
        class="composer apple-glass"
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
.ai-chat-page { display: grid; grid-template-columns: 280px minmax(0, 1fr); height: calc(100dvh - 96px); min-height: 620px; overflow: hidden; border: 1px solid var(--border-subtle); border-radius: 20px; background: var(--surface-elevated); box-shadow: var(--shadow-card); }
.chat-sidebar { display: flex; flex-direction: column; min-width: 0; padding: 18px 12px; border-right: 1px solid var(--border-subtle); background: color-mix(in srgb, var(--surface-panel) 88%, transparent); }
.sidebar-heading, .chat-header { display: flex; align-items: center; justify-content: space-between; gap: 16px; }
.sidebar-heading { padding: 0 6px 16px; }
.sidebar-heading h2, .chat-header h1, .welcome-state h2 { margin: 2px 0 0; }
.page-kicker { margin: 0; color: var(--apple-blue); font-size: 11px; font-weight: 700; letter-spacing: .09em; text-transform: uppercase; }
.session-list { display: grid; align-content: start; gap: 5px; overflow-y: auto; }
.session-item { display: grid; grid-template-columns: 28px minmax(0, 1fr) 28px; align-items: center; gap: 7px; width: 100%; padding: 10px 7px; border: 0; border-radius: 11px; color: var(--el-text-color-regular); background: transparent; text-align: left; cursor: pointer; }
.session-item:hover, .session-item.active { background: color-mix(in srgb, var(--apple-blue) 10%, transparent); color: var(--el-text-color-primary); }
.session-item span { display: grid; min-width: 0; gap: 3px; }
.session-item strong, .session-item small { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.session-item small { color: var(--el-text-color-secondary); font-size: 11px; }
.delete-session { opacity: 0; }
.session-item:hover .delete-session { opacity: 1; }
.chat-workspace { display: grid; grid-template-rows: auto minmax(0, 1fr) auto; min-width: 0; background: var(--surface-canvas); }
.chat-header { min-height: 72px; padding: 10px 24px; border-bottom: 1px solid var(--border-subtle); background: color-mix(in srgb, var(--surface-panel) 84%, transparent); backdrop-filter: blur(20px); }
.chat-header h1 { font-size: 18px; }
.route-badges { display: flex; gap: 6px; }
.mobile-session-toggle { display: none; }
.message-pane { overflow-y: auto; padding: 30px clamp(20px, 6vw, 84px); }
.welcome-state { max-width: 720px; margin: 8vh auto 0; text-align: center; }
.welcome-state > p { max-width: 610px; margin: 10px auto 28px; color: var(--el-text-color-secondary); line-height: 1.65; }
.copilot-orb { display: grid; place-items: center; width: 58px; height: 58px; margin: 0 auto 18px; border-radius: 18px; color: white; font-size: 24px; background: linear-gradient(145deg, #0a84ff, #5e5ce6); box-shadow: 0 14px 32px rgb(10 132 255 / 24%); }
.prompt-grid { display: grid; grid-template-columns: repeat(2, 1fr); gap: 10px; }
.prompt-grid button { display: flex; gap: 9px; min-height: 72px; padding: 14px; border: 1px solid var(--border-subtle); border-radius: 14px; color: var(--el-text-color-primary); background: var(--surface-panel); text-align: left; line-height: 1.45; cursor: pointer; }
.prompt-grid button:hover { border-color: color-mix(in srgb, var(--apple-blue) 42%, var(--border-subtle)); transform: translateY(-1px); box-shadow: var(--shadow-card); }
.message-row { display: grid; grid-template-columns: 34px minmax(0, 1fr); gap: 12px; max-width: 820px; margin: 0 auto 28px; }
.message-avatar { display: grid; place-items: center; width: 34px; height: 34px; border-radius: 11px; color: white; font-weight: 700; background: linear-gradient(145deg, #0a84ff, #5e5ce6); }
.message-row.user .message-avatar { color: var(--el-text-color-primary); background: var(--surface-panel); border: 1px solid var(--border-subtle); }
.message-content { min-width: 0; }
.message-meta { display: flex; align-items: center; gap: 9px; margin-bottom: 7px; }
.message-meta span { color: var(--el-text-color-secondary); font-size: 11px; }
.message-content p { margin: 0; color: var(--el-text-color-primary); white-space: pre-wrap; overflow-wrap: anywhere; line-height: 1.72; }
.message-route { display: flex; gap: 6px; margin-top: 10px; color: var(--el-text-color-secondary); font-size: 11px; }
.message-route span { padding: 3px 7px; border-radius: 6px; background: var(--surface-muted); }
.stream-caret { display: inline-block; width: 7px; height: 16px; margin-left: 3px; vertical-align: -2px; border-radius: 2px; background: var(--apple-blue); animation: blink 1s infinite; }
.composer { margin: 0 clamp(20px, 6vw, 84px) 20px; padding: 9px 12px 9px 16px; border: 1px solid var(--border-subtle); border-radius: 18px; background: color-mix(in srgb, var(--surface-panel) 92%, transparent); box-shadow: 0 12px 35px rgb(0 0 0 / 8%); }
.composer :deep(.el-textarea__inner) { min-height: 42px !important; max-height: 150px; padding: 9px 0; border: 0; box-shadow: none; background: transparent; }
.composer-footer { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.composer-footer > span { display: flex; align-items: center; gap: 5px; color: var(--el-text-color-secondary); font-size: 11px; }
@keyframes blink { 50% { opacity: .2; } }
@media (max-width: 860px) {
  .ai-chat-page { grid-template-columns: 1fr; height: calc(100dvh - 82px); border-radius: 15px; }
  .chat-sidebar { position: absolute; inset: 0 auto 0 0; z-index: 5; width: min(82vw, 300px); transform: translateX(-105%); transition: transform .2s ease; box-shadow: var(--shadow-floating); }
  .chat-sidebar.is-open { transform: translateX(0); }
  .mobile-session-toggle { display: inline-flex; }
  .message-pane { padding: 24px 16px; }
  .composer { margin: 0 12px 12px; }
  .prompt-grid { grid-template-columns: 1fr; }
  .route-badges { display: none; }
  .chat-header { padding: 10px 14px; }
}
</style>
