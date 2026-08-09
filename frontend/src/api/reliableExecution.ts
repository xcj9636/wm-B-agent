import api from './index'

export type DeadLetterResolutionAction = 'confirmed_not_sent' | 'confirmed_sent'
export type DeadLetterResolutionStatus = 'pending' | 'executed'

export interface DeadLetterSummary {
  id: string
  aggregate_type: string
  aggregate_id: string
  event_type: string
  channel: string
  attempt_count: number
  max_attempts: number
  error_code: string
  created_at: string
  updated_at: string
}

export interface DeadLetterResolutionCommand {
  action: DeadLetterResolutionAction
  evidence_reference: string
  external_message_id?: string
}

export interface DeadLetterResolutionResult {
  request_id: string
  event_id: string
  action: DeadLetterResolutionAction
  status: DeadLetterResolutionStatus
  approvals: number
  required_approvals: number
}

export const reliableExecutionApi = {
  async listDeadLetters(params: { channel?: string; limit?: number } = {}) {
    const response = await api.get<DeadLetterSummary[]>(
      '/api/v1/admin/reliable-execution/dead-letters',
      { params }
    )
    return response.data
  },

  async approveResolution(eventId: string, command: DeadLetterResolutionCommand) {
    const response = await api.post<DeadLetterResolutionResult>(
      `/api/v1/admin/reliable-execution/dead-letters/${eventId}/resolution-approvals`,
      command
    )
    return response.data
  },
}
