import { api } from '@/api'

export interface ConnectorCatalogItem {
  provider: string
  display_name: string
  description: string
  capabilities: string[]
}

export interface ConnectorConfiguration {
  id: string
  provider: string
  name: string
  enabled: boolean
  config: Record<string, number | string | boolean>
  version: number
  secret_configured: boolean
  last_status: 'not_tested' | 'healthy' | 'failed'
  last_error_code?: string
  last_tested_at?: string
  created_at: string
  updated_at: string
}

export interface ConnectorWrite {
  provider: string
  name: string
  secret: string
  config: { timeout_seconds: number }
}

export interface ConnectorProbe {
  ready: boolean
  status: string
  error_code?: string
  account: Record<string, unknown>
}

export const connectorsApi = {
  async catalog() {
    const response = await api.get<ConnectorCatalogItem[]>('/api/v1/connectors/catalog')
    return response.data
  },
  async list() {
    const response = await api.get<ConnectorConfiguration[]>('/api/v1/connectors')
    return response.data
  },
  async create(payload: ConnectorWrite) {
    const response = await api.post<ConnectorConfiguration>('/api/v1/connectors', payload)
    return response.data
  },
  async update(id: string, payload: Partial<Omit<ConnectorWrite, 'provider'>>) {
    const response = await api.put<ConnectorConfiguration>(`/api/v1/connectors/${id}`, payload)
    return response.data
  },
  async test(id: string) {
    const response = await api.post<ConnectorProbe>(`/api/v1/connectors/${id}/test`)
    return response.data
  },
  async setEnabled(id: string, enabled: boolean) {
    const action = enabled ? 'enable' : 'disable'
    const response = await api.post<ConnectorConfiguration>(
      `/api/v1/connectors/${id}/${action}`,
    )
    return response.data
  },
}
