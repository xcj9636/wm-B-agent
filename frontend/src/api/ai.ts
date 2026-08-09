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

export type AIStreamEvent =
  | { event: 'delta'; data: { delta: string } }
  | { event: 'done'; data: AIChatMessage }
  | { event: 'error'; data: { detail: string } }

function endpoint(path: string) {
  return `${resolveBackendApiUrl()}${path}`
}

async function parseEventStream(
  response: Response,
  onEvent: (event: AIStreamEvent) => void,
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
  while (!finished) {
    const result = await reader.read()
    finished = result.done
    buffer += decoder.decode(result.value, { stream: !finished })
    const frames = buffer.split('\n\n')
    buffer = frames.pop() || ''
    for (const frame of frames) {
      let event = 'message'
      const data: string[] = []
      for (const line of frame.split('\n')) {
        if (line.startsWith('event:')) event = line.slice(6).trim()
        if (line.startsWith('data:')) data.push(line.slice(5).trimStart())
      }
      if (data.length) {
        onEvent({ event, data: JSON.parse(data.join('\n')) } as AIStreamEvent)
      }
    }
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
    onEvent: (event: AIStreamEvent) => void,
  ) {
    const token = localStorage.getItem('access_token')
    const response = await fetch(
      endpoint(`/api/v1/ai/chat/sessions/${sessionId}/messages/stream`),
      {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Accept: 'text/event-stream',
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
        },
        body: JSON.stringify({ content }),
      },
    )
    await parseEventStream(response, onEvent)
  },
}
