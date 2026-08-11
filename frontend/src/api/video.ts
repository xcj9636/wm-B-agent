import api from './index'
import type {
  CompiledShotReceipt,
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
}
