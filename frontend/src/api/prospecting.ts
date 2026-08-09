import { api } from '@/api'

export type ProspectingMode = 'domain_search' | 'email_finder' | 'batch_domain_search'

export interface EvidenceReference {
  domain: string
  uri: string
  extracted_on?: string
  last_seen_on?: string
}

export interface ProspectingContact {
  id: string
  email: string
  first_name?: string
  last_name?: string
  company?: string
  domain?: string
  position?: string
  department?: string
  seniority?: string
  contact_type?: string
  confidence?: number
  decision_maker?: boolean
  verification_status: string
  verification_date?: string
  evidence: EvidenceReference[]
  imported_customer_id?: number
}

export interface ProspectingSearch {
  id: string
  provider: string
  mode: ProspectingMode
  query: Record<string, unknown>
  status: string
  connector_version: number
  result_count: number
  error_code?: string
  created_at: string
  completed_at?: string
  contacts: ProspectingContact[]
}

export interface ProspectingSearchCreate {
  mode: ProspectingMode
  domain?: string
  company?: string
  first_name?: string
  last_name?: string
  full_name?: string
  limit?: number
  offset?: number
  contact_type?: 'personal' | 'generic'
  seniorities?: string[]
  departments?: string[]
  decision_maker?: boolean
  verification_statuses?: string[]
  max_duration?: number
}

export interface ProspectingImportResult {
  created: number
  existing: number
  customer_ids: number[]
}

export interface ProspectingJobItem {
  id: string
  search_id: string
  domain: string
  status: string
  next_offset: number
  pages_completed: number
  requests_used: number
  contacts_found: number
  attempt_count: number
  max_attempts: number
  truncated: boolean
  error_code?: string
  next_attempt_at?: string
  completed_at?: string
}

export interface ProspectingJob {
  id: string
  provider: string
  status: string
  connector_version: number
  page_size: number
  max_pages_per_domain: number
  request_budget: number
  requests_used: number
  provider_remaining?: number
  provider_usage_unit?: string
  total_items: number
  completed_items: number
  failed_items: number
  contacts_found: number
  error_code?: string
  next_attempt_at?: string
  started_at?: string
  completed_at?: string
  created_at: string
  updated_at: string
  items: ProspectingJobItem[]
}

export interface ProspectingJobCreate {
  domains: string[]
  page_size: number
  max_pages_per_domain: number
  request_budget: number
  contact_type?: 'personal' | 'generic'
  seniorities: string[]
  departments: string[]
  decision_maker?: boolean
  verification_statuses: string[]
}

export const prospectingApi = {
  async createSearch(payload: ProspectingSearchCreate) {
    const response = await api.post<ProspectingSearch>('/api/v1/prospecting/searches', payload)
    return response.data
  },
  async listSearches(limit = 20) {
    const response = await api.get<ProspectingSearch[]>('/api/v1/prospecting/searches', {
      params: { limit },
    })
    return response.data
  },
  async getSearch(id: string) {
    const response = await api.get<ProspectingSearch>(`/api/v1/prospecting/searches/${id}`)
    return response.data
  },
  async importContacts(contactIds: string[]) {
    const response = await api.post<ProspectingImportResult>(
      '/api/v1/prospecting/contacts/import/',
      { contact_ids: contactIds },
    )
    return response.data
  },
  async createJob(payload: ProspectingJobCreate) {
    const response = await api.post<ProspectingJob>('/api/v1/prospecting/jobs', payload)
    return response.data
  },
  async listJobs(limit = 20) {
    const response = await api.get<ProspectingJob[]>('/api/v1/prospecting/jobs', {
      params: { limit },
    })
    return response.data
  },
  async getJob(id: string) {
    const response = await api.get<ProspectingJob>(`/api/v1/prospecting/jobs/${id}`)
    return response.data
  },
  async pauseJob(id: string) {
    const response = await api.post<ProspectingJob>(`/api/v1/prospecting/jobs/${id}/pause`)
    return response.data
  },
  async resumeJob(id: string, additionalRequests = 0) {
    const response = await api.post<ProspectingJob>(`/api/v1/prospecting/jobs/${id}/resume`, {
      additional_requests: additionalRequests,
    })
    return response.data
  },
}
