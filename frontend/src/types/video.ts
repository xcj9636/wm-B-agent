export type VideoWorkflowMode =
  | 'auto'
  | 'text_to_image_then_image_to_video'
  | 'image_to_video'
  | 'text_to_video'
  | 'reference_to_video'

export interface VideoPersonaSpec {
  identity: {
    name: string
    brand_name: string
    markets: string[]
    languages: string[]
  }
  audience_segments: string[]
  narrative: {
    tone: string[]
    value_propositions: string[]
    calls_to_action: string[]
    prohibited_claims: string[]
  }
  visual_bible: {
    style: string[]
    palette: string[]
    camera_language: string[]
    forbidden_visuals: string[]
  }
  reference_asset_ids: string[]
  default_workflow: VideoWorkflowMode
}

export interface VideoPersonaRevision {
  persona_id: string
  version_id: string
  revision: number
  status: 'draft' | 'approved' | 'retired'
  spec_hash: string
  spec: VideoPersonaSpec
  approved_by_user_id: number | null
  approved_at: string | null
  created_at: string
}

export interface VideoProjectBrief {
  title: string
  objective: string
  product_summary: string
  target_audience: string
  markets: string[]
  channels: string[]
  language: string
  target_duration_seconds: number
}

export interface VideoProjectEvidence {
  id: string
  knowledge_record_id: string
  document_id: string
  document_version: number
  source_ref: string
  title: string
  authority: string
  sensitivity: string
  content_hash: string
}

export interface StoryboardShot {
  shot_id?: string
  sequence: number
  duration_seconds: number
  purpose: string
  workflow_mode: VideoWorkflowMode
  visual_prompt: string
  motion_prompt?: string
  spoken_copy?: string
  on_screen_copy?: string
  reference_asset_ids?: string[]
  business_claims?: string[]
  claim_evidence_ids?: string[]
  constraints?: string[]
}

export interface Storyboard {
  title: string
  total_duration_seconds: number
  shots: StoryboardShot[]
}

export interface StoryboardRevision {
  project_id: string
  version_id: string
  revision: number
  status: 'draft' | 'approved'
  storyboard_hash: string
  storyboard: Storyboard
  approved_by_user_id: number | null
  approved_at: string | null
  created_at: string
}

export interface VideoProject {
  id: string
  persona_version_id: string
  persona_spec_hash: string
  brief: VideoProjectBrief
  brief_hash: string
  sensitivity: string
  status: string
  evidence: VideoProjectEvidence[]
  created_at: string
  updated_at: string
}

export interface VideoProjectDetail extends VideoProject {
  storyboards: StoryboardRevision[]
}

export interface Paginated<T> {
  items: T[]
  total: number
  limit: number
  offset: number
}

export interface CompiledShotReceipt {
  shot_id: string
  persona_version_id: string
  mode: string
  sensitivity: string
  reference_asset_ids: string[]
  prompt_hash: string
  evidence_snapshot_hash: string
}

export type MediaGenerationJobStatus =
  | 'queued'
  | 'running'
  | 'submitting'
  | 'submitted'
  | 'cancel_requested'
  | 'submission_unknown'
  | 'succeeded'
  | 'failed'
  | 'cancelled'

export interface MediaGenerationJob {
  id: string
  project_id: string
  storyboard_version_id: string
  shot_id: string
  mode: string
  provider: string
  model_id: string
  sensitivity: string
  status: MediaGenerationJobStatus
  effect_state: string
  reservation_ceiling_microusd: number
  provider_state: string | null
  error_code: string | null
  created_at: string
  updated_at: string
  completed_at: string | null
}

export interface MediaGenerationEvent {
  sequence: number
  event_type: string
  data: Record<string, string | number | boolean>
  created_at: string
}

export interface MediaGenerationEventPage {
  items: MediaGenerationEvent[]
  next_sequence: number
}

export interface MediaGenerationStreamEvent {
  id?: number
  event: string
  data: Record<string, string | number | boolean>
}
