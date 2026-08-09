<template>
  <el-card class="stat-card" shadow="never">
    <div class="stat-content">
      <div class="stat-icon" :class="colorClass"><component :is="icon" /></div>
      <div class="stat-info">
        <span class="stat-label">{{ label }}</span>
        <strong class="stat-value">{{ formattedValue }}<small v-if="suffix">{{ suffix }}</small></strong>
        <div v-if="trend !== undefined" class="stat-trend">
          <span :class="trend >= 0 ? 'trend-up' : 'trend-down'">{{ trend >= 0 ? '+' : '' }}{{ trend }}%</span>
          <span>vs {{ period }}</span>
        </div>
      </div>
    </div>
  </el-card>
</template>

<script setup lang="ts">
import { computed } from 'vue'

const props = withDefaults(defineProps<{
  label: string
  value: number
  suffix?: string
  trend?: number
  period?: string
  color?: 'primary' | 'success' | 'warning' | 'danger' | 'info'
  icon?: string
}>(), { suffix: '', period: 'previous period', color: 'primary', icon: 'DataLine' })

const colorClass = computed(() => `stat-icon--${props.color}`)
const formattedValue = computed(() => Number.isInteger(props.value) ? props.value.toLocaleString() : props.value.toFixed(1))
</script>

<style scoped lang="scss">
.stat-card { border-color: var(--el-border-color-lighter); }
.stat-content { display: flex; align-items: center; gap: 14px; }
.stat-icon { display: grid; place-items: center; width: 42px; height: 42px; flex: 0 0 auto; border-radius: 8px; font-size: 20px; }
.stat-icon--primary { color: var(--el-color-primary); background: var(--el-color-primary-light-9); }
.stat-icon--success { color: var(--el-color-success); background: var(--el-color-success-light-9); }
.stat-icon--warning { color: var(--el-color-warning); background: var(--el-color-warning-light-9); }
.stat-icon--danger { color: var(--el-color-danger); background: var(--el-color-danger-light-9); }
.stat-icon--info { color: var(--el-color-info); background: var(--el-color-info-light-9); }
.stat-info { display: grid; min-width: 0; gap: 2px; }
.stat-label { color: var(--el-text-color-secondary); font-size: 12px; }
.stat-value { color: var(--el-text-color-primary); font-size: 28px; line-height: 1.1; font-variant-numeric: tabular-nums; }
.stat-value small { margin-left: 3px; font-size: 14px; }
.stat-trend { display: flex; gap: 5px; color: var(--el-text-color-placeholder); font-size: 11px; }
.trend-up { color: var(--el-color-success); }.trend-down { color: var(--el-color-danger); }
</style>
