<template>
  <div class="page-stack settings-page">
    <header class="page-heading">
      <div>
        <p class="page-kicker">
          System configuration
        </p>
        <h1>Settings</h1>
        <p>Configure the browser-to-backend connection and inspect linked delivery accounts.</p>
      </div>
      <el-button
        :loading="loadingAccounts"
        @click="loadAccounts"
      >
        <el-icon><Refresh /></el-icon>
        Refresh accounts
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
                <strong>Backend API</strong>
                <span>Applied immediately in this browser</span>
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
            <el-form-item label="Base URL">
              <el-input
                v-model="backendUrl"
                clearable
                placeholder="Leave empty to use the Vite or reverse-proxy origin"
              >
                <template #prepend>
                  HTTP(S)
                </template>
              </el-input>
              <p class="field-help">
                Example: http://localhost:8000. Empty uses the current origin and development proxy.
              </p>
            </el-form-item>
            <div class="form-actions">
              <el-button
                type="primary"
                native-type="submit"
              >
                Save and apply
              </el-button>
              <el-button
                :loading="testingConnection"
                @click="testBackendConnection"
              >
                Test connection
              </el-button>
              <el-button @click="resetBackendUrl">
                Use proxy default
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
            <el-descriptions-item label="Service">
              {{ health.app || 'B-Agent API' }}
            </el-descriptions-item>
            <el-descriptions-item label="Version">
              {{ health.version || 'Not reported' }}
            </el-descriptions-item>
            <el-descriptions-item label="Status">
              {{ health.status }}
            </el-descriptions-item>
            <el-descriptions-item label="Effective URL">
              {{ effectiveUrl || 'Same origin' }}
            </el-descriptions-item>
          </el-descriptions>
        </el-card>

        <el-card class="settings-card">
          <template #header>
            <div class="card-title">
              <el-icon><Link /></el-icon>
              <div>
                <strong>Connected accounts</strong>
                <span>Delivery identities exposed by the administration API</span>
              </div>
              <el-tag effect="plain">
                {{ accounts.length }}
              </el-tag>
            </div>
          </template>

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
              label="Account"
              min-width="170"
            >
              <template #default="{ row }">
                <div class="account-name">
                  <strong>{{ row.name }}</strong>
                  <span>{{ row.email || row.phone_number || 'No address' }}</span>
                </div>
              </template>
            </el-table-column>
            <el-table-column
              prop="account_type"
              label="Type"
              width="170"
            />
            <el-table-column
              label="State"
              width="120"
            >
              <template #default="{ row }">
                <el-tag
                  :type="row.is_active && row.is_verified ? 'success' : 'info'"
                  effect="plain"
                >
                  {{ row.is_active ? (row.is_verified ? 'Ready' : 'Unverified') : 'Disabled' }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column
              label="Daily usage"
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
            description="No connected accounts"
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
              <div><strong>Signed-in account</strong><span>Identity and access context</span></div>
            </div>
          </template>
          <div class="profile">
            <el-avatar :size="48">
              {{ userInitial }}
            </el-avatar>
            <div>
              <strong>{{ authStore.user?.fullName || authStore.user?.username || 'Unknown user' }}</strong>
              <span>{{ authStore.user?.email || 'No email' }}</span>
            </div>
          </div>
          <el-descriptions
            :column="1"
            border
          >
            <el-descriptions-item label="Role">
              {{ authStore.user?.role || 'user' }}
            </el-descriptions-item>
            <el-descriptions-item label="Administrator">
              {{ authStore.isAdmin ? 'Yes' : 'No' }}
            </el-descriptions-item>
            <el-descriptions-item label="User ID">
              {{ authStore.user?.id || 'Unavailable' }}
            </el-descriptions-item>
          </el-descriptions>
        </el-card>

        <el-card class="settings-card">
          <template #header>
            <div class="card-title">
              <el-icon><InfoFilled /></el-icon>
              <div><strong>Connection behavior</strong><span>How runtime configuration is resolved</span></div>
            </div>
          </template>
          <ol class="behavior-list">
            <li>A saved browser URL has highest priority.</li>
            <li>Otherwise the Vite build-time API URL is used.</li>
            <li>An empty value uses the same origin and Vite proxy during development.</li>
          </ol>
        </el-card>
      </el-col>
    </el-row>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { api, updateBackendApiUrl } from '@/api'
import { resolveBackendApiUrl } from '@/api/runtimeConfig'
import { useAuthStore } from '@/stores/auth'

interface HealthResponse { status: string; app?: string; version?: string }
interface AccountResponse {
  id: number
  account_type: string
  name: string
  email?: string
  phone_number?: string
  is_active: boolean
  is_verified: boolean
  daily_limit: number
  today_sent: number
}

const authStore = useAuthStore()
const backendUrl = ref(resolveBackendApiUrl())
const effectiveUrl = ref(resolveBackendApiUrl())
const testingConnection = ref(false)
const connectionState = ref<'idle' | 'healthy' | 'failed'>('idle')
const connectionMessage = ref('')
const health = ref<HealthResponse | null>(null)
const accounts = ref<AccountResponse[]>([])
const accountsError = ref('')
const loadingAccounts = ref(false)

const userInitial = computed(() => (authStore.user?.username || '?').charAt(0).toUpperCase())
const connectionLabel = computed(() => ({ idle: 'Not tested', healthy: 'Connected', failed: 'Unavailable' })[connectionState.value])
const connectionTagType = computed(() => connectionState.value === 'healthy' ? 'success' : connectionState.value === 'failed' ? 'danger' : 'info')

function saveBackendUrl() {
  try {
    effectiveUrl.value = updateBackendApiUrl(backendUrl.value)
    backendUrl.value = effectiveUrl.value
    connectionState.value = 'idle'
    connectionMessage.value = ''
    health.value = null
    ElMessage.success('Backend API configuration applied')
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : 'Invalid backend URL')
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
      ? 'Backend connection is healthy.'
      : `Backend responded with status: ${response.data.status}`
  } catch {
    health.value = null
    connectionState.value = 'failed'
    connectionMessage.value = 'The backend could not be reached with this configuration.'
  } finally {
    testingConnection.value = false
  }
}

async function loadAccounts() {
  loadingAccounts.value = true
  accountsError.value = ''
  try {
    const response = await api.get<AccountResponse[]>('/api/v1/admin/accounts')
    accounts.value = response.data
  } catch {
    accounts.value = []
    accountsError.value = 'Accounts are unavailable. Verify the connection and your access permissions.'
  } finally {
    loadingAccounts.value = false
  }
}

function usagePercentage(account: AccountResponse) {
  return Math.min(100, Math.round((account.today_sent / Math.max(account.daily_limit, 1)) * 100))
}

onMounted(() => {
  void loadAccounts()
})
</script>

<style scoped lang="scss">
.settings-card { margin-bottom: 16px; }
.card-title { display: flex; align-items: center; gap: 10px; }
.card-title > div { display: grid; flex: 1; gap: 2px; }
.card-title span, .profile span, .account-name span { color: var(--el-text-color-secondary); font-size: 12px; }
.field-help { margin: 6px 0 0; color: var(--el-text-color-secondary); font-size: 12px; }
.form-actions { display: flex; flex-wrap: wrap; gap: 8px; }
.connection-result, .health-grid { margin-top: 16px; }
.profile { display: flex; align-items: center; gap: 12px; margin-bottom: 18px; }
.profile > div, .account-name { display: grid; gap: 3px; }
.behavior-list { margin: 0; padding-left: 20px; color: var(--el-text-color-regular); line-height: 1.7; }
@media (max-width: 640px) { .form-actions > * { flex: 1 1 100%; } }
</style>
