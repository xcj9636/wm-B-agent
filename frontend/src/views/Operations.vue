<template>
  <section
    class="operations page-stack"
    aria-labelledby="operations-title"
  >
    <header class="page-heading">
      <div>
        <p class="page-kicker">
          System control
        </p>
        <h1 id="operations-title">
          Operations
        </h1>
        <p>Monitor API connectivity, AI routing, durable execution and background tasks.</p>
      </div>
      <el-button
        :loading="loading"
        aria-label="Refresh system operations"
        @click="loadOperations"
      >
        <el-icon><Refresh /></el-icon>
        Refresh
      </el-button>
    </header>

    <el-alert
      v-if="loadError"
      type="error"
      :closable="false"
      show-icon
      title="Some operational data could not be loaded."
      :description="loadError"
    />

    <div class="health-grid">
      <article class="health-panel">
        <div class="panel-heading">
          <div>
            <span class="panel-label">Backend API</span>
            <strong>{{ health?.app || 'Unavailable' }}</strong>
          </div>
          <el-tag :type="health?.status === 'healthy' ? 'success' : 'danger'">
            {{ health?.status || 'unknown' }}
          </el-tag>
        </div>
        <dl class="detail-list">
          <div><dt>Version</dt><dd>{{ health?.version || '-' }}</dd></div>
          <div><dt>Checked</dt><dd>{{ formatTime(lastChecked) }}</dd></div>
        </dl>
      </article>

      <article class="health-panel">
        <div class="panel-heading">
          <div>
            <span class="panel-label">AI routing</span>
            <strong>{{ gateway?.backend || 'Unknown backend' }}</strong>
          </div>
          <el-tag :type="gatewayTagType">
            {{ gatewayState }}
          </el-tag>
        </div>
        <dl class="detail-list">
          <div><dt>Reachable</dt><dd>{{ booleanLabel(gateway?.reachable) }}</dd></div>
          <div><dt>Provider policy</dt><dd>{{ gateway?.allowed_providers.length || 0 }} allowed</dd></div>
          <div><dt>Configured aliases</dt><dd>{{ aliasCount }}</dd></div>
        </dl>
      </article>

      <article class="health-panel health-panel--attention">
        <div class="panel-heading">
          <div>
            <span class="panel-label">Durable delivery</span>
            <strong>Outbox</strong>
          </div>
          <el-tag :type="deadLetterCount > 0 ? 'danger' : 'success'">
            {{ deadLetterCount }} dead letters
          </el-tag>
        </div>
        <dl class="detail-list">
          <div><dt>Pending</dt><dd>{{ outboxCount('pending') }}</dd></div>
          <div><dt>Retry</dt><dd>{{ outboxCount('retry') }}</dd></div>
          <div><dt>Expired leases</dt><dd>{{ reliable?.expired_outbox_leases || 0 }}</dd></div>
        </dl>
        <router-link
          class="panel-link"
          to="/operations/dead-letters"
        >
          Open dead-letter console
        </router-link>
      </article>
    </div>

    <el-row :gutter="20">
      <el-col
        :xs="24"
        :xl="14"
      >
        <el-card shadow="never">
          <template #header>
            <div class="card-heading">
              <div>
                <strong>Background tasks</strong>
                <span>Latest scheduler records</span>
              </div>
              <el-select
                v-model="taskStatus"
                placeholder="All statuses"
                clearable
                aria-label="Filter tasks by status"
                @change="loadTasks"
              >
                <el-option
                  label="Pending"
                  value="pending"
                />
                <el-option
                  label="Running"
                  value="running"
                />
                <el-option
                  label="Completed"
                  value="completed"
                />
                <el-option
                  label="Failed"
                  value="failed"
                />
                <el-option
                  label="Retry"
                  value="retry"
                />
              </el-select>
            </div>
          </template>
          <el-table
            v-loading="tasksLoading"
            :data="tasks"
            row-key="id"
          >
            <el-table-column
              prop="task_type"
              label="Task"
              min-width="150"
            />
            <el-table-column
              prop="status"
              label="Status"
              width="120"
            >
              <template #default="{ row }">
                <el-tag
                  :type="taskTagType(row.status)"
                  effect="plain"
                >
                  {{ row.status || 'unknown' }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column
              label="Scheduled"
              min-width="170"
            >
              <template #default="{ row }">
                {{ formatTime(row.scheduled_at) }}
              </template>
            </el-table-column>
            <el-table-column
              prop="error_msg"
              label="Error"
              min-width="180"
              show-overflow-tooltip
            />
          </el-table>
          <el-empty
            v-if="!tasksLoading && tasks.length === 0"
            description="No task records"
          />
        </el-card>
      </el-col>

      <el-col
        :xs="24"
        :xl="10"
      >
        <el-card
          shadow="never"
          class="execution-card"
        >
          <template #header>
            <div class="card-heading">
              <div>
                <strong>LLM invocations</strong>
                <span>Durable business-level outcomes</span>
              </div>
            </div>
          </template>
          <div class="count-grid">
            <div
              v-for="(count, status) in reliable?.llm_invocation_counts"
              :key="status"
            >
              <span>{{ status }}</span>
              <strong>{{ count }}</strong>
            </div>
          </div>
          <el-divider />
          <div
            v-if="gateway?.issues.length"
            class="issues"
          >
            <span>Gateway issues</span>
            <el-tag
              v-for="issue in gateway.issues"
              :key="issue"
              type="warning"
              effect="plain"
            >
              {{ issue }}
            </el-tag>
          </div>
          <el-empty
            v-else
            description="No gateway issues reported"
            :image-size="72"
          />
        </el-card>
      </el-col>
    </el-row>

    <AlertPanel />
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import dayjs from 'dayjs'
import api from '@/api'
import AlertPanel from '@/components/Monitor/AlertPanel.vue'

interface HealthResponse {
  status: string
  app: string
  version: string
}

interface GatewayStatus {
  backend: string
  enabled: boolean
  ready: boolean
  reachable: boolean | null
  configured_aliases: Record<string, string>
  missing_aliases: string[]
  missing_models: string[]
  allowed_providers: string[]
  issues: string[]
}

interface ReliableStatus {
  outbox_counts: Record<string, number>
  llm_invocation_counts: Record<string, number>
  expired_outbox_leases: number
  oldest_pending_at: string | null
  checked_at: string
}

interface TaskSummary {
  id: string
  task_type: string
  status: string | null
  created_at: string | null
  scheduled_at: string | null
  executed_at: string | null
  error_msg: string | null
}

const loading = ref(false)
const tasksLoading = ref(false)
const loadError = ref('')
const lastChecked = ref<string | null>(null)
const health = ref<HealthResponse | null>(null)
const gateway = ref<GatewayStatus | null>(null)
const reliable = ref<ReliableStatus | null>(null)
const tasks = ref<TaskSummary[]>([])
const taskStatus = ref('')

const deadLetterCount = computed(() => reliable.value?.outbox_counts.dead_letter || 0)
const aliasCount = computed(() => Object.keys(gateway.value?.configured_aliases || {}).length)
const gatewayState = computed(() => {
  if (!gateway.value?.enabled) return 'disabled'
  return gateway.value.ready ? 'ready' : 'not ready'
})
const gatewayTagType = computed(() => {
  if (!gateway.value?.enabled) return 'info'
  return gateway.value.ready ? 'success' : 'warning'
})

async function loadOperations() {
  loading.value = true
  loadError.value = ''
  const [healthResult, gatewayResult, reliableResult] = await Promise.allSettled([
    api.get<HealthResponse>('/health'),
    api.get<GatewayStatus>('/api/v1/admin/ai-gateway/status'),
    api.get<ReliableStatus>('/api/v1/admin/reliable-execution/status'),
  ])

  if (healthResult.status === 'fulfilled') health.value = healthResult.value.data
  if (gatewayResult.status === 'fulfilled') gateway.value = gatewayResult.value.data
  if (reliableResult.status === 'fulfilled') reliable.value = reliableResult.value.data
  if ([healthResult, gatewayResult, reliableResult].some((result) => result.status === 'rejected')) {
    loadError.value = 'Refresh after confirming the backend URL and administrator permissions.'
  }
  lastChecked.value = new Date().toISOString()
  loading.value = false
  await loadTasks()
}

async function loadTasks() {
  tasksLoading.value = true
  try {
    const response = await api.get<{ tasks: TaskSummary[] }>('/api/v1/admin/tasks', {
      params: { status: taskStatus.value || undefined, limit: 50 },
    })
    tasks.value = response.data.tasks
  } finally {
    tasksLoading.value = false
  }
}

function outboxCount(status: string) {
  return reliable.value?.outbox_counts[status] || 0
}

function booleanLabel(value: boolean | null | undefined) {
  if (value === true) return 'Yes'
  if (value === false) return 'No'
  return 'Not checked'
}

function taskTagType(status: string | null) {
  if (status === 'completed') return 'success'
  if (status === 'failed') return 'danger'
  if (status === 'running') return 'primary'
  if (status === 'retry') return 'warning'
  return 'info'
}

function formatTime(value: string | null) {
  return value ? dayjs(value).format('YYYY-MM-DD HH:mm:ss') : '-'
}

onMounted(loadOperations)
</script>

<style scoped lang="scss">
.health-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 16px;
}

.health-panel {
  min-width: 0;
  padding: 20px;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: var(--console-radius);
  background: var(--el-bg-color);
}

.health-panel--attention {
  border-color: var(--el-color-warning-light-7);
}

.panel-heading,
.card-heading {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
}

.panel-heading > div,
.card-heading > div {
  display: grid;
  gap: 4px;
}

.panel-label,
.card-heading span {
  color: var(--el-text-color-secondary);
  font-size: 12px;
}

.detail-list {
  display: grid;
  gap: 9px;
  margin-top: 20px;
}

.detail-list div {
  display: flex;
  justify-content: space-between;
  gap: 20px;
}

.detail-list dt {
  color: var(--el-text-color-secondary);
}

.detail-list dd {
  margin: 0;
  font-variant-numeric: tabular-nums;
}

.panel-link {
  display: inline-block;
  margin-top: 16px;
  font-size: 13px;
}

.card-heading .el-select {
  width: 150px;
}

.count-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}

.count-grid div {
  display: grid;
  gap: 6px;
  padding: 14px;
  border-radius: calc(var(--console-radius) - 2px);
  background: var(--el-fill-color-light);
}

.count-grid span {
  color: var(--el-text-color-secondary);
  font-size: 12px;
  text-transform: capitalize;
}

.count-grid strong {
  font-size: 24px;
  font-variant-numeric: tabular-nums;
}

.issues {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.issues > span {
  width: 100%;
  color: var(--el-text-color-secondary);
  font-size: 12px;
}

@media (max-width: 1100px) {
  .health-grid {
    grid-template-columns: 1fr;
  }
}
</style>
