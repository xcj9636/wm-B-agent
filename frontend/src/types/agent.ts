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
