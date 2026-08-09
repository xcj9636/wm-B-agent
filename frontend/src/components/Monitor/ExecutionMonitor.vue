<template>
  <el-card
    class="execution-monitor"
    shadow="never"
  >
    <template #header>
      <div class="monitor-header">
        <div>
          <strong>Execution monitor</strong>
          <span>{{ liveExecution ? `Execution ${liveExecution.id}` : 'Waiting for an execution' }}</span>
        </div>
        <div class="header-actions">
          <el-tag
            v-if="liveExecution"
            :type="statusType"
            effect="plain"
          >
            {{ liveExecution.status }}
          </el-tag>
          <el-button
            :disabled="!executionId"
            :loading="loading"
            @click="() => refresh()"
          >
            <el-icon><Refresh /></el-icon>
          </el-button>
        </div>
      </div>
    </template>

    <el-alert
      v-if="errorMessage"
      :title="errorMessage"
      type="error"
      :closable="false"
      show-icon
    />
    <div
      v-loading="loading"
      class="monitor-content"
    >
      <template v-if="liveExecution">
        <div class="progress-block">
          <div><span>Progress</span><strong>{{ progress }}%</strong></div>
          <el-progress
            :percentage="progress"
            :status="progressStatus"
          />
        </div>

        <el-descriptions
          :column="2"
          border
        >
          <el-descriptions-item label="Workflow ID">
            {{ liveExecution.workflow_id }}
          </el-descriptions-item>
          <el-descriptions-item label="Current step">
            {{ liveExecution.current_step || 'None' }}
          </el-descriptions-item>
          <el-descriptions-item label="Started">
            {{ formatTime(liveExecution.started_at) }}
          </el-descriptions-item>
          <el-descriptions-item label="Finished">
            {{ formatTime(liveExecution.finished_at) }}
          </el-descriptions-item>
          <el-descriptions-item label="Completed steps">
            {{ liveExecution.completed_steps.length }}
          </el-descriptions-item>
          <el-descriptions-item label="Failed steps">
            {{ liveExecution.failed_steps.length }}
          </el-descriptions-item>
        </el-descriptions>

        <el-alert
          v-if="liveExecution.error_msg"
          class="execution-error"
          :title="liveExecution.error_msg"
          type="error"
          :closable="false"
          show-icon
        />

        <div class="step-columns">
          <section>
            <h4>Completed</h4><el-tag
              v-for="step in liveExecution.completed_steps"
              :key="step"
              type="success"
              effect="plain"
            >
              {{ step }}
            </el-tag><span v-if="!liveExecution.completed_steps.length">None</span>
          </section>
          <section>
            <h4>Failed</h4><el-tag
              v-for="step in liveExecution.failed_steps"
              :key="step"
              type="danger"
              effect="plain"
            >
              {{ step }}
            </el-tag><span v-if="!liveExecution.failed_steps.length">None</span>
          </section>
        </div>

        <el-collapse v-if="metricEntries.length">
          <el-collapse-item
            title="Execution metrics"
            name="metrics"
          >
            <el-descriptions
              :column="1"
              border
            >
              <el-descriptions-item
                v-for="[key, value] in metricEntries"
                :key="key"
                :label="key"
              >
                {{ formatMetric(value) }}
              </el-descriptions-item>
            </el-descriptions>
          </el-collapse-item>
        </el-collapse>

        <div class="execution-actions">
          <el-button
            v-if="liveExecution.status === 'running'"
            type="warning"
            :loading="actionLoading"
            @click="pauseExecution"
          >
            Pause
          </el-button>
          <el-button
            v-if="liveExecution.status === 'paused'"
            type="success"
            :loading="actionLoading"
            @click="resumeExecution"
          >
            Resume
          </el-button>
          <el-button
            v-if="isActive"
            type="danger"
            plain
            :loading="actionLoading"
            @click="cancelExecution"
          >
            Cancel
          </el-button>
          <el-button @click="emit('view-details', liveExecution)">
            View details
          </el-button>
        </div>
      </template>
      <el-empty
        v-else-if="!loading"
        description="Start a workflow to monitor its execution"
      />
    </div>
  </el-card>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import dayjs from 'dayjs'
import { ElMessage, ElMessageBox } from 'element-plus'
import { workflowApi } from '@/api/workflow'
import type { Execution } from '@/types'

const props = defineProps<{ execution?: Partial<Execution> | null }>()
const emit = defineEmits<{
  pause: [execution: Execution]
  resume: [execution: Execution]
  cancel: [execution: Execution]
  'view-details': [execution: Execution]
}>()

const liveExecution = ref<Execution | null>(null)
const loading = ref(false)
const actionLoading = ref(false)
const errorMessage = ref('')
let pollTimer: ReturnType<typeof setInterval> | undefined

const executionId = computed(() => props.execution?.id || liveExecution.value?.id || '')
const isActive = computed(() => ['pending', 'running', 'paused'].includes(liveExecution.value?.status || ''))
const metricEntries = computed(() => Object.entries(liveExecution.value?.metrics || {}))
const progress = computed(() => {
  const value = Number(liveExecution.value?.metrics?.progress)
  if (Number.isFinite(value)) return Math.max(0, Math.min(100, Math.round(value)))
  if (liveExecution.value?.status === 'completed') return 100
  return 0
})
const statusType = computed(() => ({ completed: 'success', failed: 'danger', running: 'primary', paused: 'warning', cancelled: 'info', pending: 'info' } as const)[liveExecution.value?.status || 'pending'])
const progressStatus = computed(() => liveExecution.value?.status === 'completed' ? 'success' : ['failed', 'cancelled'].includes(liveExecution.value?.status || '') ? 'exception' : undefined)

async function refresh(silent = false) {
  if (!executionId.value) return
  if (!silent) loading.value = true
  errorMessage.value = ''
  try {
    liveExecution.value = await workflowApi.getExecution(executionId.value)
    updatePolling()
  } catch {
    errorMessage.value = 'Execution status could not be loaded.'
    stopPolling()
  } finally {
    loading.value = false
  }
}

async function pauseExecution() {
  if (!liveExecution.value) return
  actionLoading.value = true
  try { await workflowApi.pauseExecution(liveExecution.value.id); await refresh(true); emit('pause', liveExecution.value); ElMessage.success('Execution paused') }
  finally { actionLoading.value = false }
}
async function resumeExecution() {
  if (!liveExecution.value) return
  actionLoading.value = true
  try { await workflowApi.resumeExecution(liveExecution.value.id); await refresh(true); emit('resume', liveExecution.value); ElMessage.success('Execution resumed') }
  finally { actionLoading.value = false }
}
async function cancelExecution() {
  if (!liveExecution.value) return
  try { await ElMessageBox.confirm('Cancel this workflow execution?', 'Confirm cancellation', { type: 'warning' }) }
  catch { return }
  actionLoading.value = true
  try { await workflowApi.cancelExecution(liveExecution.value.id); await refresh(true); emit('cancel', liveExecution.value); ElMessage.success('Execution cancelled') }
  finally { actionLoading.value = false }
}

function updatePolling() { if (isActive.value && !pollTimer) pollTimer = setInterval(() => void refresh(true), 2500); else if (!isActive.value) stopPolling() }
function stopPolling() { if (pollTimer) clearInterval(pollTimer); pollTimer = undefined }
function formatTime(value?: string) { return value ? dayjs(value).format('YYYY-MM-DD HH:mm:ss') : 'Not available' }
function formatMetric(value: unknown) { return typeof value === 'object' ? JSON.stringify(value) : String(value) }

watch(() => props.execution?.id, (id) => { stopPolling(); liveExecution.value = null; if (id) void refresh() }, { immediate: true })
onBeforeUnmount(stopPolling)
</script>

<style scoped lang="scss">
.monitor-header, .header-actions, .progress-block > div, .execution-actions { display: flex; align-items: center; justify-content: space-between; gap: 10px; }
.monitor-header > div:first-child { display: grid; gap: 3px; }.monitor-header span { color: var(--el-text-color-secondary); font-size: 12px; }
.monitor-content { min-height: 260px; }.progress-block { margin-bottom: 18px; }.progress-block > div { margin-bottom: 7px; }
.execution-error { margin: 16px 0; }.step-columns { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16px; margin: 18px 0; }
.step-columns section { min-height: 78px; padding: 12px; border: 1px solid var(--el-border-color-lighter); border-radius: 8px; }.step-columns h4 { margin: 0 0 9px; }
.step-columns .el-tag { margin: 0 5px 5px 0; }.step-columns span { color: var(--el-text-color-secondary); font-size: 12px; }
.execution-actions { justify-content: flex-end; flex-wrap: wrap; margin-top: 18px; }
@media (max-width: 640px) { .step-columns { grid-template-columns: 1fr; } }
</style>
