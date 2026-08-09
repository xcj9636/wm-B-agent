<template>
  <div class="page-stack">
    <header class="page-heading">
      <div>
        <p class="page-kicker">
          {{ $t('Performance intelligence') }}
        </p>
        <h1>{{ $t('Analytics') }}</h1>
        <p>{{ $t('Compare outreach, conversion and customer distribution across a selected window.') }}</p>
      </div>
      <div class="heading-actions">
        <el-select
          v-model="days"
          style="width: 140px"
          @change="loadAnalytics"
        >
          <el-option
            :label="$t('Last 7 days')"
            :value="7"
          />
          <el-option
            :label="$t('Last 30 days')"
            :value="30"
          />
          <el-option
            :label="$t('Last 90 days')"
            :value="90"
          />
        </el-select>
        <el-button
          :loading="loading"
          @click="loadAnalytics"
        >
          <el-icon><Refresh /></el-icon>{{ $t('Refresh') }}
        </el-button>
      </div>
    </header>

    <el-alert
      v-if="errorMessage"
      :title="errorMessage"
      type="warning"
      :closable="false"
      show-icon
    />
    <el-row
      v-loading="loading && !stats"
      :gutter="16"
    >
      <el-col
        v-for="metric in metrics"
        :key="metric.label"
        :xs="24"
        :sm="12"
        :xl="6"
      >
        <StatCard v-bind="metric" />
      </el-col>
    </el-row>

    <el-row :gutter="16">
      <el-col
        :xs="24"
        :xl="16"
      >
        <el-card
          shadow="never"
          class="trend-card"
        >
          <template #header>
            <div class="card-header">
              <div><strong>{{ $t('Daily trends') }}</strong><span>{{ $t('Backend observations only') }}</span></div><el-tag effect="plain">
                {{ $t('{count} points', { count: trends.length }) }}
              </el-tag>
            </div>
          </template>
          <el-table
            :data="trends"
            size="small"
            max-height="430"
          >
            <el-table-column
              prop="date"
              :label="$t('Date')"
              width="120"
            />
            <el-table-column
              prop="new_customers"
              :label="$t('Customers')"
            />
            <el-table-column :label="$t('Messages')">
              <template #default="{ row }">
                {{ row.emails_sent + row.whatsapp_sent }}
              </template>
            </el-table-column>
            <el-table-column
              prop="emails_replied"
              :label="$t('Replies')"
            />
            <el-table-column
              prop="conversions"
              :label="$t('Conversions')"
            />
          </el-table>
          <el-empty
            v-if="!loading && trends.length === 0"
            :description="$t('No daily observations for this period')"
          />
        </el-card>
      </el-col>
      <el-col
        :xs="24"
        :xl="8"
      >
        <ConversionFunnel :days="days" />
      </el-col>
    </el-row>

    <el-row :gutter="16">
      <el-col
        :xs="24"
        :lg="12"
      >
        <el-card shadow="never">
          <template #header>
            <div class="card-header">
              <div><strong>{{ $t('Customers by platform') }}</strong><span>{{ $t('New customer source') }}</span></div>
            </div>
          </template>
          <div
            v-if="platforms.length"
            class="rank-list"
          >
            <div
              v-for="item in platforms"
              :key="item.platform"
              class="rank-row"
            >
              <span>{{ item.platform || $t('Unknown') }}</span><strong>{{ item.count }}</strong>
              <el-progress
                :percentage="relativePercent(item.count, platforms)"
                :show-text="false"
              />
            </div>
          </div>
          <el-empty
            v-else-if="!loading"
            :description="$t('No platform distribution data')"
          />
        </el-card>
      </el-col>
      <el-col
        :xs="24"
        :lg="12"
      >
        <el-card shadow="never">
          <template #header>
            <div class="card-header">
              <div><strong>{{ $t('Customers by country') }}</strong><span>{{ $t('Top 20 locations') }}</span></div>
            </div>
          </template>
          <div
            v-if="countries.length"
            class="rank-list"
          >
            <div
              v-for="item in countries"
              :key="item.country"
              class="rank-row"
            >
              <span>{{ item.country }}</span><strong>{{ item.count }}</strong>
              <el-progress
                :percentage="relativePercent(item.count, countries)"
                :show-text="false"
              />
            </div>
          </div>
          <el-empty
            v-else-if="!loading"
            :description="$t('No country distribution data')"
          />
        </el-card>
      </el-col>
    </el-row>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { api } from '@/api'
import type { DashboardStats, MetricCard, TrendPoint, TrendsResponse } from '@/types'
import StatCard from '@/components/Dashboard/StatCard.vue'
import ConversionFunnel from '@/components/Dashboard/ConversionFunnel.vue'
import { translate } from '@/i18n'

interface PlatformCount { platform: string; count: number }
interface CountryCount { country: string; count: number }

const days = ref(30)
const loading = ref(false)
const errorMessage = ref('')
const stats = ref<DashboardStats | null>(null)
const trends = ref<TrendPoint[]>([])
const platforms = ref<PlatformCount[]>([])
const countries = ref<CountryCount[]>([])

const metrics = computed<MetricCard[]>(() => [
  { label: translate('New customers today'), value: stats.value?.today.new_customers || 0, icon: 'User', color: 'primary' },
  { label: translate('Messages today'), value: (stats.value?.today.emails_sent || 0) + (stats.value?.today.whatsapp_sent || 0), icon: 'Promotion', color: 'success' },
  { label: translate('Replies today'), value: stats.value?.today.emails_replied || 0, icon: 'ChatDotRound', color: 'warning' },
  { label: translate('Conversion rate'), value: stats.value?.conversion_rate || 0, suffix: '%', icon: 'TrendCharts', color: 'danger' },
])

async function loadAnalytics() {
  loading.value = true
  errorMessage.value = ''
  const results = await Promise.allSettled([
    api.get<DashboardStats>('/api/v1/stats/dashboard'),
    api.get<TrendsResponse>('/api/v1/stats/trends', { params: { days: days.value } }),
    api.get<{ platforms: PlatformCount[] }>('/api/v1/stats/by-platform', { params: { days: days.value } }),
    api.get<{ countries: CountryCount[] }>('/api/v1/stats/by-country', { params: { days: days.value } }),
  ])
  const [dashboardResult, trendResult, platformResult, countryResult] = results
  if (dashboardResult.status === 'fulfilled') stats.value = dashboardResult.value.data
  if (trendResult.status === 'fulfilled') trends.value = trendResult.value.data.stats
  if (platformResult.status === 'fulfilled') platforms.value = platformResult.value.data.platforms
  if (countryResult.status === 'fulfilled') countries.value = countryResult.value.data.countries
  if (results.some((result) => result.status === 'rejected')) errorMessage.value = translate('Some analytics sources are unavailable. Available results are still shown.')
  loading.value = false
}

function relativePercent<T extends { count: number }>(count: number, values: T[]) {
  return Math.round((count / Math.max(...values.map((item) => item.count), 1)) * 100)
}

onMounted(() => { void loadAnalytics() })
</script>

<style scoped lang="scss">
.heading-actions { display: flex; flex-wrap: wrap; gap: 8px; }.el-col { margin-bottom: 16px; }
.card-header { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.card-header > div { display: grid; gap: 3px; }.card-header span { color: var(--el-text-color-secondary); font-size: 12px; }
.rank-list { display: grid; gap: 14px; }.rank-row { display: grid; grid-template-columns: 1fr auto; gap: 5px 12px; font-size: 13px; }
.rank-row :deep(.el-progress) { grid-column: 1 / -1; }
@media (max-width: 640px) { .heading-actions { width: 100%; }.heading-actions > * { flex: 1; } }
</style>
