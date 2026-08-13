import { api } from '@/api'

export type MediaWorkflowMode = 'text_to_image' | 'image_to_video' | 'text_to_video'

export interface MediaModelCapability {
  id: string
  display_name: string
  modes: MediaWorkflowMode[]
}

export interface MediaCapabilityCatalog {
  provider: 'fal'
  schema_version: string
  models: MediaModelCapability[]
}

export interface MediaRuntimeProbe {
  id: string
  revision_id: string
  ready: boolean
  reachable: boolean
  issues: string[]
  capability_snapshot_hash: string
  created_at: string
}

export interface MediaRuntimeRevision {
  id: string
  org_id: string
  revision: number
  provider: 'fal'
  enabled_modes: MediaWorkflowMode[]
  model_aliases: Partial<Record<MediaWorkflowMode, string>>
  capability_snapshot: MediaCapabilityCatalog
  capability_snapshot_hash: string
  pricing_configured: boolean
  pricing_snapshot_hash: string
  api_key_configured: boolean
  latest_probe?: MediaRuntimeProbe
  created_at: string
}

export interface MediaRuntimeState {
  active_revision?: MediaRuntimeRevision
  submission_enabled: boolean
  api_key_configured: boolean
}

export interface MediaRuntimeRevisionCreate {
  provider: 'fal'
  enabled_modes: MediaWorkflowMode[]
  model_aliases: Partial<Record<MediaWorkflowMode, string>>
  api_key?: string
}

const basePath = '/api/v1/admin/media/runtime'

export const mediaRuntimeApi = {
  async getState() {
    const response = await api.get<MediaRuntimeState>(basePath)
    return response.data
  },
  async getCapabilities() {
    const response = await api.get<MediaCapabilityCatalog>(`${basePath}/capabilities`)
    return response.data
  },
  async listRevisions() {
    const response = await api.get<MediaRuntimeRevision[]>(`${basePath}/revisions`)
    return response.data
  },
  async createRevision(command: MediaRuntimeRevisionCreate) {
    const response = await api.post<MediaRuntimeRevision>(`${basePath}/revisions`, command)
    return response.data
  },
  async probeRevision(revisionId: string) {
    const response = await api.post<MediaRuntimeProbe>(`${basePath}/revisions/${revisionId}/probe`)
    return response.data
  },
  async activateRevision(revisionId: string) {
    const response = await api.post<MediaRuntimeState>(`${basePath}/revisions/${revisionId}/activate`)
    return response.data
  },
}
