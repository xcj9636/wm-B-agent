<template>
  <el-card
    class="alert-panel"
    shadow="never"
  >
    <template #header>
      <div class="panel-header">
        <div class="panel-title">
          <el-icon><Bell /></el-icon>
          <div><strong>{{ $t('Operational alerts') }}</strong><span>{{ $t('Derived from current backend health') }}</span></div>
        </div>
        <el-button
          :loading="loading"
          @click="fetchAlerts"
        >
          <el-icon><Refresh /></el-icon>Refresh
        </el-button>
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
      class="alerts-list"
    >
      <article
        v-for="alert in alerts"
        :key="alert.id"
        class="alert-item"
        :class="`alert-${alert.severity}`"
      >
        <el-icon class="alert-icon">
          <component :is="alert.severity === 'critical' ? 'CircleCloseFilled' : 'WarningFilled'" />
        </el-icon>
        <div class="alert-copy">
          <strong>{{ alert.title }}</strong>
          <p>{{ alert.message }}</p>
          <span>{{ formatTime(alert.createdAt) }}</span>
        </div>
        <div class="alert-action">
          <el-tag
            :type="alert.severity === 'critical' ? 'danger' : 'warning'"
            effect="plain"
          >
            {{ alert.severity }}
          </el-tag>
          <el-button
            v-if="alert.action === 'dead-letters'"
            text
            type="primary"
            @click="router.push('/operations/dead-letters')"
          >
            Review
          </el-button>
        </div>
      </article>
      <el-empty
        v-if="!loading && !errorMessage && alerts.length === 0"
        :description="$t('No operational alerts')"
      />
    </div>
  </el-card>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import dayjs from 'dayjs'
import relativeTime from 'dayjs/plugin/relativeTime'
import api from '@/api'

dayjs.extend(relativeTime)

interface GatewayStatus {
  enabled: boolean
  ready: boolean
  reachable: boolean | null
  issues: string[]
}
interface ReliableStatus {
  outbox_counts: Record<string, number>
  llm_invocation_counts: Record<string, number>
  expired_outbox_leases: number
  checked_at: string
}
interface OperationalAlert {
  id: string
  title: string
  message: string
  severity: 'critical' | 'warning'
  createdAt: string
  action?: 'dead-letters'
}

const router = useRouter()
const loading = ref(false)
const errorMessage = ref('')
const alerts = ref<OperationalAlert[]>([])

async function fetchAlerts() {
  loading.value = true
  errorMessage.value = ''
  try {
    const [gatewayResponse, reliableResponse] = await Promise.all([
      api.get<GatewayStatus>('/api/v1/admin/ai-gateway/status'),
      api.get<ReliableStatus>('/api/v1/admin/reliable-execution/status'),
    ])
    const gateway = gatewayResponse.data
    const reliable = reliableResponse.data
    const observedAt = reliable.checked_at || new Date().toISOString()
    const next: OperationalAlert[] = []

    if (gateway.enabled && (!gateway.ready || gateway.reachable === false)) {
      next.push({ id: 'gateway-readiness', title: 'AI gateway is not ready', message: gateway.issues.join('; ') || 'Review provider and model alias configuration.', severity: 'critical', createdAt: observedAt })
    }
    gateway.issues.forEach((issue, index) => {
      if (next.some((item) => item.message.includes(issue))) return
      next.push({ id: `gateway-issue-${index}`, title: 'AI routing warning', message: issue, severity: 'warning', createdAt: observedAt })
    })

    const deadLetters = reliable.outbox_counts.dead_letter || 0
    if (deadLetters > 0) {
      next.push({ id: 'dead-letters', title: 'Dead-letter events require review', message: `${deadLetters} durable delivery events need an approved resolution.`, severity: 'critical', createdAt: observedAt, action: 'dead-letters' })
    }
    if (reliable.expired_outbox_leases > 0) {
      next.push({ id: 'expired-leases', title: 'Expired delivery leases detected', message: `${reliable.expired_outbox_leases} outbox leases have expired and may need recovery.`, severity: 'warning', createdAt: observedAt })
    }
    const failedInvocations = reliable.llm_invocation_counts.failed || 0
    if (failedInvocations > 0) {
      next.push({ id: 'llm-failures', title: 'LLM invocations failed', message: `${failedInvocations} recorded invocations are in a failed state.`, severity: 'warning', createdAt: observedAt })
    }
    alerts.value = next
  } catch {
    alerts.value = []
    errorMessage.value = 'Operational alerts could not be derived from the backend status endpoints.'
  } finally {
    loading.value = false
  }
}

function formatTime(value: string) { return dayjs(value).fromNow() }
onMounted(() => { void fetchAlerts() })
</script>

<style scoped lang="scss">
.panel-header, .panel-title, .alert-item, .alert-action { display: flex; align-items: center; gap: 10px; }
.panel-header { justify-content: space-between; }.panel-title > div { display: grid; gap: 2px; }.panel-title span, .alert-copy span { color: var(--el-text-color-secondary); font-size: 12px; }
.alerts-list { display: grid; min-height: 140px; gap: 8px; }.alert-item { align-items: flex-start; padding: 12px; border: 1px solid var(--el-border-color-lighter); border-radius: 8px; }
.alert-critical { border-color: var(--el-color-danger-light-7); }.alert-warning { border-color: var(--el-color-warning-light-7); }
.alert-icon { margin-top: 3px; color: var(--el-color-warning); }.alert-critical .alert-icon { color: var(--el-color-danger); }
.alert-copy { display: grid; flex: 1; gap: 4px; }.alert-copy p { margin: 0; color: var(--el-text-color-regular); line-height: 1.45; }
.alert-action { align-items: flex-end; flex-direction: column; }
@media (max-width: 640px) { .alert-item { flex-wrap: wrap; }.alert-action { width: 100%; align-items: center; flex-direction: row; justify-content: flex-end; } }
</style>
