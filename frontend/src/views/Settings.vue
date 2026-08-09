<template>
  <div class="page-stack settings-page">
    <header class="page-heading">
      <div>
        <p class="page-kicker">
          {{ $t('System configuration') }}
        </p>
        <h1>{{ $t('Settings') }}</h1>
        <p>{{ $t('Configure the browser-to-backend connection and inspect linked delivery accounts.') }}</p>
      </div>
      <el-button
        :loading="loadingAccounts"
        @click="loadAccounts"
      >
        <el-icon><Refresh /></el-icon>
        {{ $t('Refresh accounts') }}
      </el-button>
    </header>

    <el-row :gutter="16">
      <el-col
        :xs="24"
        :xl="15"
      >
        <el-card class="settings-card">
          <template #header>
            <div class="card-title">
              <el-icon><Connection /></el-icon>
              <div>
                <strong>{{ $t('Backend API') }}</strong>
                <span>{{ $t('Applied immediately in this browser') }}</span>
              </div>
              <el-tag
                :type="connectionTagType"
                effect="plain"
              >
                {{ connectionLabel }}
              </el-tag>
            </div>
          </template>

          <el-form
            label-position="top"
            @submit.prevent="saveBackendUrl"
          >
            <el-form-item :label="$t('Base URL')">
              <el-input
                v-model="backendUrl"
                clearable
                :placeholder="$t('Leave empty to use the Vite or reverse-proxy origin')"
              >
                <template #prepend>
                  HTTP(S)
                </template>
              </el-input>
              <p class="field-help">
                {{ $t('Example: http://localhost:8000. Empty uses the current origin and development proxy.') }}
              </p>
            </el-form-item>
            <div class="form-actions">
              <el-button
                type="primary"
                native-type="submit"
              >
                {{ $t('Save and apply') }}
              </el-button>
              <el-button
                :loading="testingConnection"
                @click="testBackendConnection"
              >
                {{ $t('Test connection') }}
              </el-button>
              <el-button @click="resetBackendUrl">
                {{ $t('Use proxy default') }}
              </el-button>
            </div>
          </el-form>

          <el-alert
            v-if="connectionMessage"
            class="connection-result"
            :title="connectionMessage"
            :type="connectionState === 'healthy' ? 'success' : 'error'"
            :closable="false"
            show-icon
          />

          <el-descriptions
            v-if="health"
            class="health-grid"
            :column="2"
            border
          >
            <el-descriptions-item :label="$t('Service')">
              {{ health.app || 'B-Agent API' }}
            </el-descriptions-item>
            <el-descriptions-item :label="$t('Version')">
              {{ health.version || $t('Not reported') }}
            </el-descriptions-item>
            <el-descriptions-item :label="$t('Status')">
              {{ health.status }}
            </el-descriptions-item>
            <el-descriptions-item :label="$t('Effective URL')">
              {{ effectiveUrl || $t('Same origin') }}
            </el-descriptions-item>
          </el-descriptions>
        </el-card>

        <el-card
          v-if="authStore.isAdmin"
          v-loading="loadingAiConfig"
          class="settings-card"
        >
          <template #header>
            <div class="card-title">
              <el-icon><MagicStick /></el-icon>
              <div>
                <strong>{{ $t('AI route configuration') }}</strong>
                <span>{{ $t('Hot-loaded by the backend for the next AI request') }}</span>
              </div>
              <el-tag
                :type="aiProbe?.ready ? 'success' : 'info'"
                effect="plain"
              >
                {{ aiConfigVersion }}
              </el-tag>
            </div>
          </template>

          <el-alert
            :title="$t('Provider credentials stay in backend-only storage and are never returned to this browser.')"
            type="info"
            :closable="false"
            show-icon
            class="ai-security-note"
          />

          <el-form
            label-position="top"
            @submit.prevent="saveAiConfig"
          >
            <el-row :gutter="12">
              <el-col
                :sm="8"
                :xs="24"
              >
                <el-form-item :label="$t('Routing backend')">
                  <el-select v-model="aiConfig.backend">
                    <el-option
                      label="OmniRoute"
                      value="omniroute"
                    />
                    <el-option
                      :label="$t('Direct provider')"
                      value="direct"
                    />
                  </el-select>
                </el-form-item>
              </el-col>
              <el-col
                :sm="16"
                :xs="24"
              >
                <el-form-item :label="$t('Gateway base URL')">
                  <el-input
                    v-model="aiConfig.base_url"
                    placeholder="http://omniroute:20128"
                  />
                </el-form-item>
              </el-col>
            </el-row>

            <el-form-item :label="$t('Approved providers')">
              <el-select
                v-model="aiConfig.allowed_providers"
                multiple
                filterable
                allow-create
                default-first-option
                :placeholder="$t('Add exact provider IDs returned by OmniRoute')"
              />
              <p class="field-help">
                {{ $t('An empty allowlist is rejected when OmniRoute is active.') }}
              </p>
            </el-form-item>

            <div class="alias-grid">
              <el-form-item
                v-for="item in aiUseCases"
                :key="item.key"
                :label="$t(item.label)"
              >
                <el-select
                  v-model="aiConfig.model_aliases[item.key]"
                  filterable
                  allow-create
                  default-first-option
                  :placeholder="$t('Fixed model or combo alias')"
                >
                  <el-option
                    v-for="model in discoveredModels"
                    :key="model"
                    :label="model"
                    :value="model"
                  />
                </el-select>
              </el-form-item>
            </div>

            <el-row :gutter="12">
              <el-col
                :sm="10"
                :xs="24"
              >
                <el-form-item :label="$t('Timeout seconds')">
                  <el-input-number
                    v-model="aiConfig.timeout_seconds"
                    :min="1"
                    :max="300"
                    controls-position="right"
                  />
                </el-form-item>
              </el-col>
              <el-col
                :sm="14"
                :xs="24"
              >
                <el-form-item :label="$t('Gateway key')">
                  <el-input
                    v-model="aiKey"
                    type="password"
                    show-password
                    autocomplete="new-password"
                    :placeholder="aiKeyConfigured ? $t('Configured, leave empty to keep') : $t('Enter a backend gateway key')"
                  />
                </el-form-item>
              </el-col>
            </el-row>

            <div class="form-actions">
              <el-button
                type="primary"
                native-type="submit"
                :loading="savingAiConfig"
              >
                {{ $t('Apply without restart') }}
              </el-button>
              <el-button
                :loading="testingAiConfig"
                @click="probeAiConfig"
              >
                {{ $t('Test AI route') }}
              </el-button>
              <el-button
                :loading="discoveringModels"
                @click="discoverAiModels"
              >
                {{ $t('Discover models') }}
              </el-button>
            </div>
          </el-form>

          <el-alert
            v-if="aiProbe"
            class="connection-result"
            :title="aiProbe.ready ? $t('AI route is ready.') : $t('AI route is not ready.')"
            :description="aiProbe.issues.join(', ') || undefined"
            :type="aiProbe.ready ? 'success' : 'warning'"
            :closable="false"
            show-icon
          />
        </el-card>

        <el-card class="settings-card">
          <template #header>
            <div class="card-title">
              <el-icon><Link /></el-icon>
              <div>
                <strong>{{ $t('Connected accounts') }}</strong>
                <span>{{ $t('Delivery identities exposed by the administration API') }}</span>
              </div>
              <el-tag effect="plain">
                {{ accounts.length }}
              </el-tag>
            </div>
          </template>

          <div class="mailbox-connect-grid">
            <article
              v-for="provider in mailboxProviders"
              :key="provider.provider"
              class="mailbox-connect-card"
            >
              <div
                class="mailbox-provider-mark"
                :class="provider.provider"
              >
                {{ provider.provider === 'gmail' ? 'G' : 'M' }}
              </div>
              <div class="mailbox-provider-copy">
                <strong>{{ provider.display_name }}</strong>
                <span>
                  {{ provider.configured
                    ? $t('OAuth is configured on the backend.')
                    : $t('Configure OAuth credentials on the backend first.') }}
                </span>
              </div>
              <el-button
                type="primary"
                plain
                :disabled="!provider.configured"
                :loading="connectingProvider === provider.provider"
                @click="startMailboxOAuth(provider.provider)"
              >
                {{ provider.provider === 'gmail' ? $t('Connect Gmail') : $t('Connect Microsoft') }}
              </el-button>
            </article>
          </div>
          <p class="mailbox-security-note">
            {{ $t('OAuth credentials stay in backend-only storage and are never sent to this browser.') }}
          </p>

          <el-alert
            v-if="accountsError"
            :title="accountsError"
            type="warning"
            :closable="false"
            show-icon
          />
          <el-table
            v-else
            v-loading="loadingAccounts"
            :data="accounts"
            stripe
          >
            <el-table-column
              prop="name"
              :label="$t('Account')"
              min-width="170"
            >
              <template #default="{ row }">
                <div class="account-name">
                  <strong>{{ row.name }}</strong>
                  <span>{{ row.email || row.phone_number || $t('No address') }}</span>
                </div>
              </template>
            </el-table-column>
            <el-table-column
              prop="account_type"
              :label="$t('Type')"
              width="170"
            />
            <el-table-column
              :label="$t('State')"
              width="150"
            >
              <template #default="{ row }">
                <el-tag
                  :type="row.connection_status === 'connected' ? 'success' : 'warning'"
                  effect="plain"
                >
                  {{ $t(row.connection_status === 'connected' ? 'Ready' : 'Reconnect required') }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column
              :label="$t('Daily usage')"
              min-width="180"
            >
              <template #default="{ row }">
                <el-progress
                  :percentage="usagePercentage(row)"
                  :format="() => `${row.today_sent} / ${row.daily_limit}`"
                />
              </template>
            </el-table-column>
          </el-table>
          <el-empty
            v-if="!loadingAccounts && !accountsError && accounts.length === 0"
            :description="$t('No connected accounts')"
          />
        </el-card>
      </el-col>

      <el-col
        :xs="24"
        :xl="9"
      >
        <el-card class="settings-card">
          <template #header>
            <div class="card-title">
              <el-icon><UserFilled /></el-icon>
              <div><strong>{{ $t('Signed-in account') }}</strong><span>{{ $t('Identity and access context') }}</span></div>
            </div>
          </template>
          <div class="profile">
            <el-avatar :size="48">
              {{ userInitial }}
            </el-avatar>
            <div>
              <strong>{{ authStore.user?.fullName || authStore.user?.username || $t('Unknown user') }}</strong>
              <span>{{ authStore.user?.email || $t('No email') }}</span>
            </div>
          </div>
          <el-descriptions
            :column="1"
            border
          >
            <el-descriptions-item :label="$t('Role')">
              {{ authStore.user?.role || 'user' }}
            </el-descriptions-item>
            <el-descriptions-item :label="$t('Administrator')">
              {{ $t(authStore.isAdmin ? 'Yes' : 'No') }}
            </el-descriptions-item>
            <el-descriptions-item :label="$t('User ID')">
              {{ authStore.user?.id || $t('Unavailable') }}
            </el-descriptions-item>
          </el-descriptions>
        </el-card>

        <el-card class="settings-card">
          <template #header>
            <div class="card-title">
              <el-icon><InfoFilled /></el-icon>
              <div><strong>{{ $t('Connection behavior') }}</strong><span>{{ $t('How runtime configuration is resolved') }}</span></div>
            </div>
          </template>
          <ol class="behavior-list">
            <li>{{ $t('A saved browser URL has highest priority.') }}</li>
            <li>{{ $t('Otherwise the Vite build-time API URL is used.') }}</li>
            <li>{{ $t('An empty value uses the same origin and Vite proxy during development.') }}</li>
          </ol>
        </el-card>
      </el-col>
    </el-row>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { api, updateBackendApiUrl } from '@/api'
import { aiApi, type AIRuntimeConfigUpdate } from '@/api/ai'
import {
  mailboxApi,
  type MailboxAccount,
  type MailboxOAuthProvider,
  type MailboxProvider,
} from '@/api/mailboxes'
import { resolveBackendApiUrl } from '@/api/runtimeConfig'
import { useAuthStore } from '@/stores/auth'
import { translate } from '@/i18n'

interface HealthResponse { status: string; app?: string; version?: string }
const authStore = useAuthStore()
const route = useRoute()
const router = useRouter()
const backendUrl = ref(resolveBackendApiUrl())
const effectiveUrl = ref(resolveBackendApiUrl())
const testingConnection = ref(false)
const connectionState = ref<'idle' | 'healthy' | 'failed'>('idle')
const connectionMessage = ref('')
const health = ref<HealthResponse | null>(null)
const accounts = ref<MailboxAccount[]>([])
const mailboxProviders = ref<MailboxOAuthProvider[]>([])
const connectingProvider = ref<MailboxProvider | ''>('')
const accountsError = ref('')
const loadingAccounts = ref(false)
const loadingAiConfig = ref(false)
const savingAiConfig = ref(false)
const testingAiConfig = ref(false)
const discoveringModels = ref(false)
const aiConfigVersion = ref('env v0')
const aiKeyConfigured = ref(false)
const aiKey = ref('')
const discoveredModels = ref<string[]>([])
const aiProbe = ref<{ ready: boolean; reachable: boolean; models: string[]; issues: string[] } | null>(null)
const aiConfig = ref<AIRuntimeConfigUpdate>({
  backend: 'omniroute',
  base_url: 'http://omniroute:20128',
  allowed_providers: [],
  model_aliases: {},
  timeout_seconds: 60,
})
const aiUseCases = [
  { key: 'lead_classification', label: 'Lead classification model' },
  { key: 'message_draft', label: 'Message drafting model' },
  { key: 'live_reply', label: 'Live reply model' },
  { key: 'rag_query_rewrite', label: 'Knowledge query model' },
  { key: 'summarization', label: 'Summarization model' },
]

const userInitial = computed(() => (authStore.user?.username || '?').charAt(0).toUpperCase())
const connectionLabel = computed(() => translate(({ idle: 'Not tested', healthy: 'Connected', failed: 'Unavailable' })[connectionState.value]))
const connectionTagType = computed(() => connectionState.value === 'healthy' ? 'success' : connectionState.value === 'failed' ? 'danger' : 'info')

function saveBackendUrl() {
  try {
    effectiveUrl.value = updateBackendApiUrl(backendUrl.value)
    backendUrl.value = effectiveUrl.value
    connectionState.value = 'idle'
    connectionMessage.value = ''
    health.value = null
    ElMessage.success(translate('Backend API configuration applied'))
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : translate('Invalid backend URL'))
  }
}

function resetBackendUrl() {
  backendUrl.value = ''
  saveBackendUrl()
}

async function testBackendConnection() {
  testingConnection.value = true
  try {
    saveBackendUrl()
    const response = await api.get<HealthResponse>('/health', { timeout: 8000 })
    health.value = response.data
    connectionState.value = response.data.status === 'healthy' ? 'healthy' : 'failed'
    connectionMessage.value = connectionState.value === 'healthy'
      ? translate('Backend connection is healthy.')
      : translate('Backend responded with status: {status}', { status: response.data.status })
  } catch {
    health.value = null
    connectionState.value = 'failed'
    connectionMessage.value = translate('The backend could not be reached with this configuration.')
  } finally {
    testingConnection.value = false
  }
}

async function loadAccounts() {
  loadingAccounts.value = true
  accountsError.value = ''
  try {
    const [providers, linkedAccounts] = await Promise.all([
      mailboxApi.providers(),
      mailboxApi.list(),
    ])
    mailboxProviders.value = providers
    accounts.value = linkedAccounts
  } catch {
    accounts.value = []
    accountsError.value = translate('Accounts are unavailable. Verify the connection and your access permissions.')
  } finally {
    loadingAccounts.value = false
  }
}

async function startMailboxOAuth(provider: MailboxProvider) {
  connectingProvider.value = provider
  try {
    const result = await mailboxApi.startOAuth(provider)
    window.location.assign(result.authorization_url)
  } catch {
    ElMessage.error(translate('Mailbox authorization could not be started.'))
    connectingProvider.value = ''
  }
}

async function consumeMailboxOAuthResult() {
  if (route.query.mailbox_oauth !== 'success') return
  ElMessage.success(translate('Mailbox connected successfully.'))
  const query = { ...route.query }
  delete query.mailbox_oauth
  delete query.provider
  await router.replace({ query })
}

async function loadAiConfig() {
  if (!authStore.isAdmin) return
  loadingAiConfig.value = true
  try {
    const config = await aiApi.getConfig()
    aiConfig.value = {
      backend: config.backend,
      base_url: config.base_url,
      allowed_providers: [...config.allowed_providers],
      model_aliases: { ...config.model_aliases },
      timeout_seconds: config.timeout_seconds,
    }
    aiKey.value = ''
    aiKeyConfigured.value = config.api_key_configured
    aiConfigVersion.value = (config.source === 'runtime' ? 'runtime' : 'env') + ' v' + config.version
  } catch {
    ElMessage.error(translate('AI route configuration could not be loaded.'))
  } finally {
    loadingAiConfig.value = false
  }
}

async function saveAiConfig() {
  savingAiConfig.value = true
  try {
    const aliases = Object.fromEntries(
      Object.entries(aiConfig.value.model_aliases).filter(([, value]) => value?.trim()),
    )
    const updated = await aiApi.updateConfig({
      ...aiConfig.value,
      model_aliases: aliases,
      ...(aiKey.value.trim() ? { api_key: aiKey.value.trim() } : {}),
    })
    aiKey.value = ''
    aiKeyConfigured.value = updated.api_key_configured
    aiConfigVersion.value = 'runtime v' + updated.version
    aiProbe.value = null
    ElMessage.success(translate('AI route applied to new requests.'))
  } catch {
    ElMessage.error(translate('AI route configuration could not be applied.'))
  } finally {
    savingAiConfig.value = false
  }
}

async function probeAiConfig() {
  testingAiConfig.value = true
  try {
    aiProbe.value = await aiApi.testConfig()
    discoveredModels.value = aiProbe.value.models
  } catch {
    aiProbe.value = { ready: false, reachable: false, models: [], issues: ['gateway_probe_failed'] }
  } finally {
    testingAiConfig.value = false
  }
}

async function discoverAiModels() {
  discoveringModels.value = true
  try {
    discoveredModels.value = await aiApi.listModels()
    ElMessage.success(translate('{count} AI routes discovered', { count: discoveredModels.value.length }))
  } catch {
    ElMessage.error(translate('AI models could not be discovered.'))
  } finally {
    discoveringModels.value = false
  }
}

function usagePercentage(account: MailboxAccount) {
  return Math.min(100, Math.round((account.today_sent / Math.max(account.daily_limit, 1)) * 100))
}

onMounted(() => {
  void consumeMailboxOAuthResult()
  void loadAccounts()
  void loadAiConfig()
})
</script>

<style scoped lang="scss">
.settings-card { margin-bottom: 16px; }
.settings-page, .settings-page > *, .settings-page :deep(.el-col) { min-width: 0; }
.card-title { display: flex; align-items: center; gap: 10px; }
.card-title > div { display: grid; flex: 1; gap: 2px; }
.card-title span, .profile span, .account-name span { color: var(--el-text-color-secondary); font-size: 12px; }
.field-help { margin: 6px 0 0; color: var(--el-text-color-secondary); font-size: 12px; }
.form-actions { display: flex; flex-wrap: wrap; gap: 8px; }
.connection-result, .health-grid { margin-top: 16px; }
.ai-security-note { margin-bottom: 16px; }
.alias-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 0 12px; }
.alias-grid :deep(.el-select), .settings-card :deep(.el-input-number) { width: 100%; }
.profile { display: flex; align-items: center; gap: 12px; margin-bottom: 18px; }
.profile > div, .account-name { display: grid; gap: 3px; }
.mailbox-connect-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; margin-bottom: 10px; }
.mailbox-connect-card { display: flex; align-items: center; gap: 12px; padding: 14px; border: 1px solid var(--el-border-color-lighter); border-radius: 16px; background: color-mix(in srgb, var(--el-fill-color-light) 70%, transparent); }
.mailbox-provider-mark { display: grid; place-items: center; width: 38px; height: 38px; flex: 0 0 38px; border-radius: 11px; color: #fff; font-weight: 700; background: #2563eb; }
.mailbox-provider-mark.gmail { background: linear-gradient(135deg, #4285f4, #ea4335); }
.mailbox-provider-mark.outlook { background: linear-gradient(135deg, #0078d4, #28a8ea); }
.mailbox-provider-copy { display: grid; gap: 3px; min-width: 0; flex: 1; }
.mailbox-provider-copy span, .mailbox-security-note { color: var(--el-text-color-secondary); font-size: 12px; }
.mailbox-security-note { margin: 0 0 16px; }
.behavior-list { margin: 0; padding-left: 20px; color: var(--el-text-color-regular); line-height: 1.7; }
@media (max-width: 760px) {
  .settings-page .page-heading { align-items: stretch; flex-wrap: wrap; }
  .settings-page .page-heading > .el-button { width: 100%; margin-left: 0; }
  .form-actions > * { flex: 1 1 100%; min-width: 0; width: 100%; margin-left: 0 !important; }
  .alias-grid, .mailbox-connect-grid { grid-template-columns: minmax(0, 1fr); width: 100%; }
  .mailbox-connect-card { flex-wrap: wrap; min-width: 0; }
  .mailbox-connect-card > .el-button { width: 100%; margin-left: 0; }
}
</style>
