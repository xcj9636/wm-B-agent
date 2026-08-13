import api from './index'
import { resolveBackendApiUrl } from './runtimeConfig'
import type {
  CompiledShotReceipt,
  MediaGenerationEventPage,
  MediaGenerationJob,
  MediaGenerationStreamEvent,
  Paginated,
  Storyboard,
  StoryboardRevision,
  VideoPersonaRevision,
  VideoPersonaSpec,
  VideoProject,
  VideoProjectBrief,
  VideoProjectDetail,
} from '@/types/video'

type PersonaCommand = { idempotency_key: string; spec: VideoPersonaSpec }
type ProjectCommand = {
  idempotency_key: string
  persona_version_id: string
  brief: VideoProjectBrief
  evidence_record_ids: string[]
}

function endpoint(path: string) {
  return `${resolveBackendApiUrl()}${path}`
}

async function parseMediaEventStream(
  response: Response,
  onEvent: (event: MediaGenerationStreamEvent) => void,
  signal?: AbortSignal,
) {
  if (!response.ok) {
    throw new Error(`Media job stream failed with status ${response.status}`)
  }
  if (!response.headers.get('content-type')?.includes('text/event-stream')) {
    throw new Error('Media job stream returned an invalid content type')
  }
  const reader = response.body?.getReader()
  if (!reader) throw new Error('Streaming is not supported by this browser')
  const decoder = new TextDecoder()
  let buffer = ''
  let finished = false

  const consume = (frame: string) => {
    let event = 'message'
    let id: number | undefined
    const data: string[] = []
    for (const line of frame.replace(/\r/g, '').split('\n')) {
      if (line.startsWith('id:')) {
        const parsed = Number.parseInt(line.slice(3).trim(), 10)
        if (Number.isSafeInteger(parsed) && parsed >= 0) id = parsed
      } else if (line.startsWith('event:')) {
        event = line.slice(6).trim()
      } else if (line.startsWith('data:')) {
        data.push(line.slice(5).trimStart())
      }
    }
    if (data.length) {
      onEvent({ id, event, data: JSON.parse(data.join('\n')) })
    }
  }

  try {
    while (!finished) {
      if (signal?.aborted) throw new DOMException('Aborted', 'AbortError')
      const result = await reader.read()
      finished = result.done
      buffer += decoder.decode(result.value, { stream: !finished })
      const frames = buffer.replace(/\r\n/g, '\n').split('\n\n')
      buffer = frames.pop() || ''
      frames.filter(Boolean).forEach(consume)
    }
    if (buffer.trim()) consume(buffer)
  } finally {
    if (!finished) await reader.cancel().catch(() => undefined)
    reader.releaseLock()
  }
}

export const videoApi = {
  async listPersonas(limit = 50, offset = 0) {
    const response = await api.get<Paginated<VideoPersonaRevision>>('/api/v1/video/personas', {
      params: { limit, offset },
    })
    return response.data
  },

  async listPersonaVersions(personaId: string, limit = 50, offset = 0) {
    const response = await api.get<Paginated<VideoPersonaRevision>>(
      `/api/v1/video/personas/${personaId}/versions`,
      { params: { limit, offset } },
    )
    return response.data
  },

  async createPersona(data: PersonaCommand) {
    const response = await api.post<VideoPersonaRevision>('/api/v1/video/personas', data)
    return response.data
  },

  async revisePersona(personaId: string, data: PersonaCommand) {
    const response = await api.post<VideoPersonaRevision>(
      `/api/v1/video/personas/${personaId}/versions`,
      data,
    )
    return response.data
  },

  async approvePersona(versionId: string) {
    const response = await api.post<VideoPersonaRevision>(
      `/api/v1/video/persona-versions/${versionId}/approve`,
      {},
    )
    return response.data
  },

  async listProjects(limit = 50, offset = 0) {
    const response = await api.get<Paginated<VideoProject>>('/api/v1/video/projects', {
      params: { limit, offset },
    })
    return response.data
  },

  async getProject(projectId: string) {
    const response = await api.get<VideoProjectDetail>(`/api/v1/video/projects/${projectId}`)
    return response.data
  },

  async createProject(data: ProjectCommand) {
    const response = await api.post<VideoProject>('/api/v1/video/projects', data)
    return response.data
  },

  async createStoryboard(projectId: string, data: { idempotency_key: string; storyboard: Storyboard }) {
    const response = await api.post<StoryboardRevision>(
      `/api/v1/video/projects/${projectId}/storyboards`,
      data,
    )
    return response.data
  },

  async approveStoryboard(versionId: string) {
    const response = await api.post<StoryboardRevision>(
      `/api/v1/video/storyboard-versions/${versionId}/approve`,
      {},
    )
    return response.data
  },

  async compileShot(projectId: string, storyboardVersionId: string, shotId: string) {
    const response = await api.post<CompiledShotReceipt>(
      `/api/v1/video/projects/${projectId}/storyboards/${storyboardVersionId}/shots/${shotId}/compile`,
      {},
    )
    return response.data
  },

  async createGenerationJob(
    projectId: string,
    storyboardVersionId: string,
    shotId: string,
    idempotencyKey: string,
  ) {
    const response = await api.post<MediaGenerationJob>(
      `/api/v1/video/projects/${projectId}/shots/${shotId}/generation-jobs`,
      {
        idempotency_key: idempotencyKey,
        storyboard_version_id: storyboardVersionId,
      },
    )
    return response.data
  },

  async getGenerationJob(jobId: string) {
    const response = await api.get<MediaGenerationJob>(
      `/api/v1/video/generation-jobs/${jobId}`,
    )
    return response.data
  },

  async listGenerationJobEvents(jobId: string, afterSequence = 0) {
    const response = await api.get<MediaGenerationEventPage>(
      `/api/v1/video/generation-jobs/${jobId}/events`,
      { params: { after_sequence: afterSequence, limit: 100 } },
    )
    return response.data
  },

  async streamGenerationJobEvents(
    jobId: string,
    lastEventId: number,
    onEvent: (event: MediaGenerationStreamEvent) => void,
    signal?: AbortSignal,
  ) {
    const token = localStorage.getItem('access_token')
    const response = await fetch(
      endpoint(`/api/v1/video/generation-jobs/${jobId}/events/stream`),
      {
        signal,
        headers: {
          Accept: 'text/event-stream',
          'Last-Event-ID': String(lastEventId),
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
        },
      },
    )
    let durableEvents = 0
    await parseMediaEventStream(response, (event) => {
      if (typeof event.id === 'number') durableEvents += 1
      onEvent(event)
    }, signal)
    return {
      jobStatus: response.headers.get('x-media-job-status') || 'unknown',
      durableEvents,
    }
  },
}
