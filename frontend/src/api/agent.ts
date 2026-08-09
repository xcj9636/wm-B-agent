import api from './index'
import type {
  AgentDelivery,
  AgentOverview,
  AgentResearchJob,
  AgentRun,
  ResearchEvidenceUpdate,
  ResearchOutreachDraft,
  MailboxAccount,
} from '@/types/agent'

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

  async researchJobs() {
    const response = await api.get<AgentResearchJob[]>('/api/v1/agent/research-jobs')
    return response.data
  },

  async researchJob(id: string) {
    const response = await api.get<AgentResearchJob>(`/api/v1/agent/research-jobs/${id}`)
    return response.data
  },

  async createResearchJob(data: { customer_id: number; objective: string }) {
    const response = await api.post<AgentResearchJob>('/api/v1/agent/research-jobs', data)
    return response.data
  },

  async updateResearchEvidence(id: string, data: ResearchEvidenceUpdate) {
    const response = await api.put<AgentResearchJob>(
      `/api/v1/agent/research-jobs/${id}/evidence`,
      data,
    )
    return response.data
  },

  async reviewResearchJob(id: string, data: { decision: 'approve' | 'reject'; reason: string }) {
    const response = await api.post<AgentResearchJob>(
      `/api/v1/agent/research-jobs/${id}/review`,
      data,
    )
    return response.data
  },

  async createOutreachDraft(
    id: string,
    data: {
      channel: 'email' | 'whatsapp'
      language: string
      goal: string
      idempotency_key: string
    },
  ) {
    const response = await api.post<ResearchOutreachDraft>(
      `/api/v1/agent/research-jobs/${id}/drafts`,
      data,
    )
    return response.data
  },

  async reviewOutreachDraft(
    draftId: string,
    data: { decision: 'approve' | 'reject'; reason: string },
  ) {
    const response = await api.patch<ResearchOutreachDraft>(
      `/api/v1/agent/outreach-drafts/${draftId}/review`,
      data,
    )
    return response.data
  },

  async deliveries() {
    const response = await api.get<AgentDelivery[]>('/api/v1/agent/deliveries')
    return response.data
  },

  async mailboxAccounts() {
    const response = await api.get<MailboxAccount[]>('/api/v1/admin/accounts')
    return response.data.filter((account) => ['gmail', 'outlook'].includes(account.account_type))
  },

  async createDelivery(
    draftId: string,
    data: { account_id: number; scheduled_at: string; idempotency_key: string },
  ) {
    const response = await api.post<AgentDelivery>(
      `/api/v1/agent/outreach-drafts/${draftId}/deliveries`,
      data,
    )
    return response.data
  },

  async reviewDelivery(
    deliveryId: string,
    data: { decision: 'approve' | 'reject'; reason: string },
  ) {
    const response = await api.patch<AgentDelivery>(
      `/api/v1/agent/deliveries/${deliveryId}/review`,
      data,
    )
    return response.data
  },
}
