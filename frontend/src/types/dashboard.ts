export interface DailyStats {
  date: string
  new_customers: number
  active_customers: number
  converted_customers: number
  emails_sent: number
  whatsapp_sent: number
  emails_opened: number
  emails_replied: number
  new_conversations: number
  active_conversations: number
  ai_handled: number
  manual_takeovers: number
  workflows_executed: number
  workflows_completed: number
  workflows_failed: number
}

export interface DashboardStats {
  today: DailyStats
  week: DailyStats
  month: DailyStats
  conversion_rate: number
  avg_response_time: number
}

export interface ActivityItem {
  id: string
  type: ActivityType
  description: string
  timestamp: string
  metadata?: Record<string, unknown>
}

export type ActivityType =
  | 'message_sent'
  | 'message_delivered'
  | 'message_opened'
  | 'reply_received'
  | 'workflow_started'
  | 'workflow_completed'
  | 'workflow_failed'
  | 'customer_created'
  | 'customer_updated'
  | 'takeover_requested'
  | 'system_alert'

export interface FunnelStage {
  name: string
  value: number
}

export interface FunnelResponse {
  stages: FunnelStage[]
  conversion_rates: Array<{ stage: string; rate: number }>
}

export interface TrendPoint {
  date: string
  new_customers: number
  emails_sent: number
  whatsapp_sent: number
  emails_replied: number
  conversions: number
}

export interface TrendsResponse {
  days: number
  stats: TrendPoint[]
}

export interface MetricCard {
  label: string
  value: number
  suffix?: string
  trend?: number
  period?: string
  color?: 'primary' | 'success' | 'warning' | 'danger' | 'info'
  icon?: string
}
