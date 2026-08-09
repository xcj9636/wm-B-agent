<template>
  <el-card
    class="recent-activity"
    shadow="never"
  >
    <template #header>
      <div class="card-header">
        <div><strong>{{ $t('Recent activity') }}</strong><span>{{ $t('Latest auditable workspace events') }}</span></div>
        <el-button
          text
          type="primary"
          :loading="loading"
          @click="refresh"
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
      class="activity-content"
    >
      <el-timeline v-if="displayActivities.length">
        <el-timeline-item
          v-for="activity in displayActivities"
          :key="activity.id"
          :timestamp="formatTimestamp(activity.timestamp)"
          :type="activityTypes[activity.type] || 'info'"
        >
          <div class="activity-item">
            <el-icon class="activity-icon">
              <component :is="activityIcons[activity.type] || 'Notification'" />
            </el-icon>
            <div>
              <p>{{ activity.description }}</p>
              <div
                v-if="safeMetadata(activity.metadata).length"
                class="activity-meta"
              >
                <el-tag
                  v-for="entry in safeMetadata(activity.metadata)"
                  :key="entry"
                  size="small"
                  type="info"
                  effect="plain"
                >
                  {{ entry }}
                </el-tag>
              </div>
            </div>
          </div>
        </el-timeline-item>
      </el-timeline>
      <el-empty
        v-else-if="!loading"
        :description="$t('No recent activity')"
      />
    </div>
  </el-card>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import dayjs from 'dayjs'
import relativeTime from 'dayjs/plugin/relativeTime'
import 'dayjs/locale/zh-cn'
import { api } from '@/api'
import type { ActivityItem } from '@/types'
import { locale, translate } from '@/i18n'

dayjs.extend(relativeTime)

const props = withDefaults(defineProps<{ activities?: ActivityItem[]; autoLoad?: boolean }>(), {
  activities: () => [],
  autoLoad: true,
})
const loading = ref(false)
const errorMessage = ref('')
const internalActivities = ref<ActivityItem[]>([])
const displayActivities = computed(() => props.activities.length ? props.activities : internalActivities.value)

const activityTypes: Record<string, 'primary' | 'success' | 'warning' | 'danger' | 'info'> = {
  message_sent: 'primary', message_delivered: 'success', message_opened: 'warning', reply_received: 'primary',
  workflow_started: 'info', workflow_completed: 'success', workflow_failed: 'danger', customer_created: 'primary',
  customer_updated: 'info', takeover_requested: 'warning', system_alert: 'danger',
}
const activityIcons: Record<string, string> = {
  message_sent: 'Promotion', message_delivered: 'SuccessFilled', message_opened: 'View', reply_received: 'ChatDotRound',
  workflow_started: 'Operation', workflow_completed: 'CircleCheck', workflow_failed: 'CircleClose', customer_created: 'User',
  customer_updated: 'Edit', takeover_requested: 'Warning', system_alert: 'WarningFilled',
}

async function refresh() {
  loading.value = true
  errorMessage.value = ''
  try {
    const response = await api.get<ActivityItem[]>('/api/v1/stats/activities')
    internalActivities.value = response.data
  } catch {
    errorMessage.value = translate('Activity events are temporarily unavailable.')
  } finally {
    loading.value = false
  }
}

function safeMetadata(metadata?: Record<string, unknown>) {
  if (!metadata) return []
  return Object.entries(metadata)
    .filter(([, value]) => ['string', 'number', 'boolean'].includes(typeof value))
    .slice(0, 4)
    .map(([key, value]) => `${key}: ${String(value).slice(0, 80)}`)
}

function formatTimestamp(timestamp: string) {
  return dayjs(timestamp).locale(locale.value === 'zh-CN' ? 'zh-cn' : 'en').fromNow()
}

watch(() => props.activities, (value) => { if (value.length) internalActivities.value = value }, { immediate: true })
onMounted(() => { if (props.autoLoad && !props.activities.length) void refresh() })
</script>

<style scoped lang="scss">
.card-header { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.card-header > div { display: grid; gap: 3px; }.card-header span { color: var(--el-text-color-secondary); font-size: 12px; }
.activity-content { min-height: 280px; padding-top: 4px; }
.activity-item { display: flex; gap: 10px; }.activity-item p { margin: 0; line-height: 1.5; }
.activity-icon { margin-top: 2px; color: var(--el-text-color-secondary); }
.activity-meta { display: flex; flex-wrap: wrap; gap: 4px; margin-top: 7px; }
</style>
