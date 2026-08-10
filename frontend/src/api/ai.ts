import { api } from '@/api'
import { resolveBackendApiUrl } from './runtimeConfig'

export interface AIRuntimeConfig {
  backend: 'direct' | 'omniroute'
  base_url: string
  allowed_providers: string[]
  model_aliases: Record<string, string>
  timeout_seconds: number
  source: 'environment' | 'runtime'
  version: number
  api_key_configured: boolean
  updated_at?: string
}

export interface AIRuntimeConfigUpdate {
  backend: 'direct' | 'omniroute'
  base_url: string
  allowed_providers: string[]
  model_aliases: Record<string, string>
  timeout_seconds: number
  api_key?: string
}

export interface AIChatMessage {
  id: string
  session_id: string
  role: 'user' | 'assistant'
  content: string
  resolved_model?: string
  resolved_provider?: string
  usage: Record<string, number | string | null>
  created_at: string
}

export interface AIChatSession {
  id: string
  title: string
  use_case: string
  created_at: string
  updated_at: string
  messages: AIChatMessage[]
}

export interface AIChatRun {
  run_id: string
  turn_id: string
  session_id: string
  status: string
}

interface PendingChatRun {
  runId: string
  sessionId: string
  lastEventId: number
}

export type AIStreamEvent =
  | { id?: number; event: 'run.started'; data: { run_id: string; turn_id?: string } }
  | { id?: number; event: 'stream.reset'; data: { content: string } }
  | { id?: number; event: 'message.delta'; data: { delta: string } }
  | { id?: number; event: 'run.completed'; data: AIChatMessage }
  | { id?: number; event: 'run.failed'; data: { error_code?: string; detail?: string } }
  | { id?: number; event: 'heartbeat'; data: { status?: string } }
  | { event: 'delta'; data: { delta: string } }
  | { event: 'done'; data: AIChatMessage }
  | { event: 'error'; data: { detail: string } }

function endpoint(path: string) {
  return `${resolveBackendApiUrl()}${path}`
}

async function parseEventStream(
  response: Response,
  onEvent: (event: AIStreamEvent) => void,
  signal?: AbortSignal,
) {
  if (!response.ok) throw new Error(`AI stream failed with status ${response.status}`)
  if (!response.headers.get('content-type')?.includes('text/event-stream')) {
    throw new Error('AI stream returned an invalid content type')
  }
  const reader = response.body?.getReader()
  if (!reader) throw new Error('Streaming is not supported by this browser')
  const decoder = new TextDecoder()
  let buffer = ''

  let finished = false
  try {
    while (!finished) {
      if (signal?.aborted) throw new DOMException('Aborted', 'AbortError')
      const result = await reader.read()
      finished = result.done
      buffer += decoder.decode(result.value, { stream: !finished })
      const frames = buffer.split('\n\n')
      buffer = frames.pop() || ''
      for (const frame of frames) {
        let event = 'message'
        let id: number | undefined
        const data: string[] = []
        for (const line of frame.split('\n')) {
          if (line.startsWith('id:')) {
            const parsed = Number.parseInt(line.slice(3).trim(), 10)
            if (Number.isSafeInteger(parsed) && parsed >= 0) id = parsed
          }
          if (line.startsWith('event:')) event = line.slice(6).trim()
          if (line.startsWith('data:')) data.push(line.slice(5).trimStart())
        }
        if (data.length) {
          onEvent({ id, event, data: JSON.parse(data.join('\n')) } as AIStreamEvent)
        }
      }
    }
  } finally {
    if (!finished) await reader.cancel().catch(() => undefined)
    reader.releaseLock()
  }
}

async function replayRunEvents(
  runId: string,
  lastEventId: number,
  onEvent: (event: AIStreamEvent) => void,
  signal?: AbortSignal,
) {
  const token = localStorage.getItem('access_token')
  let durableEvents = 0
  const response = await fetch(endpoint(`/api/v1/agent/runs/${runId}/events`), {
    signal,
    headers: {
      Accept: 'text/event-stream',
      'Last-Event-ID': String(lastEventId),
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
  })
  await parseEventStream(response, (event) => {
    if ('id' in event && typeof event.id === 'number') durableEvents += 1
    onEvent(event)
  }, signal)
  return {
    status: response.headers.get('x-agent-run-status') || 'unknown',
    durableEvents,
  }
}

const pendingRunKey = 'b-agent:pending-chat-run'
const failedRunStatuses = new Set(['failed', 'cancelled', 'unknown'])

function readPendingRun(): PendingChatRun | null {
  try {
    const value = sessionStorage.getItem(pendingRunKey)
    return value ? JSON.parse(value) as PendingChatRun : null
  } catch {
    sessionStorage.removeItem(pendingRunKey)
    return null
  }
}

function writePendingRun(pending: PendingChatRun) {
  sessionStorage.setItem(pendingRunKey, JSON.stringify(pending))
}

async function waitForNextReplay(signal?: AbortSignal) {
  await new Promise<void>((resolve, reject) => {
    const onAbort = () => {
      window.clearTimeout(timer)
      reject(new DOMException('Aborted', 'AbortError'))
    }
    const timer = window.setTimeout(() => {
      signal?.removeEventListener('abort', onAbort)
      resolve()
    }, 500)
    signal?.addEventListener('abort', onAbort, { once: true })
  })
}

async function consumeRunEvents(
  pending: PendingChatRun,
  onEvent: (event: AIStreamEvent) => void,
  signal?: AbortSignal,
) {
  let terminal = false
  const trackEvent = (event: AIStreamEvent) => {
    if ('id' in event && typeof event.id === 'number') {
      pending.lastEventId = event.id
      writePendingRun(pending)
    }
    if (event.event === 'run.completed' || event.event === 'done') terminal = true
    if (event.event === 'run.failed' || event.event === 'error') terminal = true
    if (event.event === 'heartbeat' && event.data.status && failedRunStatuses.has(event.data.status)) {
      terminal = true
    }
    if (event.event === 'stream.reset') onEvent(event)
    else if (event.event !== 'heartbeat') onEvent(event)
  }

  try {
    while (!terminal) {
      const replay = await replayRunEvents(
        pending.runId,
        pending.lastEventId,
        trackEvent,
        signal,
      )
      if (failedRunStatuses.has(replay.status)) terminal = true
      if (replay.status === 'completed' && replay.durableEvents === 0) terminal = true
      if (!terminal) await waitForNextReplay(signal)
    }
  } finally {
    if (terminal) sessionStorage.removeItem(pendingRunKey)
  }
}

export const aiApi = {
  async getConfig() {
    const response = await api.get<AIRuntimeConfig>('/api/v1/ai/config')
    return response.data
  },
  async updateConfig(config: AIRuntimeConfigUpdate) {
    const response = await api.put<AIRuntimeConfig>('/api/v1/ai/config', config)
    return response.data
  },
  async testConfig() {
    const response = await api.post<{
      ready: boolean
      reachable: boolean
      models: string[]
      issues: string[]
    }>('/api/v1/ai/config/test')
    return response.data
  },
  async listModels() {
    const response = await api.get<{ models: string[] }>('/api/v1/ai/models')
    return response.data.models
  },
}

export const chatApi = {
  async listSessions() {
    const response = await api.get<AIChatSession[]>('/api/v1/ai/chat/sessions')
    return response.data
  },
  async createSession(title?: string) {
    const response = await api.post<AIChatSession>('/api/v1/ai/chat/sessions', { title })
    return response.data
  },
  async getSession(sessionId: string) {
    const response = await api.get<AIChatSession>(`/api/v1/ai/chat/sessions/${sessionId}`)
    return response.data
  },
  async deleteSession(sessionId: string) {
    await api.delete(`/api/v1/ai/chat/sessions/${sessionId}`)
  },
  async sendMessage(sessionId: string, content: string) {
    const response = await api.post<AIChatMessage>(
      `/api/v1/ai/chat/sessions/${sessionId}/messages`,
      { content },
    )
    return response.data
  },
  async streamMessage(
    sessionId: string,
    content: string,
    idempotencyKey: string,
    onEvent: (event: AIStreamEvent) => void,
    signal?: AbortSignal,
  ) {
    const token = localStorage.getItem('access_token')
    const response = await fetch(
      endpoint(`/api/v1/ai/chat/sessions/${sessionId}/messages/runs`),
      {
        method: 'POST',
        signal,
        headers: {
          'Content-Type': 'application/json',
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
        },
        body: JSON.stringify({ content, idempotency_key: idempotencyKey }),
      },
    )
    if (!response.ok) {
      throw new Error(`AI run could not start with status ${response.status}`)
    }
    const run = await response.json() as AIChatRun
    const pending = {
      runId: run.run_id,
      sessionId: run.session_id,
      lastEventId: 0,
    }
    writePendingRun(pending)
    await consumeRunEvents(pending, onEvent, signal)
  },
  pendingRun() {
    return readPendingRun()
  },
  async resumePendingMessage(
    onEvent: (event: AIStreamEvent) => void,
    signal?: AbortSignal,
  ) {
    const pending = readPendingRun()
    if (!pending) return false
    await consumeRunEvents(pending, onEvent, signal)
    return true
  },
}
