import { api } from '@/api'

export type ProspectingMode = 'domain_search' | 'email_finder'

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
}
