<template>
  <section
    class="agent-center page-stack"
    aria-labelledby="agent-center-title"
  >
    <header class="page-heading">
      <div>
        <p class="page-kicker">
          {{ $t('Autonomous revenue system') }}
        </p>
        <h1 id="agent-center-title">
          {{ $t('Agent Center') }}
        </h1>
        <p>{{ $t('See how B-agent discovers prospects, runs skills, routes AI and converts conversations.') }}</p>
      </div>
      <div class="heading-actions">
        <el-button @click="router.push('/settings')">
          {{ $t('Configure AI route') }}
        </el-button>
        <el-button @click="router.push('/workflows')">
          {{ $t('Open workflow studio') }}
        </el-button>
        <el-button
          type="primary"
          :loading="loading"
          @click="loadAgent"
        >
          <el-icon><Refresh /></el-icon>
          {{ $t('Refresh') }}
        </el-button>
      </div>
    </header>

    <el-alert
      v-if="errorMessage"
      type="error"
      :closable="false"
      show-icon
      :title="errorMessage"
    />

    <article
      v-loading="loading && !overview"
      class="agent-hero apple-glass"
    >
      <div class="agent-identity">
        <div
          class="agent-orb"
          aria-hidden="true"
        >
          <img
            src="/b-agent-logo.svg"
            alt=""
          >
          <span class="orb-pulse" />
        </div>
        <div>
          <div class="identity-line">
            <h2>{{ overview?.agent.name || 'B-agent' }}</h2>
            <el-tag
              :type="overview?.agent.status === 'ready' ? 'success' : 'warning'"
              effect="plain"
              round
            >
              {{ $t(overview?.agent.status || 'unknown') }}
            </el-tag>
          </div>
          <p>{{ $t(overview?.agent.description || 'B2B revenue acquisition and conversion agent') }}</p>
        </div>
      </div>

      <div class="runtime-grid">
        <div class="runtime-metric">
          <span>{{ $t('Runtime mode') }}</span>
          <strong>{{ $t(overview?.runtime.mode || 'unknown') }}</strong>
        </div>
        <div class="runtime-metric">
          <span>{{ $t('Registered skills') }}</span>
          <strong>{{ overview?.runtime.registered_skill_count || 0 }}</strong>
        </div>
        <div class="runtime-metric">
          <span>{{ $t('Loaded workflows') }}</span>
          <strong>{{ overview?.runtime.registered_workflow_count || 0 }}</strong>
        </div>
        <div class="runtime-metric">
          <span>{{ $t('Active runs') }}</span>
          <strong>{{ overview?.runtime.active_run_count || 0 }}</strong>
        </div>
      </div>
    </article>

    <div class="section-heading research-heading">
      <div>
        <p class="page-kicker">
          {{ $t('Evidence-led account intelligence') }}
        </p>
        <h2>{{ $t('Research queue') }}</h2>
        <p>{{ $t('Build sourced company dossiers, review market signals and create approval-only outreach drafts.') }}</p>
      </div>
      <el-button
        type="primary"
        @click="openCreateResearch"
      >
        {{ $t('New research job') }}
      </el-button>
    </div>

    <div class="research-metrics">
      <div>
        <span>{{ $t('Queued') }}</span>
        <strong>{{ researchCounts.queued }}</strong>
      </div>
      <div>
        <span>{{ $t('Awaiting review') }}</span>
        <strong>{{ researchCounts.inReview }}</strong>
      </div>
      <div>
        <span>{{ $t('Approved dossiers') }}</span>
        <strong>{{ researchCounts.completed }}</strong>
      </div>
      <div>
        <span>{{ $t('Outreach drafts') }}</span>
        <strong>{{ researchCounts.drafts }}</strong>
      </div>
    </div>

    <el-card
      shadow="never"
      class="research-card"
    >
      <el-table
        v-loading="researchLoading"
        :data="researchJobs"
        row-key="id"
        :empty-text="$t('No research jobs yet')"
      >
        <el-table-column
          :label="$t('Company')"
          min-width="190"
        >
          <template #default="{ row }">
            <div class="research-company">
              <strong>{{ row.company_name }}</strong>
              <a
                v-if="row.website"
                :href="row.website"
                target="_blank"
                rel="noopener noreferrer"
              >{{ row.website }}</a>
            </div>
          </template>
        </el-table-column>
        <el-table-column
          prop="objective"
          :label="$t('Research objective')"
          min-width="220"
          show-overflow-tooltip
        />
        <el-table-column
          :label="$t('Status')"
          width="130"
        >
          <template #default="{ row }">
            <el-tag
              :type="researchStatusType(row.status)"
              effect="plain"
            >
              {{ $t(row.status) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column
          :label="$t('Evidence')"
          width="126"
        >
          <template #default="{ row }">
            {{ row.profile_evidence.length + row.market_signals.length }} · v{{ row.version }}
          </template>
        </el-table-column>
        <el-table-column
          :label="$t('Missing signals')"
          min-width="170"
        >
          <template #default="{ row }">
            <span v-if="row.missing_fields.length">{{ formatMissingFields(row.missing_fields) }}</span>
            <el-tag
              v-else
              type="success"
              size="small"
              effect="plain"
            >
              {{ $t('Complete') }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column
          :label="$t('Actions')"
          min-width="250"
          fixed="right"
        >
          <template #default="{ row }">
            <el-button
              link
              type="primary"
              @click="openEvidence(row)"
            >
              {{ $t('Evidence review') }}
            </el-button>
            <el-button
              link
              type="primary"
              :disabled="row.status !== 'completed'"
              @click="openDraft(row)"
            >
              {{ $t('Generate outreach draft') }}
            </el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <div class="section-heading">
      <div>
        <p class="page-kicker">
          {{ $t('Orchestration map') }}
        </p>
        <h2>{{ $t('Business pipelines') }}</h2>
        <p>{{ $t('The actual skill chains that power acquisition, outreach and conversion.') }}</p>
      </div>
    </div>

    <div class="pipeline-grid">
      <article
        v-for="pipeline in overview?.pipelines || []"
        :key="pipeline.id"
        class="pipeline-card"
        :class="`pipeline-card--${pipeline.accent}`"
      >
        <div class="pipeline-header">
          <span class="pipeline-index">{{ String((overview?.pipelines || []).indexOf(pipeline) + 1).padStart(2, '0') }}</span>
          <div>
            <h3>{{ $t(pipeline.name) }}</h3>
            <p>{{ $t(pipeline.description) }}</p>
          </div>
        </div>
        <ol class="stage-list">
          <li
            v-for="(stage, index) in pipeline.stages"
            :key="`${pipeline.id}-${stage.skill}`"
          >
            <span class="stage-marker">{{ index + 1 }}</span>
            <div>
              <strong>{{ $t(stage.name) }}</strong>
              <code>{{ stage.skill }}</code>
            </div>
            <el-tag
              :type="capabilityReady(stage.skill) ? 'success' : 'danger'"
              size="small"
              effect="plain"
            >
              {{ $t(capabilityReady(stage.skill) ? 'Ready' : 'Unavailable') }}
            </el-tag>
          </li>
        </ol>
      </article>
    </div>

    <div class="agent-details-grid">
      <el-card
        shadow="never"
        class="routing-card"
      >
        <template #header>
          <div class="card-heading">
            <div>
              <strong>{{ $t('AI routing and policy') }}</strong>
              <span>{{ $t('The active model gateway contract') }}</span>
            </div>
            <el-tag effect="plain">
              {{ overview?.routing.backend || '-' }}
            </el-tag>
          </div>
        </template>
        <dl class="routing-details">
          <div>
            <dt>{{ $t('Routing backend') }}</dt>
            <dd>{{ overview?.routing.backend || '-' }}</dd>
          </div>
          <div>
            <dt>{{ $t('Approved providers') }}</dt>
            <dd>{{ providerPolicy }}</dd>
          </div>
          <div>
            <dt>{{ $t('Configured model aliases') }}</dt>
            <dd>{{ configuredModels.length }}</dd>
          </div>
        </dl>
        <div
          v-if="configuredModels.length"
          class="model-list"
        >
          <div
            v-for="[useCase, model] in configuredModels"
            :key="useCase"
          >
            <span>{{ formatKey(useCase) }}</span>
            <code>{{ model }}</code>
          </div>
        </div>
        <el-empty
          v-else
          :image-size="62"
          :description="$t('No model aliases configured')"
        />
      </el-card>

      <el-card
        shadow="never"
        class="capability-card"
      >
        <template #header>
          <div class="card-heading">
            <div>
              <strong>{{ $t('Capability registry') }}</strong>
              <span>{{ $t('Every skill available to the orchestrator') }}</span>
            </div>
            <el-tag
              type="success"
              effect="plain"
            >
              {{ overview?.capabilities.length || 0 }}
            </el-tag>
          </div>
        </template>
        <div class="capability-list">
          <div
            v-for="capability in overview?.capabilities || []"
            :key="capability.name"
            class="capability-row"
          >
            <span
              class="capability-dot"
              :class="{ 'is-ready': capability.ready }"
            />
            <div>
              <strong>{{ $t(capability.display_name) }}</strong>
              <span>{{ $t(capability.category) }} · v{{ capability.version }}</span>
            </div>
            <code>{{ capability.name }}</code>
          </div>
        </div>
      </el-card>
    </div>

    <el-card
      shadow="never"
      class="runs-card"
    >
      <template #header>
        <div class="card-heading">
          <div>
            <strong>{{ $t('Live agent runs') }}</strong>
            <span>{{ $t('Current in-process orchestration executions') }}</span>
          </div>
          <el-tag effect="plain">
            {{ runs.length }}
          </el-tag>
        </div>
      </template>
      <el-table
        :data="runs"
        row-key="id"
        :empty-text="$t('No agent runs yet')"
      >
        <el-table-column
          prop="workflow_id"
          :label="$t('Workflow')"
          min-width="180"
        />
        <el-table-column
          :label="$t('Status')"
          width="120"
        >
          <template #default="{ row }">
            <el-tag
              :type="statusType(row.status)"
              effect="plain"
            >
              {{ $t(row.status) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column
          :label="$t('Progress')"
          min-width="170"
        >
          <template #default="{ row }">
            <el-progress
              :percentage="row.metrics.progress"
              :stroke-width="7"
            />
          </template>
        </el-table-column>
        <el-table-column
          prop="current_step"
          :label="$t('Current step')"
          min-width="150"
        >
          <template #default="{ row }">
            {{ row.current_step || $t('None') }}
          </template>
        </el-table-column>
        <el-table-column
          :label="$t('Started')"
          min-width="170"
        >
          <template #default="{ row }">
            {{ formatTime(row.started_at) }}
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <el-dialog
      v-model="createResearchVisible"
      append-to-body
      :title="$t('New research job')"
      width="min(560px, 94vw)"
    >
      <el-form label-position="top">
        <el-form-item :label="$t('Customer')">
          <el-select
            v-model="researchCreate.customer_id"
            filterable
            :placeholder="$t('Choose an imported customer')"
          >
            <el-option
              v-for="customer in customers"
              :key="customer.id"
              :label="customer.company_name || customer.username || customer.email || `#${customer.id}`"
              :value="customer.id"
            />
          </el-select>
        </el-form-item>
        <el-form-item :label="$t('Research objective')">
          <el-input
            v-model="researchCreate.objective"
            type="textarea"
            :rows="3"
            :placeholder="$t('Example: validate distributor fit and recent expansion signals')"
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="createResearchVisible = false">
          {{ $t('Cancel') }}
        </el-button>
        <el-button
          type="primary"
          :loading="creatingResearch"
          :disabled="!researchCreate.customer_id || researchCreate.objective.trim().length < 3"
          @click="createResearchJob"
        >
          {{ $t('Create research job') }}
        </el-button>
      </template>
    </el-dialog>

    <el-dialog
      v-model="evidenceVisible"
      class="research-dialog"
      append-to-body
      :title="$t('Evidence review')"
      width="min(880px, 96vw)"
      top="4vh"
    >
      <div
        v-if="activeResearch"
        class="evidence-workbench"
      >
        <div class="dialog-context">
          <div>
            <span>{{ $t('Company') }}</span>
            <strong>{{ activeResearch.company_name }}</strong>
          </div>
          <div>
            <span>{{ $t('Research objective') }}</span>
            <strong>{{ activeResearch.objective }}</strong>
          </div>
          <el-tag :type="researchStatusType(activeResearch.status)">
            {{ $t(activeResearch.status) }} · v{{ activeResearch.version }}
          </el-tag>
        </div>

        <div class="evidence-section">
          <div class="evidence-heading">
            <div>
              <strong>{{ $t('Company profile evidence') }}</strong>
              <span>{{ $t('Every value must include a public HTTP(S) source.') }}</span>
            </div>
            <el-button @click="addProfileEvidence">
              {{ $t('Add profile evidence') }}
            </el-button>
          </div>
          <div
            v-for="(item, index) in evidenceDraft.profile_evidence"
            :key="`profile-${index}`"
            class="evidence-row profile-row"
          >
            <el-select v-model="item.field">
              <el-option
                v-for="field in profileFields"
                :key="field"
                :label="$t(field)"
                :value="field"
              />
            </el-select>
            <el-input
              v-model="item.value"
              :placeholder="$t('Observed value')"
            />
            <el-input
              v-model="item.source_url"
              placeholder="https://"
            />
            <el-date-picker
              v-model="item.observed_at"
              type="date"
              value-format="YYYY-MM-DDT00:00:00Z"
              :placeholder="$t('Observed date')"
            />
            <el-input-number
              v-model="item.confidence"
              :min="0"
              :max="1"
              :step="0.05"
            />
            <el-button
              circle
              text
              type="danger"
              :aria-label="$t('Remove')"
              @click="evidenceDraft.profile_evidence.splice(index, 1)"
            >
              ×
            </el-button>
          </div>
        </div>

        <div class="evidence-section">
          <div class="evidence-heading">
            <div>
              <strong>{{ $t('Market signals') }}</strong>
              <span>{{ $t('Record a specific event, its date and the original source.') }}</span>
            </div>
            <el-button @click="addMarketSignal">
              {{ $t('Add market signal') }}
            </el-button>
          </div>
          <div
            v-for="(item, index) in evidenceDraft.market_signals"
            :key="`signal-${index}`"
            class="evidence-row signal-row"
          >
            <el-select v-model="item.type">
              <el-option
                v-for="signal in signalTypes"
                :key="signal"
                :label="$t(signal)"
                :value="signal"
              />
            </el-select>
            <el-input
              v-model="item.summary"
              :placeholder="$t('Observed market signal')"
            />
            <el-input
              v-model="item.source_url"
              placeholder="https://"
            />
            <el-date-picker
              v-model="item.observed_at"
              type="date"
              value-format="YYYY-MM-DDT00:00:00Z"
              :placeholder="$t('Observed date')"
            />
            <el-input-number
              v-model="item.confidence"
              :min="0"
              :max="1"
              :step="0.05"
            />
            <el-button
              circle
              text
              type="danger"
              :aria-label="$t('Remove')"
              @click="evidenceDraft.market_signals.splice(index, 1)"
            >
              ×
            </el-button>
          </div>
        </div>

        <el-alert
          type="info"
          :closable="false"
          show-icon
          :title="$t('AI will only receive evidence from the approved dossier and current ICP context.')"
        />
      </div>
      <template #footer>
        <el-button @click="evidenceVisible = false">
          {{ $t('Close') }}
        </el-button>
        <el-button
          :loading="savingEvidence"
          @click="saveEvidence"
        >
          {{ $t('Save evidence') }}
        </el-button>
        <el-button
          type="danger"
          plain
          :disabled="activeResearch?.status !== 'in_review'"
          @click="reviewResearch('reject')"
        >
          {{ $t('Request revision') }}
        </el-button>
        <el-button
          type="primary"
          :disabled="activeResearch?.status !== 'in_review'"
          @click="reviewResearch('approve')"
        >
          {{ $t('Approve dossier') }}
        </el-button>
      </template>
    </el-dialog>

    <el-dialog
      v-model="draftVisible"
      class="draft-dialog"
      append-to-body
      :title="$t('Generate outreach draft')"
      width="min(720px, 95vw)"
      top="5vh"
    >
      <div
        v-if="activeResearch"
        class="draft-workbench"
      >
        <el-alert
          v-if="latestDraft?.stale"
          type="warning"
          :closable="false"
          show-icon
          :title="$t('This draft is stale because its research dossier changed.')"
        />
        <el-form label-position="top">
          <div class="draft-form-grid">
            <el-form-item :label="$t('Channel')">
              <el-select v-model="draftCreate.channel">
                <el-option
                  :label="$t('Email')"
                  value="email"
                />
                <el-option
                  :label="$t('WhatsApp')"
                  value="whatsapp"
                />
              </el-select>
            </el-form-item>
            <el-form-item :label="$t('Language')">
              <el-input v-model="draftCreate.language" />
            </el-form-item>
          </div>
          <el-form-item :label="$t('One clear goal')">
            <el-input
              v-model="draftCreate.goal"
              :placeholder="$t('Example: request a 20-minute distributor-fit call')"
            />
          </el-form-item>
        </el-form>

        <div
          v-if="latestDraft"
          class="draft-preview"
        >
          <div class="draft-meta">
            <el-tag :type="draftStatusType(latestDraft.status)">
              {{ $t(latestDraft.status) }}
            </el-tag>
            <span>{{ latestDraft.resolved_provider || $t('Provider unavailable') }} · {{ latestDraft.resolved_model || $t('Model unavailable') }}</span>
          </div>
          <div v-if="latestDraft.subject">
            <span>{{ $t('Subject') }}</span>
            <strong>{{ latestDraft.subject }}</strong>
          </div>
          <div>
            <span>{{ $t('Body') }}</span>
            <pre>{{ latestDraft.body }}</pre>
          </div>
          <div class="draft-evidence">
            <span>{{ $t('Evidence references') }}</span>
            <code
              v-for="evidenceId in latestDraft.evidence_ids"
              :key="evidenceId"
            >{{ evidenceId }}</code>
          </div>
        </div>
      </div>
      <template #footer>
        <el-button @click="draftVisible = false">
          {{ $t('Close') }}
        </el-button>
        <el-button
          :loading="generatingDraft"
          :disabled="draftCreate.goal.trim().length < 3"
          @click="generateDraft"
        >
          {{ $t('Generate outreach draft') }}
        </el-button>
        <el-button
          type="danger"
          plain
          :disabled="!latestDraft || latestDraft.status !== 'draft'"
          @click="reviewDraft('reject')"
        >
          {{ $t('Reject draft') }}
        </el-button>
        <el-button
          type="primary"
          :disabled="!latestDraft || latestDraft.status !== 'draft' || latestDraft.stale"
          @click="reviewDraft('approve')"
        >
          {{ $t('Approve draft') }}
        </el-button>
      </template>
    </el-dialog>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import dayjs from 'dayjs'
import { ElMessage, ElMessageBox } from 'element-plus'
import { agentApi } from '@/api/agent'
import { customerApi } from '@/api/customer'
import type { Customer } from '@/types'
import type {
  AgentOverview,
  AgentResearchJob,
  AgentRun,
  ResearchEvidenceUpdate,
  ResearchOutreachDraft,
} from '@/types/agent'
import { translate } from '@/i18n'

type ResearchCustomerOption = Customer & { company_name?: string }

const router = useRouter()
const loading = ref(false)
const errorMessage = ref('')
const overview = ref<AgentOverview | null>(null)
const runs = ref<AgentRun[]>([])
const customers = ref<ResearchCustomerOption[]>([])
const researchJobs = ref<AgentResearchJob[]>([])
const researchLoading = ref(false)
const createResearchVisible = ref(false)
const creatingResearch = ref(false)
const evidenceVisible = ref(false)
const savingEvidence = ref(false)
const draftVisible = ref(false)
const generatingDraft = ref(false)
const activeResearch = ref<AgentResearchJob | null>(null)
const researchCreate = ref({ customer_id: undefined as number | undefined, objective: '' })
const evidenceDraft = ref<ResearchEvidenceUpdate>({ profile_evidence: [], market_signals: [] })
const draftCreate = ref({
  channel: 'email' as 'email' | 'whatsapp',
  language: 'en',
  goal: '',
  idempotency_key: '',
})

const profileFields = ['industry', 'country', 'company_size', 'company_type', 'website', 'market'] as const
const signalTypes = ['market_expansion', 'product_launch', 'hiring', 'funding', 'certification', 'distribution', 'partnership', 'news', 'other'] as const

const configuredModels = computed(() => Object.entries(overview.value?.routing.models || {}).filter(([, model]) => Boolean(model)))
const providerPolicy = computed(() => overview.value?.routing.provider_policy.length
  ? overview.value.routing.provider_policy.join(', ')
  : translate('No providers approved'))
const researchCounts = computed(() => ({
  queued: researchJobs.value.filter((item) => item.status === 'queued').length,
  inReview: researchJobs.value.filter((item) => ['in_review', 'needs_revision'].includes(item.status)).length,
  completed: researchJobs.value.filter((item) => item.status === 'completed').length,
  drafts: researchJobs.value.reduce((count, item) => count + item.drafts.length, 0),
}))
const latestDraft = computed(() => activeResearch.value?.drafts[0] ?? null)

async function loadAgent() {
  loading.value = true
  researchLoading.value = true
  errorMessage.value = ''
  try {
    const [overviewResult, runResult, researchResult, customerResult] = await Promise.all([
      agentApi.overview(),
      agentApi.runs(),
      agentApi.researchJobs(),
      customerApi.list({ page: 1, page_size: 100 }),
    ])
    overview.value = overviewResult
    runs.value = runResult
    researchJobs.value = researchResult
    customers.value = customerResult.items as ResearchCustomerOption[]
  } catch {
    errorMessage.value = translate('Agent runtime could not be loaded.')
  } finally {
    loading.value = false
    researchLoading.value = false
  }
}

function openCreateResearch() {
  researchCreate.value = { customer_id: undefined, objective: '' }
  createResearchVisible.value = true
}

async function createResearchJob() {
  if (!researchCreate.value.customer_id) return
  creatingResearch.value = true
  try {
    const created = await agentApi.createResearchJob({
      customer_id: researchCreate.value.customer_id,
      objective: researchCreate.value.objective.trim(),
    })
    researchJobs.value.unshift(created)
    createResearchVisible.value = false
    ElMessage.success(translate('Research job created.'))
  } catch {
    ElMessage.error(translate('Research job could not be created.'))
  } finally {
    creatingResearch.value = false
  }
}

function openEvidence(job: AgentResearchJob) {
  activeResearch.value = job
  evidenceDraft.value = {
    profile_evidence: job.profile_evidence.map((item) => ({
      field: item.field,
      value: item.value,
      source_url: item.source_url,
      observed_at: item.observed_at,
      confidence: item.confidence,
    })),
    market_signals: job.market_signals.map((item) => ({
      type: item.type,
      summary: item.summary,
      source_url: item.source_url,
      observed_at: item.observed_at,
      confidence: item.confidence,
    })),
  }
  if (!evidenceDraft.value.profile_evidence.length) addProfileEvidence()
  if (!evidenceDraft.value.market_signals.length) addMarketSignal()
  evidenceVisible.value = true
}

function addProfileEvidence() {
  evidenceDraft.value.profile_evidence.push({
    field: 'industry',
    value: '',
    source_url: '',
    observed_at: new Date().toISOString(),
    confidence: 0.8,
  })
}

function addMarketSignal() {
  evidenceDraft.value.market_signals.push({
    type: 'market_expansion',
    summary: '',
    source_url: '',
    observed_at: new Date().toISOString(),
    confidence: 0.8,
  })
}

function replaceResearchJob(job: AgentResearchJob) {
  const index = researchJobs.value.findIndex((item) => item.id === job.id)
  if (index >= 0) researchJobs.value.splice(index, 1, job)
  else researchJobs.value.unshift(job)
  activeResearch.value = job
}

async function saveEvidence() {
  if (!activeResearch.value) return
  savingEvidence.value = true
  try {
    const updated = await agentApi.updateResearchEvidence(
      activeResearch.value.id,
      evidenceDraft.value,
    )
    replaceResearchJob(updated)
    ElMessage.success(translate('Research evidence saved for review.'))
  } catch {
    ElMessage.error(translate('Research evidence could not be saved. Check every source URL and field.'))
  } finally {
    savingEvidence.value = false
  }
}

async function reviewResearch(decision: 'approve' | 'reject') {
  if (!activeResearch.value) return
  try {
    const prompt = await ElMessageBox.prompt(
      translate(decision === 'approve' ? 'Record why this dossier is approved.' : 'Describe what evidence must be revised.'),
      translate(decision === 'approve' ? 'Approve dossier' : 'Request revision'),
      { inputValidator: (value: string) => value.trim().length >= 3 || translate('A review reason is required.') },
    )
    const reason = (prompt as { value: string }).value.trim()
    const updated = await agentApi.reviewResearchJob(activeResearch.value.id, {
      decision,
      reason,
    })
    replaceResearchJob(updated)
    ElMessage.success(translate(decision === 'approve' ? 'Research dossier approved.' : 'Revision requested.'))
  } catch (error) {
    if (isDialogCancel(error)) return
    ElMessage.error(translate('Research review could not be saved.'))
  }
}

function openDraft(job: AgentResearchJob) {
  activeResearch.value = job
  draftCreate.value = {
    channel: 'email',
    language: 'en',
    goal: '',
    idempotency_key: `research-${job.id}-${window.crypto.randomUUID()}`,
  }
  draftVisible.value = true
}

async function generateDraft() {
  if (!activeResearch.value) return
  generatingDraft.value = true
  try {
    const draft = await agentApi.createOutreachDraft(activeResearch.value.id, {
      ...draftCreate.value,
      goal: draftCreate.value.goal.trim(),
      language: draftCreate.value.language.trim(),
    })
    const drafts = activeResearch.value.drafts.filter((item) => item.id !== draft.id)
    replaceResearchJob({ ...activeResearch.value, drafts: [draft, ...drafts] })
    ElMessage.success(translate('Evidence-bound draft generated.'))
  } catch {
    ElMessage.error(translate('Draft could not be generated. Check ICP, contact verification and AI route configuration.'))
  } finally {
    generatingDraft.value = false
  }
}

async function reviewDraft(decision: 'approve' | 'reject') {
  const draft = latestDraft.value
  if (!draft || !activeResearch.value) return
  try {
    const prompt = await ElMessageBox.prompt(
      translate(decision === 'approve' ? 'Record why this draft is ready.' : 'Describe why this draft is rejected.'),
      translate(decision === 'approve' ? 'Approve draft' : 'Reject draft'),
      { inputValidator: (value: string) => value.trim().length >= 3 || translate('A review reason is required.') },
    )
    const reason = (prompt as { value: string }).value.trim()
    const reviewed = await agentApi.reviewOutreachDraft(draft.id, {
      decision,
      reason,
    })
    const drafts = activeResearch.value.drafts.map((item) => item.id === reviewed.id ? reviewed : item)
    replaceResearchJob({ ...activeResearch.value, drafts })
    ElMessage.success(translate(decision === 'approve' ? 'Draft approved for later delivery workflow.' : 'Draft rejected.'))
  } catch (error) {
    if (isDialogCancel(error)) return
    ElMessage.error(translate('Draft review could not be saved.'))
  }
}

function capabilityReady(name: string) {
  return overview.value?.capabilities.some((capability) => capability.name === name && capability.ready) ?? false
}

function statusType(status: AgentRun['status']) {
  return ({ completed: 'success', failed: 'danger', running: 'primary', paused: 'warning', cancelled: 'info', pending: 'info' } as const)[status]
}

function researchStatusType(status: AgentResearchJob['status']) {
  return ({ queued: 'info', in_review: 'warning', completed: 'success', needs_revision: 'danger' } as const)[status]
}

function draftStatusType(status: ResearchOutreachDraft['status']) {
  return ({ draft: 'info', approved: 'success', rejected: 'danger' } as const)[status]
}

function formatMissingFields(values: string[]) {
  return values.map((item) => translate(item)).join(', ')
}

function isDialogCancel(error: unknown) {
  const action = typeof error === 'string'
    ? error
    : (error as { action?: string } | null)?.action
  return action === 'cancel' || action === 'close'
}

function formatKey(value: string) {
  return value.replace(/_/g, ' ').replace(/\b\w/g, (letter: string) => letter.toUpperCase())
}

function formatTime(value?: string) {
  return value ? dayjs(value).format('YYYY-MM-DD HH:mm:ss') : translate('Not available')
}

onMounted(loadAgent)
</script>

<style scoped lang="scss">
.agent-center { padding-bottom: 36px; }
.heading-actions { display: flex; flex-wrap: wrap; gap: 10px; }
.agent-hero { position: relative; display: grid; overflow: hidden; min-height: 180px; grid-template-columns: minmax(280px, 1.1fr) minmax(440px, 1fr); align-items: center; gap: 32px; padding: 28px; }
.agent-hero::after { position: absolute; width: 380px; height: 380px; border-radius: 50%; background: color-mix(in srgb, var(--apple-blue) 12%, transparent); content: ''; filter: blur(70px); inset: -240px -90px auto auto; pointer-events: none; }
.agent-identity { position: relative; z-index: 1; display: flex; align-items: center; gap: 20px; }
.agent-orb { position: relative; display: grid; width: 84px; flex: 0 0 84px; aspect-ratio: 1; place-items: center; border: 1px solid color-mix(in srgb, var(--apple-blue) 22%, var(--border-hairline)); border-radius: 24px; background: color-mix(in srgb, var(--surface-elevated) 86%, transparent); box-shadow: 0 18px 45px color-mix(in srgb, var(--apple-blue) 18%, transparent); }
.agent-orb img { width: 58px; height: 58px; }.orb-pulse { position: absolute; right: -3px; bottom: -3px; width: 16px; height: 16px; border: 3px solid var(--surface-elevated); border-radius: 50%; background: var(--el-color-success); }
.identity-line { display: flex; align-items: center; gap: 12px; }.identity-line h2 { margin: 0; font-size: clamp(26px, 3vw, 38px); letter-spacing: -0.04em; }.agent-identity p { max-width: 480px; margin: 8px 0 0; color: var(--el-text-color-secondary); }
.runtime-grid { position: relative; z-index: 1; display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 10px; }
.runtime-metric { display: grid; min-height: 92px; align-content: center; gap: 8px; padding: 16px; border: 1px solid var(--border-hairline); border-radius: 16px; background: color-mix(in srgb, var(--surface-elevated) 78%, transparent); }.runtime-metric span { color: var(--el-text-color-secondary); font-size: 12px; }.runtime-metric strong { font-size: 22px; letter-spacing: -0.03em; white-space: nowrap; }.runtime-metric:first-child strong { font-size: 18px; }
.section-heading { display: flex; justify-content: space-between; margin-top: 4px; }.section-heading h2 { margin: 3px 0 7px; font-size: 24px; letter-spacing: -0.025em; }.section-heading p:last-child { margin: 0; color: var(--el-text-color-secondary); }
.research-heading { align-items: end; gap: 20px; }
.research-metrics { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; }.research-metrics > div { display: grid; gap: 7px; padding: 16px 18px; border: 1px solid var(--border-hairline); border-radius: 16px; background: var(--surface-elevated); box-shadow: var(--shadow-card); }.research-metrics span { color: var(--el-text-color-secondary); font-size: 12px; }.research-metrics strong { font-size: 24px; letter-spacing: -0.04em; }
.research-card :deep(.el-card__body) { padding-top: 8px; }.research-company { display: grid; gap: 4px; }.research-company strong { font-size: 13px; }.research-company a { overflow: hidden; color: var(--apple-blue); font-size: 11px; text-overflow: ellipsis; white-space: nowrap; }
.evidence-workbench, .draft-workbench { display: grid; gap: 18px; }.dialog-context { display: grid; grid-template-columns: minmax(160px, 0.8fr) minmax(220px, 1.2fr) auto; align-items: center; gap: 16px; padding: 15px; border-radius: 14px; background: var(--el-fill-color-light); }.dialog-context > div { display: grid; gap: 4px; }.dialog-context span { color: var(--el-text-color-secondary); font-size: 11px; }.dialog-context strong { font-size: 13px; }
.evidence-section { display: grid; gap: 12px; padding: 16px; border: 1px solid var(--border-hairline); border-radius: 16px; }.evidence-heading { display: flex; align-items: center; justify-content: space-between; gap: 16px; }.evidence-heading > div { display: grid; gap: 4px; }.evidence-heading span { color: var(--el-text-color-secondary); font-size: 12px; }.evidence-row { display: grid; grid-template-columns: 145px minmax(160px, 1fr) minmax(190px, 1.2fr) 150px 104px 34px; align-items: center; gap: 8px; }.evidence-row :deep(.el-input-number), .evidence-row :deep(.el-date-editor) { width: 100%; }
.draft-form-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }.draft-preview { display: grid; gap: 16px; padding: 18px; border: 1px solid var(--border-hairline); border-radius: 16px; background: var(--el-fill-color-extra-light); }.draft-preview > div:not(.draft-meta) { display: grid; gap: 6px; }.draft-preview span { color: var(--el-text-color-secondary); font-size: 11px; }.draft-preview pre { margin: 0; font: inherit; line-height: 1.7; white-space: pre-wrap; }.draft-meta { display: flex; align-items: center; justify-content: space-between; gap: 12px; }.draft-evidence code { overflow: hidden; font-size: 10px; text-overflow: ellipsis; }
.pipeline-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 16px; }
.pipeline-card { --pipeline-accent: var(--apple-blue); overflow: hidden; padding: 20px; border: 1px solid var(--border-hairline); border-radius: 20px; background: var(--surface-elevated); box-shadow: var(--shadow-card); }.pipeline-card--green { --pipeline-accent: var(--el-color-success); }.pipeline-card--orange { --pipeline-accent: var(--el-color-warning); }
.pipeline-header { display: flex; min-height: 94px; gap: 13px; }.pipeline-index { display: grid; width: 38px; height: 38px; flex: 0 0 38px; place-items: center; border-radius: 11px; background: color-mix(in srgb, var(--pipeline-accent) 12%, transparent); color: var(--pipeline-accent); font-size: 12px; font-weight: 700; }.pipeline-header h3 { margin: 2px 0 6px; font-size: 17px; }.pipeline-header p { margin: 0; color: var(--el-text-color-secondary); font-size: 13px; line-height: 1.5; }
.stage-list { display: grid; gap: 0; margin: 12px 0 0; padding: 0; list-style: none; }.stage-list li { position: relative; display: grid; min-height: 58px; grid-template-columns: 28px minmax(0, 1fr) auto; align-items: center; gap: 10px; }.stage-list li:not(:last-child)::after { position: absolute; width: 1px; height: 17px; background: var(--border-hairline); content: ''; left: 13px; bottom: -8px; }.stage-marker { display: grid; width: 28px; aspect-ratio: 1; place-items: center; border: 1px solid color-mix(in srgb, var(--pipeline-accent) 32%, var(--border-hairline)); border-radius: 50%; color: var(--pipeline-accent); font-size: 11px; font-weight: 700; }.stage-list li > div { display: grid; gap: 3px; min-width: 0; }.stage-list strong { font-size: 13px; }.stage-list code { overflow: hidden; color: var(--el-text-color-secondary); font-size: 11px; text-overflow: ellipsis; }
.agent-details-grid { display: grid; grid-template-columns: minmax(0, 0.9fr) minmax(420px, 1.1fr); gap: 16px; }.card-heading { display: flex; align-items: center; justify-content: space-between; gap: 16px; }.card-heading > div { display: grid; gap: 3px; }.card-heading span { color: var(--el-text-color-secondary); font-size: 12px; }
.routing-details { display: grid; gap: 0; margin: 0; }.routing-details > div { display: flex; align-items: center; justify-content: space-between; gap: 20px; padding: 11px 0; border-bottom: 1px solid var(--border-hairline); }.routing-details dt { color: var(--el-text-color-secondary); }.routing-details dd { margin: 0; font-weight: 600; text-align: right; }.model-list { display: grid; gap: 8px; margin-top: 14px; }.model-list > div { display: grid; grid-template-columns: 1fr minmax(130px, auto); gap: 12px; padding: 9px 11px; border-radius: 10px; background: var(--el-fill-color-light); }.model-list span { color: var(--el-text-color-secondary); font-size: 12px; }.model-list code { font-size: 11px; text-align: right; }
.capability-list { display: grid; max-height: 390px; overflow: auto; }.capability-row { display: grid; grid-template-columns: 10px minmax(0, 1fr) auto; align-items: center; gap: 11px; padding: 10px 2px; border-bottom: 1px solid var(--border-hairline); }.capability-dot { width: 8px; height: 8px; border-radius: 50%; background: var(--el-color-danger); }.capability-dot.is-ready { background: var(--el-color-success); box-shadow: 0 0 0 4px color-mix(in srgb, var(--el-color-success) 12%, transparent); }.capability-row > div { display: grid; gap: 3px; }.capability-row strong { font-size: 13px; }.capability-row span { color: var(--el-text-color-secondary); font-size: 11px; }.capability-row code { color: var(--el-text-color-secondary); font-size: 11px; }
.runs-card :deep(.el-card__body) { padding-top: 4px; }
@media (max-width: 1180px) { .agent-hero { grid-template-columns: 1fr; }.pipeline-grid { grid-template-columns: 1fr; }.pipeline-header { min-height: auto; }.agent-details-grid { grid-template-columns: 1fr; }.evidence-row { grid-template-columns: 140px 1fr 1fr; }.evidence-row > :nth-child(n+4) { grid-row: 2; }.evidence-row > :nth-child(6) { justify-self: end; } }
@media (max-width: 720px) { .heading-actions { width: 100%; }.heading-actions .el-button { flex: 1; margin: 0; }.agent-hero { padding: 20px; }.agent-identity { align-items: flex-start; }.agent-orb { width: 64px; flex-basis: 64px; border-radius: 18px; }.agent-orb img { width: 44px; height: 44px; }.runtime-grid, .research-metrics { grid-template-columns: repeat(2, minmax(0, 1fr)); }.research-heading { align-items: stretch; flex-direction: column; }.agent-details-grid { display: block; }.agent-details-grid > * + * { margin-top: 16px; }.capability-row { grid-template-columns: 10px minmax(0, 1fr); }.capability-row code { grid-column: 2; }.pipeline-card { padding: 16px; }.dialog-context { grid-template-columns: 1fr; }.evidence-heading { align-items: stretch; flex-direction: column; }.evidence-row { grid-template-columns: 1fr; padding-bottom: 14px; border-bottom: 1px solid var(--border-hairline); }.evidence-row > :nth-child(n+4) { grid-column: 1; grid-row: auto; }.evidence-row > :nth-child(6) { justify-self: end; }.draft-form-grid { grid-template-columns: 1fr; }.draft-meta { align-items: flex-start; flex-direction: column; } }
</style>

<style lang="scss">
.research-dialog, .draft-dialog { max-height: 92vh; overflow: auto; }
</style>
