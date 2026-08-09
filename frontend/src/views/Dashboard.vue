<template>
  <div class="page-stack">
    <header class="page-heading">
      <div>
        <p class="page-kicker">Live workspace</p>
        <h1>Dashboard</h1>
        <p>Today’s customer, outreach and automation signals from the backend.</p>
      </div>
      <el-button :loading="loading" @click="loadDashboard">
        <el-icon><Refresh /></el-icon>
        Refresh
      </el-button>
    </header>

    <el-alert v-if="errorMessage" :title="errorMessage" type="error" :closable="false" show-icon>
      <template #default><el-button text type="primary" @click="loadDashboard">Try again</el-button></template>
    </el-alert>

    <el-row v-loading="loading && !stats" :gutter="16">
      <el-col v-for="metric in metrics" :key="metric.label" :xs="24" :sm="12" :xl="6">
        <StatCard v-bind="metric" />
      </el-col>
    </el-row>

    <el-row :gutter="16">
      <el-col :xs="24" :xl="16">
        <RecentActivity :activities="activities" :auto-load="false" />
      </el-col>
      <el-col :xs="24" :xl="8">
        <el-card class="leads-card">
          <template #header>
            <div class="card-header">
              <div><strong>High-intent leads</strong><span>Prioritized by latest customer state</span></div>
              <el-tag type="danger" effect="plain">{{ highIntentLeads.length }}</el-tag>
            </div>
          </template>
          <el-table v-loading="loading" :data="highIntentLeads" size="small">
            <el-table-column prop="name" label="Lead" min-width="120" />
            <el-table-column prop="platform" label="Channel" width="100" />
            <el-table-column label="Intent" width="100">
              <template #default="{ row }">
                <el-tag :type="row.intent === 'very_high' ? 'danger' : 'warning'" effect="plain" size="small">
                  {{ row.intent.replace('_', ' ') }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column width="64" align="right">
              <template #default="{ row }"><el-button text type="primary" @click="router.push(`/customers/${row.id}`)">View</el-button></template>
            </el-table-column>
          </el-table>
          <el-empty v-if="!loading && highIntentLeads.length === 0" description="No high-intent leads" />
        </el-card>
      </el-col>
    </el-row>

    <el-card v-if="stats" class="period-card">
      <template #header><strong>Period comparison</strong></template>
      <el-table :data="periodRows" size="small">
        <el-table-column prop="period" label="Period" width="100" />
        <el-table-column prop="customers" label="Customers" />
        <el-table-column prop="messages" label="Messages" />
        <el-table-column prop="replies" label="Replies" />
        <el-table-column prop="workflows" label="Workflows" />
        <el-table-column prop="failures" label="Failures" />
      </el-table>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '@/api'
import type { ActivityItem, DashboardStats, MetricCard } from '@/types'
import StatCard from '@/components/Dashboard/StatCard.vue'
import RecentActivity from '@/components/Dashboard/RecentActivity.vue'

interface HighIntentLead { id: number; name: string; intent: string; platform: string }

const router = useRouter()
const loading = ref(false)
const errorMessage = ref('')
const stats = ref<DashboardStats | null>(null)
const activities = ref<ActivityItem[]>([])
const highIntentLeads = ref<HighIntentLead[]>([])

const metrics = computed<MetricCard[]>(() => [
  { label: 'New customers', value: stats.value?.today.new_customers || 0, color: 'primary', icon: 'User' },
  { label: 'Messages sent', value: (stats.value?.today.emails_sent || 0) + (stats.value?.today.whatsapp_sent || 0), color: 'success', icon: 'Promotion' },
  { label: 'Active conversations', value: stats.value?.today.active_conversations || 0, color: 'warning', icon: 'ChatDotRound' },
  { label: 'Conversion rate', value: stats.value?.conversion_rate || 0, suffix: '%', color: 'danger', icon: 'TrendCharts' },
])

const periodRows = computed(() => {
  if (!stats.value) return []
  return ([['Today', stats.value.today], ['7 days', stats.value.week], ['30 days', stats.value.month]] as const).map(([period, data]) => ({
    period,
    customers: data.new_customers,
    messages: data.emails_sent + data.whatsapp_sent,
    replies: data.emails_replied,
    workflows: data.workflows_executed,
    failures: data.workflows_failed,
  }))
})

async function loadDashboard() {
  loading.value = true
  errorMessage.value = ''
  const [statsResult, activitiesResult, leadsResult] = await Promise.allSettled([
    api.get<DashboardStats>('/api/v1/stats/dashboard'),
    api.get<ActivityItem[]>('/api/v1/stats/activities'),
    api.get<HighIntentLead[]>('/api/v1/customers/high-intent'),
  ])
  if (statsResult.status === 'fulfilled') stats.value = statsResult.value.data
  if (activitiesResult.status === 'fulfilled') activities.value = activitiesResult.value.data
  if (leadsResult.status === 'fulfilled') highIntentLeads.value = leadsResult.value.data
  if ([statsResult, activitiesResult, leadsResult].some((result) => result.status === 'rejected')) {
    errorMessage.value = 'Some dashboard data could not be loaded. Available results are still shown.'
  }
  loading.value = false
}

onMounted(() => { void loadDashboard() })
</script>

<style scoped lang="scss">
.el-col { margin-bottom: 16px; }
.card-header { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.card-header > div { display: grid; gap: 3px; }
.card-header span { color: var(--el-text-color-secondary); font-size: 12px; }
.leads-card { height: 100%; }
</style>
