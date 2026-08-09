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

export interface IcpWeights {
  role_fit: number
  contact_quality: number
  evidence_quality: number
}

export interface IcpProfile {
  id: string
  name: string
  target_departments: string[]
  target_seniorities: string[]
  title_keywords: string[]
  preferred_contact_types: string[]
  weights: IcpWeights
  minimum_score: number
  version: number
  created_at: string
  updated_at: string
}

export type IcpProfileUpdate = Omit<IcpProfile, 'id' | 'version' | 'created_at' | 'updated_at'>

export interface ProspectScore {
  id: string
  contact_id: string
  email: string
  name: string
  company?: string
  domain?: string
  position?: string
  department?: string
  seniority?: string
  profile_version: number
  base_score: number
  score_adjustment: number
  final_score: number
  tier: 'A' | 'B' | 'C' | 'D'
  stale: boolean
  recommended: boolean
  factor_scores: Record<string, number>
  reasons: string[]
  missing_signals: string[]
  review_status: 'unreviewed' | 'qualified' | 'disqualified'
  review_reason?: string
  reviewed_at?: string
  scored_at: string
}

export interface ProspectRanking {
  search_id: string
  profile_id: string
  profile_version: number
  minimum_score: number
  stale: boolean
  scores: ProspectScore[]
}

export interface ProspectScoreReview {
  review_status: 'unreviewed' | 'qualified' | 'disqualified'
  score_adjustment: number
  review_reason?: string
}

export const prospectingApi = {
  async getIcpProfile() {
    const response = await api.get<IcpProfile>('/api/v1/prospecting/icp-profile')
    return response.data
  },
  async updateIcpProfile(payload: IcpProfileUpdate) {
    const response = await api.put<IcpProfile>('/api/v1/prospecting/icp-profile', payload)
    return response.data
  },
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
  async scoreSearch(id: string) {
    const response = await api.post<ProspectRanking>(`/api/v1/prospecting/searches/${id}/score`)
    return response.data
  },
  async getRanking(id: string) {
    const response = await api.get<ProspectRanking>(`/api/v1/prospecting/searches/${id}/ranking`)
    return response.data
  },
  async reviewScore(id: string, payload: ProspectScoreReview) {
    const response = await api.patch<ProspectScore>(`/api/v1/prospecting/scores/${id}/review`, payload)
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
