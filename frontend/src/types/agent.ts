export interface AgentCapability {
  name: string
  display_name: string
  description: string
  category: string
  version: string
  ready: boolean
}

export interface AgentPipelineStage {
  name: string
  skill: string
}

export interface AgentPipeline {
  id: string
  name: string
  description: string
  accent: 'blue' | 'green' | 'orange'
  stages: AgentPipelineStage[]
}

export interface AgentRun {
  id: string
  workflow_id: string
  status: 'pending' | 'running' | 'paused' | 'completed' | 'failed' | 'cancelled'
  current_step?: string
  started_at?: string
  finished_at?: string
  error_msg?: string
  completed_steps: string[]
  failed_steps: string[]
  metrics: {
    progress: number
    total_steps: number
    skipped_steps: number
    duration_seconds?: number
  }
}

export interface AgentOverview {
  agent: {
    name: string
    description: string
    status: string
  }
  runtime: {
    mode: 'full' | 'minimal'
    registered_skill_count: number
    registered_workflow_count: number
    active_run_count: number
  }
  routing: {
    backend: string
    provider_policy: string[]
    models: Record<string, string>
  }
  pipelines: AgentPipeline[]
  capabilities: AgentCapability[]
}

export interface ResearchProfileEvidence {
  id: string
  field: 'industry' | 'country' | 'company_size' | 'company_type' | 'website' | 'market'
  value: string
  source_url: string
  observed_at: string
  confidence: number
}

export interface ResearchMarketSignal {
  id: string
  type: 'market_expansion' | 'product_launch' | 'hiring' | 'funding' | 'certification' | 'distribution' | 'partnership' | 'news' | 'other'
  summary: string
  source_url: string
  observed_at: string
  confidence: number
}

export interface ResearchOutreachDraft {
  id: string
  research_job_id: string
  customer_id: number
  channel: 'email' | 'whatsapp'
  language: string
  goal: string
  subject?: string
  body: string
  personalization_points: string[]
  evidence_ids: string[]
  status: 'draft' | 'approved' | 'rejected'
  research_version: number
  stale: boolean
  resolved_model?: string
  resolved_provider?: string
  usage: Record<string, unknown>
  review_reason?: string
  reviewed_at?: string
  created_at: string
  updated_at: string
}

export interface AgentResearchJob {
  id: string
  customer_id: number
  company_name: string
  website?: string
  objective: string
  status: 'queued' | 'in_review' | 'completed' | 'needs_revision'
  profile_evidence: ResearchProfileEvidence[]
  market_signals: ResearchMarketSignal[]
  missing_fields: string[]
  version: number
  review_reason?: string
  reviewed_at?: string
  drafts: ResearchOutreachDraft[]
  created_at: string
  updated_at: string
}

export type ResearchEvidenceUpdate = {
  profile_evidence: Omit<ResearchProfileEvidence, 'id'>[]
  market_signals: Omit<ResearchMarketSignal, 'id'>[]
}
