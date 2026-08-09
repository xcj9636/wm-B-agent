import api from './index'
import type { AgentOverview, AgentRun } from '@/types/agent'

export const agentApi = {
  async overview() {
    const response = await api.get<AgentOverview>('/api/v1/agent/overview')
    return response.data
  },

  async runs() {
    const response = await api.get<AgentRun[]>('/api/v1/agent/runs')
    return response.data
  },

  async run(id: string) {
    const response = await api.get<AgentRun>(`/api/v1/agent/runs/${id}`)
    return response.data
  },
}
