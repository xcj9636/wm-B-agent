<template>
  <section class="dead-letters" aria-labelledby="dead-letters-title">
    <header class="page-header">
      <div>
        <p class="eyebrow">Reliable execution</p>
        <h1 id="dead-letters-title">Dead-letter operations</h1>
        <p class="page-description">
          Review delivery failures without exposing recipient or message content.
        </p>
      </div>
      <el-button
        aria-label="Refresh dead letters"
        :loading="loading"
        @click="loadDeadLetters"
      >
        <el-icon><Refresh /></el-icon>
        Refresh
      </el-button>
    </header>

    <el-alert
      class="approval-notice"
      type="warning"
      :closable="false"
      show-icon
      title="Two different administrators must approve the same evidence-backed resolution."
    />

    <el-card shadow="never">
      <div class="toolbar" aria-label="Dead-letter filters">
        <el-select
          v-model="channel"
          aria-label="Filter by channel"
          placeholder="All channels"
          clearable
          @change="loadDeadLetters"
        >
          <el-option label="Email" value="email" />
          <el-option label="WhatsApp" value="whatsapp" />
        </el-select>
        <span class="result-count" aria-live="polite">
          {{ deadLetters.length }} unresolved event{{ deadLetters.length === 1 ? '' : 's' }}
        </span>
      </div>

      <el-table
        v-loading="loading"
        :data="deadLetters"
        row-key="id"
        stripe
        empty-text="No dead-letter events"
      >
        <el-table-column label="Event" min-width="220">
          <template #default="{ row }">
            <div class="event-identity">
              <code>{{ row.id }}</code>
              <span>{{ row.aggregate_type }} · {{ row.event_type }}</span>
            </div>
          </template>
        </el-table-column>
        <el-table-column prop="channel" label="Channel" width="120">
          <template #default="{ row }">
            <el-tag effect="plain">{{ row.channel }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="Attempts" width="110">
          <template #default="{ row }">
            {{ row.attempt_count }} / {{ row.max_attempts }}
          </template>
        </el-table-column>
        <el-table-column prop="error_code" label="Error code" min-width="210">
          <template #default="{ row }">
            <code>{{ row.error_code }}</code>
          </template>
        </el-table-column>
        <el-table-column label="Last updated" width="180">
          <template #default="{ row }">
            {{ formatTime(row.updated_at) }}
          </template>
        </el-table-column>
        <el-table-column label="Action" width="120" fixed="right">
          <template #default="{ row }">
            <el-button type="primary" link @click="openResolution(row)">
              Resolve
            </el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <el-dialog
      v-model="dialogOpen"
      title="Approve dead-letter resolution"
      width="min(560px, 92vw)"
      :close-on-click-modal="false"
      @closed="resetResolution"
    >
      <div v-if="selectedEvent" class="resolution-context">
        <span>Event</span>
        <code>{{ selectedEvent.id }}</code>
      </div>

      <el-form
        ref="formRef"
        :model="form"
        :rules="rules"
        label-position="top"
        @submit.prevent="submitResolution"
      >
        <el-form-item label="Resolution" prop="action">
          <el-radio-group v-model="form.action">
            <el-radio-button value="confirmed_not_sent">
              Confirmed not sent
            </el-radio-button>
            <el-radio-button value="confirmed_sent">
              Confirmed sent
            </el-radio-button>
          </el-radio-group>
        </el-form-item>

        <el-form-item label="Evidence reference" prop="evidence_reference">
          <el-input
            v-model="form.evidence_reference"
            maxlength="128"
            placeholder="provider-audit/INC-1234"
            autocomplete="off"
          />
          <p class="field-help">
            Enter a ticket or provider-audit reference only. Do not paste secrets or content.
          </p>
        </el-form-item>

        <el-form-item
          v-if="form.action === 'confirmed_sent'"
          label="Provider message ID"
          prop="external_message_id"
        >
          <el-input
            v-model="form.external_message_id"
            maxlength="255"
            placeholder="provider-message-001"
            autocomplete="off"
          />
        </el-form-item>

        <el-checkbox v-model="acknowledged" class="acknowledgement">
          I verified the provider evidence and understand this approval is audited.
        </el-checkbox>
      </el-form>

      <template #footer>
        <el-button @click="dialogOpen = false">Cancel</el-button>
        <el-button
          type="danger"
          :loading="submitting"
          :disabled="!acknowledged"
          @click="submitResolution"
        >
          Submit approval
        </el-button>
      </template>
    </el-dialog>
  </section>
</template>

<script setup lang="ts">
import { reactive, ref } from 'vue'
import type { FormInstance, FormRules } from 'element-plus'
import { ElMessage } from 'element-plus'
import dayjs from 'dayjs'
import {
  reliableExecutionApi,
  type DeadLetterResolutionCommand,
  type DeadLetterSummary,
} from '@/api/reliableExecution'

const EVIDENCE_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._:/-]{2,127}$/

const loading = ref(false)
const submitting = ref(false)
const deadLetters = ref<DeadLetterSummary[]>([])
const channel = ref('')
const dialogOpen = ref(false)
const acknowledged = ref(false)
const selectedEvent = ref<DeadLetterSummary | null>(null)
const formRef = ref<FormInstance>()
const form = reactive<DeadLetterResolutionCommand>({
  action: 'confirmed_not_sent',
  evidence_reference: '',
  external_message_id: undefined,
})

const referenceValidator = (_rule: unknown, value: string, callback: (error?: Error) => void) => {
  if (!EVIDENCE_PATTERN.test(value || '')) {
    callback(new Error('Use a 3–128 character audit or ticket reference.'))
    return
  }
  callback()
}

const providerIdValidator = (_rule: unknown, value: string, callback: (error?: Error) => void) => {
  if (form.action === 'confirmed_sent' && !EVIDENCE_PATTERN.test(value || '')) {
    callback(new Error('A valid provider message ID is required.'))
    return
  }
  callback()
}

const rules: FormRules<DeadLetterResolutionCommand> = {
  action: [{ required: true, message: 'Select a resolution.', trigger: 'change' }],
  evidence_reference: [{ validator: referenceValidator, trigger: 'blur' }],
  external_message_id: [{ validator: providerIdValidator, trigger: 'blur' }],
}

async function loadDeadLetters() {
  loading.value = true
  try {
    deadLetters.value = await reliableExecutionApi.listDeadLetters({
      channel: channel.value || undefined,
      limit: 100,
    })
  } finally {
    loading.value = false
  }
}

function openResolution(event: DeadLetterSummary) {
  selectedEvent.value = event
  dialogOpen.value = true
}

async function submitResolution() {
  if (!selectedEvent.value || !acknowledged.value || submitting.value) return
  const valid = await formRef.value?.validate().catch(() => false)
  if (!valid) return

  submitting.value = true
  try {
    const command: DeadLetterResolutionCommand = {
      action: form.action,
      evidence_reference: form.evidence_reference,
      ...(form.action === 'confirmed_sent'
        ? { external_message_id: form.external_message_id }
        : {}),
    }
    const result = await reliableExecutionApi.approveResolution(selectedEvent.value.id, command)
    if (result.status === 'pending') {
      ElMessage.warning('Approval recorded. Waiting for a different administrator.')
    } else {
      ElMessage.success('Resolution executed and the durable state was reconciled.')
    }
    dialogOpen.value = false
    await loadDeadLetters()
  } finally {
    submitting.value = false
  }
}

function resetResolution() {
  selectedEvent.value = null
  acknowledged.value = false
  form.action = 'confirmed_not_sent'
  form.evidence_reference = ''
  form.external_message_id = undefined
  formRef.value?.clearValidate()
}

function formatTime(value: string) {
  return dayjs(value).format('YYYY-MM-DD HH:mm:ss')
}

loadDeadLetters()
</script>

<style lang="scss" scoped>
.dead-letters {
  display: grid;
  gap: 20px;
}

.page-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 24px;
}

.eyebrow {
  margin: 0 0 4px;
  color: var(--el-color-primary);
  font-size: 12px;
  font-weight: 700;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}

h1 {
  margin: 0;
  font-size: 28px;
}

.page-description {
  margin: 8px 0 0;
  color: var(--el-text-color-secondary);
}

.approval-notice {
  border: 1px solid var(--el-color-warning-light-5);
}

.toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 16px;
}

.toolbar .el-select {
  width: 190px;
}

.result-count,
.event-identity span,
.field-help,
.resolution-context span {
  color: var(--el-text-color-secondary);
  font-size: 13px;
}

.event-identity {
  display: grid;
  gap: 5px;
}

code {
  font-size: 12px;
  overflow-wrap: anywhere;
}

.resolution-context {
  display: grid;
  gap: 5px;
  margin-bottom: 20px;
  padding: 12px;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 6px;
  background: var(--el-fill-color-light);
}

.field-help {
  margin: 6px 0 0;
  line-height: 1.45;
}

.acknowledgement {
  align-items: flex-start;
  height: auto;
  white-space: normal;
}

@media (max-width: 720px) {
  .page-header,
  .toolbar {
    align-items: stretch;
    flex-direction: column;
  }

  .toolbar .el-select {
    width: 100%;
  }
}
</style>
