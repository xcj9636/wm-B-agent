import { api } from './index'

export type MailboxProvider = 'gmail' | 'outlook'

export interface MailboxOAuthProvider {
  provider: MailboxProvider
  display_name: string
  configured: boolean
}

export interface MailboxAccount {
  id: number
  account_type: string
  name: string
  email?: string
  phone_number?: string
  is_active: boolean
  is_verified: boolean
  connection_status: string
  secret_configured: boolean
  oauth_scopes: string[]
  token_expires_at?: string
  last_verified_at?: string
  last_error_code?: string
  daily_limit: number
  today_sent: number
}

export const mailboxApi = {
  async providers() {
    const response = await api.get<MailboxOAuthProvider[]>('/api/v1/mailboxes/oauth/providers')
    return response.data
  },

  async startOAuth(provider: MailboxProvider) {
    const response = await api.post<{ authorization_url: string }>(
      '/api/v1/mailboxes/oauth/start',
      { provider, return_to: '/settings' },
    )
    return response.data
  },

  async list() {
    const response = await api.get<MailboxAccount[]>('/api/v1/mailboxes')
    return response.data
  },
}
