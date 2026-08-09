<template>
  <el-card
    class="conversion-funnel"
    shadow="never"
  >
    <template #header>
      <div class="card-header">
        <div><strong>{{ $t('Conversion funnel') }}</strong><span>{{ $t('{count}-day customer journey', { count: days }) }}</span></div>
        <el-button
          text
          type="primary"
          :loading="loading"
          @click="fetchFunnelData"
        >
          {{ $t('Refresh') }}
        </el-button>
      </div>
    </template>
    <el-alert
      v-if="errorMessage"
      :title="errorMessage"
      type="warning"
      :closable="false"
      show-icon
    />
    <div
      v-loading="loading"
      class="funnel-content"
    >
      <div
        v-if="stages.length"
        class="funnel-list"
      >
        <div
          v-for="(stage, index) in stages"
          :key="stage.name"
          class="funnel-row"
        >
          <div class="stage-heading">
            <span>{{ stage.name }}</span>
            <strong>{{ stage.value.toLocaleString() }}</strong>
          </div>
          <div class="stage-track">
            <div
              class="stage-fill"
              :style="{ width: stageWidth(stage.value) }"
            />
          </div>
          <span class="stage-rate">{{ index === 0 ? 'Baseline' : `${rateFor(stage.name).toFixed(1)}% from previous stage` }}</span>
        </div>
      </div>
      <el-empty
        v-else-if="!loading"
        :description="$t('No funnel data for this period')"
      />
    </div>
  </el-card>
</template>

<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
import { api } from '@/api'
import type { FunnelResponse, FunnelStage } from '@/types'

const props = withDefaults(defineProps<{ days?: number }>(), { days: 30 })
const loading = ref(false)
const errorMessage = ref('')
const stages = ref<FunnelStage[]>([])
const rates = ref<Record<string, number>>({})

async function fetchFunnelData() {
  loading.value = true
  errorMessage.value = ''
  try {
    const response = await api.get<FunnelResponse>('/api/v1/stats/conversion-funnel', { params: { days: props.days } })
    stages.value = response.data.stages
    rates.value = Object.fromEntries(response.data.conversion_rates.map((item) => [item.stage, item.rate]))
  } catch {
    stages.value = []
    rates.value = {}
    errorMessage.value = 'Conversion funnel data is temporarily unavailable.'
  } finally {
    loading.value = false
  }
}

function stageWidth(value: number) {
  const baseline = Math.max(stages.value[0]?.value || 0, 1)
  return `${Math.max(value > 0 ? 4 : 0, Math.min(100, (value / baseline) * 100))}%`
}
function rateFor(stage: string) { return rates.value[stage] || 0 }

watch(() => props.days, () => { void fetchFunnelData() })
onMounted(() => { void fetchFunnelData() })
</script>

<style scoped lang="scss">
.card-header { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.card-header > div { display: grid; gap: 3px; }.card-header span { color: var(--el-text-color-secondary); font-size: 12px; }
.funnel-content { min-height: 290px; }.funnel-list { display: grid; gap: 18px; }
.stage-heading { display: flex; justify-content: space-between; gap: 12px; font-size: 13px; }
.stage-track { height: 9px; margin-top: 6px; overflow: hidden; border-radius: 3px; background: var(--el-fill-color); }
.stage-fill { height: 100%; border-radius: inherit; background: var(--el-color-primary); }
.stage-rate { display: block; margin-top: 4px; color: var(--el-text-color-secondary); font-size: 11px; }
</style>
