<template>
  <div class="page-stack connectors-page">
    <section class="page-hero native-panel">
      <div>
        <span class="eyebrow">{{ $t('Integration control plane') }}</span>
        <h1>{{ $t('Connectors') }}</h1>
        <p>{{ $t('Configure prospecting and delivery providers without restarting B-agent.') }}</p>
      </div>
      <el-button
        type="primary"
        @click="openCreate"
      >
        <el-icon><Plus /></el-icon>
        {{ $t('Add connector') }}
      </el-button>
    </section>

    <section class="security-note native-panel">
      <el-icon><Lock /></el-icon>
      <div>
        <strong>{{ $t('API key is write-only') }}</strong>
        <span>{{ $t('Secrets are stored by the backend and are never returned to this browser.') }}</span>
      </div>
    </section>

    <section
      class="connector-grid"
      :aria-busy="loading"
    >
      <article
        v-for="connector in connectors"
        :key="connector.id"
        class="connector-card native-panel"
      >
        <div class="connector-heading">
          <div class="provider-mark">
            H
          </div>
          <div>
            <span>{{ connector.provider }}</span>
            <h2>{{ connector.name }}</h2>
          </div>
          <el-tag
            :type="statusType(connector)"
            effect="light"
            round
          >
            {{ $t(statusLabel(connector)) }}
          </el-tag>
        </div>

        <dl class="connector-facts">
          <div>
            <dt>{{ $t('Runtime version') }}</dt>
            <dd>v{{ connector.version }}</dd>
          </div>
          <div>
            <dt>{{ $t('Credential') }}</dt>
            <dd>{{ $t(connector.secret_configured ? 'Configured' : 'Missing') }}</dd>
          </div>
          <div>
            <dt>{{ $t('Timeout seconds') }}</dt>
            <dd>{{ connector.config.timeout_seconds || 15 }}</dd>
          </div>
          <div>
            <dt>{{ $t('Last tested') }}</dt>
            <dd>{{ formatTime(connector.last_tested_at) }}</dd>
          </div>
        </dl>

        <div
          v-if="connector.last_error_code"
          class="connector-error"
        >
          {{ $t('Error code') }}: {{ connector.last_error_code }}
        </div>

        <div class="connector-actions">
          <el-button
            :loading="testingId === connector.id"
            @click="testConnector(connector)"
          >
            {{ $t('Test connection') }}
          </el-button>
          <el-button @click="openEdit(connector)">
            {{ $t('Edit') }}
          </el-button>
          <el-button
            :type="connector.enabled ? 'default' : 'primary'"
            :loading="toggleId === connector.id"
            :disabled="!connector.enabled && connector.last_status !== 'healthy'"
            @click="toggleConnector(connector)"
          >
            {{ $t(connector.enabled ? 'Disable' : 'Enable') }}
          </el-button>
        </div>
      </article>

      <el-empty
        v-if="!loading && connectors.length === 0"
        class="native-panel empty-state"
        :description="$t('No connectors configured')"
      >
        <el-button
          type="primary"
          @click="openCreate"
        >
          {{ $t('Configure Hunter') }}
        </el-button>
      </el-empty>
    </section>

    <el-dialog
      v-model="dialogOpen"
      :title="$t(editingId ? 'Edit connector' : 'Add connector')"
      width="min(520px, 92vw)"
      destroy-on-close
    >
      <el-form
        label-position="top"
        @submit.prevent="saveConnector"
      >
        <el-form-item :label="$t('Provider')">
          <el-select
            v-model="form.provider"
            :disabled="Boolean(editingId)"
          >
            <el-option
              v-for="item in catalog"
              :key="item.provider"
              :label="item.display_name"
              :value="item.provider"
            />
          </el-select>
        </el-form-item>
        <el-form-item :label="$t('Name')">
          <el-input
            v-model="form.name"
            maxlength="100"
          />
        </el-form-item>
        <el-form-item :label="$t('Hunter API key')">
          <el-input
            v-model="form.secret"
            type="password"
            show-password
            autocomplete="new-password"
            :placeholder="$t(editingId ? 'Leave empty to keep the current key' : 'Enter Hunter API key')"
          />
        </el-form-item>
        <el-form-item :label="$t('Timeout seconds')">
          <el-input-number
            v-model="form.timeout"
            :min="1"
            :max="60"
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogOpen = false">
          {{ $t('Cancel') }}
        </el-button>
        <el-button
          type="primary"
          :loading="saving"
          @click="saveConnector"
        >
          {{ $t('Save and apply') }}
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import {
  connectorsApi,
  type ConnectorCatalogItem,
  type ConnectorConfiguration,
} from '@/api/connectors'
import { translate } from '@/i18n'

const catalog = ref<ConnectorCatalogItem[]>([])
const connectors = ref<ConnectorConfiguration[]>([])
const loading = ref(true)
const saving = ref(false)
const testingId = ref('')
const toggleId = ref('')
const dialogOpen = ref(false)
const editingId = ref('')
const form = reactive({ provider: 'hunter', name: '', secret: '', timeout: 15 })

async function load() {
  loading.value = true
  try {
    const [available, configured] = await Promise.all([
      connectorsApi.catalog(),
      connectorsApi.list(),
    ])
    catalog.value = available
    connectors.value = configured
  } catch {
    ElMessage.error(translate('Connectors could not be loaded.'))
  } finally {
    loading.value = false
  }
}

function openCreate() {
  editingId.value = ''
  Object.assign(form, { provider: 'hunter', name: 'Hunter', secret: '', timeout: 15 })
  dialogOpen.value = true
}

function openEdit(connector: ConnectorConfiguration) {
  editingId.value = connector.id
  Object.assign(form, {
    provider: connector.provider,
    name: connector.name,
    secret: '',
    timeout: Number(connector.config.timeout_seconds || 15),
  })
  dialogOpen.value = true
}

async function saveConnector() {
  if (!form.name.trim() || (!editingId.value && !form.secret.trim())) {
    ElMessage.warning(translate('Name and API key are required.'))
    return
  }
  saving.value = true
  try {
    if (editingId.value) {
      await connectorsApi.update(editingId.value, {
        name: form.name.trim(),
        ...(form.secret.trim() ? { secret: form.secret.trim() } : {}),
        config: { timeout_seconds: form.timeout },
      })
    } else {
      await connectorsApi.create({
        provider: form.provider,
        name: form.name.trim(),
        secret: form.secret.trim(),
        config: { timeout_seconds: form.timeout },
      })
    }
    form.secret = ''
    dialogOpen.value = false
    ElMessage.success(translate('Connector configuration applied.'))
    await load()
  } catch {
    ElMessage.error(translate('Connector configuration could not be applied.'))
  } finally {
    saving.value = false
  }
}

async function testConnector(connector: ConnectorConfiguration) {
  testingId.value = connector.id
  try {
    const result = await connectorsApi.test(connector.id)
    ElMessage[result.ready ? 'success' : 'error'](
      translate(result.ready ? 'Connector is healthy.' : 'Connector test failed.'),
    )
    await load()
  } finally {
    testingId.value = ''
  }
}

async function toggleConnector(connector: ConnectorConfiguration) {
  toggleId.value = connector.id
  try {
    await connectorsApi.setEnabled(connector.id, !connector.enabled)
    ElMessage.success(translate(connector.enabled ? 'Connector disabled.' : 'Connector enabled.'))
    await load()
  } finally {
    toggleId.value = ''
  }
}

function statusLabel(connector: ConnectorConfiguration) {
  if (!connector.enabled) return 'Disabled'
  if (connector.last_status === 'healthy') return 'Connected'
  if (connector.last_status === 'failed') return 'Unavailable'
  return 'Not tested'
}

function statusType(connector: ConnectorConfiguration) {
  if (!connector.enabled) return 'info'
  if (connector.last_status === 'healthy') return 'success'
  if (connector.last_status === 'failed') return 'danger'
  return 'warning'
}

function formatTime(value?: string) {
  return value ? new Date(value).toLocaleString() : translate('Not tested')
}

onMounted(load)
</script>

<style scoped>
.connectors-page { max-width: 1240px; margin: 0 auto; }
.page-hero { display: flex; align-items: flex-end; justify-content: space-between; gap: 24px; padding: 28px; }
.page-hero h1 { margin: 5px 0 8px; font-size: clamp(28px, 4vw, 42px); letter-spacing: -.035em; }
.page-hero p { max-width: 680px; margin: 0; color: var(--text-secondary); }
.eyebrow { color: var(--accent-color); font-size: 12px; font-weight: 700; letter-spacing: .08em; text-transform: uppercase; }
.security-note { display: flex; gap: 12px; align-items: center; padding: 16px 20px; color: var(--text-secondary); }
.security-note .el-icon { color: var(--accent-color); font-size: 22px; }
.security-note div { display: grid; gap: 3px; }
.security-note strong { color: var(--text-primary); }
.connector-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 420px), 1fr)); gap: 16px; }
.connector-card { padding: 22px; }
.connector-heading { display: grid; grid-template-columns: 46px 1fr auto; align-items: center; gap: 12px; }
.provider-mark { display: grid; place-items: center; width: 46px; height: 46px; border-radius: 14px; background: #ff5a5f; color: white; font-size: 22px; font-weight: 750; box-shadow: 0 8px 24px rgb(255 90 95 / 24%); }
.connector-heading span { color: var(--text-secondary); font-size: 12px; text-transform: uppercase; }
.connector-heading h2 { margin: 2px 0 0; font-size: 20px; }
.connector-facts { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin: 22px 0; }
.connector-facts div { padding: 13px; border: 1px solid var(--border-color); border-radius: 14px; background: var(--surface-muted); }
.connector-facts dt { color: var(--text-secondary); font-size: 12px; }
.connector-facts dd { margin: 5px 0 0; font-weight: 650; }
.connector-error { margin-bottom: 16px; padding: 10px 12px; border-radius: 10px; background: rgb(255 59 48 / 10%); color: var(--danger-color, #ff3b30); font-size: 13px; }
.connector-actions { display: flex; flex-wrap: wrap; gap: 8px; }
.empty-state { grid-column: 1 / -1; padding: 32px; }
:deep(.el-select), :deep(.el-input-number) { width: 100%; }
@media (max-width: 640px) {
  .page-hero { align-items: stretch; flex-direction: column; }
  .connector-heading { grid-template-columns: 46px 1fr; }
  .connector-heading .el-tag { grid-column: 1 / -1; justify-self: start; }
  .connector-facts { grid-template-columns: 1fr; }
}
</style>
