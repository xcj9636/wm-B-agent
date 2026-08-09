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
              <strong>{{ capability.display_name }}</strong>
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
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import dayjs from 'dayjs'
import { agentApi } from '@/api/agent'
import type { AgentOverview, AgentRun } from '@/types/agent'
import { translate } from '@/i18n'

const router = useRouter()
const loading = ref(false)
const errorMessage = ref('')
const overview = ref<AgentOverview | null>(null)
const runs = ref<AgentRun[]>([])

const configuredModels = computed(() => Object.entries(overview.value?.routing.models || {}).filter(([, model]) => Boolean(model)))
const providerPolicy = computed(() => overview.value?.routing.provider_policy.length
  ? overview.value.routing.provider_policy.join(', ')
  : translate('No providers approved'))

async function loadAgent() {
  loading.value = true
  errorMessage.value = ''
  try {
    const [overviewResult, runResult] = await Promise.all([
      agentApi.overview(),
      agentApi.runs(),
    ])
    overview.value = overviewResult
    runs.value = runResult
  } catch {
    errorMessage.value = translate('Agent runtime could not be loaded.')
  } finally {
    loading.value = false
  }
}

function capabilityReady(name: string) {
  return overview.value?.capabilities.some((capability) => capability.name === name && capability.ready) ?? false
}

function statusType(status: AgentRun['status']) {
  return ({ completed: 'success', failed: 'danger', running: 'primary', paused: 'warning', cancelled: 'info', pending: 'info' } as const)[status]
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
.runtime-metric { display: grid; min-height: 92px; align-content: center; gap: 8px; padding: 16px; border: 1px solid var(--border-hairline); border-radius: 16px; background: color-mix(in srgb, var(--surface-elevated) 78%, transparent); }.runtime-metric span { color: var(--el-text-color-secondary); font-size: 12px; }.runtime-metric strong { font-size: 24px; letter-spacing: -0.03em; }
.section-heading { display: flex; justify-content: space-between; margin-top: 4px; }.section-heading h2 { margin: 3px 0 7px; font-size: 24px; letter-spacing: -0.025em; }.section-heading p:last-child { margin: 0; color: var(--el-text-color-secondary); }
.pipeline-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 16px; }
.pipeline-card { --pipeline-accent: var(--apple-blue); overflow: hidden; padding: 20px; border: 1px solid var(--border-hairline); border-radius: 20px; background: var(--surface-elevated); box-shadow: var(--shadow-card); }.pipeline-card--green { --pipeline-accent: var(--el-color-success); }.pipeline-card--orange { --pipeline-accent: var(--el-color-warning); }
.pipeline-header { display: flex; min-height: 94px; gap: 13px; }.pipeline-index { display: grid; width: 38px; height: 38px; flex: 0 0 38px; place-items: center; border-radius: 11px; background: color-mix(in srgb, var(--pipeline-accent) 12%, transparent); color: var(--pipeline-accent); font-size: 12px; font-weight: 700; }.pipeline-header h3 { margin: 2px 0 6px; font-size: 17px; }.pipeline-header p { margin: 0; color: var(--el-text-color-secondary); font-size: 13px; line-height: 1.5; }
.stage-list { display: grid; gap: 0; margin: 12px 0 0; padding: 0; list-style: none; }.stage-list li { position: relative; display: grid; min-height: 58px; grid-template-columns: 28px minmax(0, 1fr) auto; align-items: center; gap: 10px; }.stage-list li:not(:last-child)::after { position: absolute; width: 1px; height: 17px; background: var(--border-hairline); content: ''; left: 13px; bottom: -8px; }.stage-marker { display: grid; width: 28px; aspect-ratio: 1; place-items: center; border: 1px solid color-mix(in srgb, var(--pipeline-accent) 32%, var(--border-hairline)); border-radius: 50%; color: var(--pipeline-accent); font-size: 11px; font-weight: 700; }.stage-list li > div { display: grid; gap: 3px; min-width: 0; }.stage-list strong { font-size: 13px; }.stage-list code { overflow: hidden; color: var(--el-text-color-secondary); font-size: 11px; text-overflow: ellipsis; }
.agent-details-grid { display: grid; grid-template-columns: minmax(0, 0.9fr) minmax(420px, 1.1fr); gap: 16px; }.card-heading { display: flex; align-items: center; justify-content: space-between; gap: 16px; }.card-heading > div { display: grid; gap: 3px; }.card-heading span { color: var(--el-text-color-secondary); font-size: 12px; }
.routing-details { display: grid; gap: 0; margin: 0; }.routing-details > div { display: flex; align-items: center; justify-content: space-between; gap: 20px; padding: 11px 0; border-bottom: 1px solid var(--border-hairline); }.routing-details dt { color: var(--el-text-color-secondary); }.routing-details dd { margin: 0; font-weight: 600; text-align: right; }.model-list { display: grid; gap: 8px; margin-top: 14px; }.model-list > div { display: grid; grid-template-columns: 1fr minmax(130px, auto); gap: 12px; padding: 9px 11px; border-radius: 10px; background: var(--el-fill-color-light); }.model-list span { color: var(--el-text-color-secondary); font-size: 12px; }.model-list code { font-size: 11px; text-align: right; }
.capability-list { display: grid; max-height: 390px; overflow: auto; }.capability-row { display: grid; grid-template-columns: 10px minmax(0, 1fr) auto; align-items: center; gap: 11px; padding: 10px 2px; border-bottom: 1px solid var(--border-hairline); }.capability-dot { width: 8px; height: 8px; border-radius: 50%; background: var(--el-color-danger); }.capability-dot.is-ready { background: var(--el-color-success); box-shadow: 0 0 0 4px color-mix(in srgb, var(--el-color-success) 12%, transparent); }.capability-row > div { display: grid; gap: 3px; }.capability-row strong { font-size: 13px; }.capability-row span { color: var(--el-text-color-secondary); font-size: 11px; }.capability-row code { color: var(--el-text-color-secondary); font-size: 11px; }
.runs-card :deep(.el-card__body) { padding-top: 4px; }
@media (max-width: 1180px) { .agent-hero { grid-template-columns: 1fr; }.pipeline-grid { grid-template-columns: 1fr; }.pipeline-header { min-height: auto; }.agent-details-grid { grid-template-columns: 1fr; } }
@media (max-width: 720px) { .heading-actions { width: 100%; }.heading-actions .el-button { flex: 1; margin: 0; }.agent-hero { padding: 20px; }.agent-identity { align-items: flex-start; }.agent-orb { width: 64px; flex-basis: 64px; border-radius: 18px; }.agent-orb img { width: 44px; height: 44px; }.runtime-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }.agent-details-grid { display: block; }.agent-details-grid > * + * { margin-top: 16px; }.capability-row { grid-template-columns: 10px minmax(0, 1fr); }.capability-row code { grid-column: 2; }.pipeline-card { padding: 16px; } }
</style>
