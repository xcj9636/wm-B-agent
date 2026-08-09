<template>
  <div class="prospecting page-stack">
    <header class="page-heading">
      <div>
        <p class="page-kicker">
          {{ $t('Relationships') }}
        </p>
        <h1>{{ $t('Prospecting') }}</h1>
        <p>{{ $t('Find verified overseas buyers and import only the contacts your team approves.') }}</p>
      </div>
      <div class="safety-note apple-glass">
        <el-icon><CircleCheck /></el-icon>
        <span>{{ $t('Verified contacts can be imported. Other statuses are retained but blocked from outreach.') }}</span>
      </div>
    </header>

    <section class="workspace-grid">
      <el-card
        class="search-panel"
        shadow="never"
      >
        <template #header>
          <div class="panel-heading">
            <div>
              <span class="eyebrow">{{ $t('Search mode') }}</span>
              <strong>{{ $t(modeLabel) }}</strong>
            </div>
            <el-segmented
              v-model="form.mode"
              :options="modeOptions"
            />
          </div>
        </template>

        <el-form
          label-position="top"
          @submit.prevent="runSearch"
        >
          <div
            v-if="form.mode !== 'batch_domain_search'"
            class="form-grid"
          >
            <el-form-item :label="$t('Company domain')">
              <el-input
                v-model="form.domain"
                placeholder="example.com"
                clearable
              />
            </el-form-item>
            <el-form-item :label="$t('Company name')">
              <el-input
                v-model="form.company"
                placeholder="Acme Europe"
                clearable
              />
            </el-form-item>
          </div>

          <template v-if="form.mode === 'batch_domain_search'">
            <el-form-item :label="$t('Company domains')">
              <el-input
                v-model="form.batch_domains"
                type="textarea"
                :rows="5"
                :placeholder="$t('One domain per line')"
              />
              <p class="field-hint">
                {{ $t('Duplicate domains are normalized before the job is queued.') }}
              </p>
            </el-form-item>
            <div class="form-grid batch-settings">
              <el-form-item :label="$t('Page size')">
                <el-input-number
                  v-model="form.page_size"
                  :min="1"
                  :max="100"
                />
              </el-form-item>
              <el-form-item :label="$t('Maximum pages per domain')">
                <el-input-number
                  v-model="form.max_pages_per_domain"
                  :min="1"
                  :max="10"
                />
              </el-form-item>
              <el-form-item :label="$t('Request budget')">
                <el-input-number
                  v-model="form.request_budget"
                  :min="1"
                  :max="500"
                />
              </el-form-item>
            </div>
          </template>

          <template v-if="form.mode === 'email_finder'">
            <div class="form-grid named-grid">
              <el-form-item :label="$t('First name')">
                <el-input
                  v-model="form.first_name"
                  autocomplete="off"
                />
              </el-form-item>
              <el-form-item :label="$t('Last name')">
                <el-input
                  v-model="form.last_name"
                  autocomplete="off"
                />
              </el-form-item>
              <el-form-item :label="$t('Full name')">
                <el-input
                  v-model="form.full_name"
                  autocomplete="off"
                />
              </el-form-item>
            </div>
            <el-form-item :label="$t('Maximum lookup duration')">
              <div class="slider-row">
                <el-slider
                  v-model="form.max_duration"
                  :min="3"
                  :max="20"
                  :show-tooltip="false"
                />
                <span>{{ form.max_duration }} {{ $t('Seconds') }}</span>
              </div>
            </el-form-item>
          </template>

          <template v-else>
            <div class="form-grid">
              <el-form-item :label="$t('Contact type')">
                <el-select
                  v-model="form.contact_type"
                  clearable
                  :placeholder="$t('Any type')"
                >
                  <el-option
                    :label="$t('Personal')"
                    value="personal"
                  />
                  <el-option
                    :label="$t('Generic')"
                    value="generic"
                  />
                </el-select>
              </el-form-item>
              <el-form-item
                v-if="form.mode === 'domain_search'"
                :label="$t('Result limit')"
              >
                <el-input-number
                  v-model="form.limit"
                  :min="1"
                  :max="100"
                />
              </el-form-item>
            </div>
            <div class="form-grid">
              <el-form-item :label="$t('Department')">
                <el-select
                  v-model="form.departments"
                  multiple
                  collapse-tags
                  clearable
                >
                  <el-option
                    v-for="item in departments"
                    :key="item"
                    :label="$t(departmentLabel[item])"
                    :value="item"
                  />
                </el-select>
              </el-form-item>
              <el-form-item :label="$t('Seniority')">
                <el-select
                  v-model="form.seniorities"
                  multiple
                  clearable
                >
                  <el-option
                    v-for="item in seniorities"
                    :key="item"
                    :label="$t(seniorityLabel[item])"
                    :value="item"
                  />
                </el-select>
              </el-form-item>
            </div>
            <div class="form-grid compact-grid">
              <el-form-item :label="$t('Verification status')">
                <el-checkbox-group v-model="form.verification_statuses">
                  <el-checkbox value="valid">
                    {{ $t('valid') }}
                  </el-checkbox>
                  <el-checkbox value="accept_all">
                    {{ $t('Accept all') }}
                  </el-checkbox>
                  <el-checkbox value="unknown">
                    {{ $t('Unknown') }}
                  </el-checkbox>
                </el-checkbox-group>
              </el-form-item>
              <el-form-item
                class="switch-field"
                :label="$t('Decision makers only')"
              >
                <el-switch v-model="form.decision_maker" />
              </el-form-item>
            </div>
          </template>

          <el-button
            class="search-button"
            type="primary"
            native-type="submit"
            :loading="searching"
          >
            <el-icon><Search /></el-icon>
            {{ searching
              ? $t('Searching Hunter through the secure backend')
              : $t(form.mode === 'batch_domain_search' ? 'Start batch job' : 'Search prospects') }}
          </el-button>
        </el-form>
      </el-card>

      <el-card
        class="history-panel"
        shadow="never"
      >
        <template #header>
          <div class="panel-heading">
            <div>
              <span class="eyebrow">{{ $t('Workspace') }}</span>
              <strong>{{ $t('Recent searches') }}</strong>
            </div>
            <el-button
              circle
              text
              :aria-label="$t('Refresh')"
              :loading="historyLoading"
              @click="loadSearches"
            >
              <el-icon><Refresh /></el-icon>
            </el-button>
          </div>
        </template>
        <div
          v-if="searches.length"
          class="history-list"
        >
          <button
            v-for="item in searches"
            :key="item.id"
            type="button"
            class="history-item"
            :class="{ active: activeSearch?.id === item.id }"
            @click="openSearch(item.id)"
          >
            <span class="history-icon"><el-icon><OfficeBuilding /></el-icon></span>
            <span class="history-copy">
              <strong>{{ searchTitle(item) }}</strong>
              <small>{{ formatDate(item.created_at) }} · {{ item.result_count }} {{ $t('Results') }}</small>
            </span>
            <el-tag
              size="small"
              :type="statusType(item.status)"
            >
              {{ $t(item.status) }}
            </el-tag>
          </button>
        </div>
        <el-empty
          v-else
          :description="$t('No prospect searches yet')"
          :image-size="72"
        />
      </el-card>
    </section>

    <el-card
      v-if="jobs.length || jobsLoading"
      class="jobs-panel"
      shadow="never"
    >
      <template #header>
        <div class="panel-heading">
          <div>
            <span class="eyebrow">{{ $t('Durable execution') }}</span>
            <strong>{{ $t('Batch enrichment jobs') }}</strong>
          </div>
          <el-button
            circle
            text
            :aria-label="$t('Refresh')"
            :loading="jobsLoading"
            @click="loadJobs()"
          >
            <el-icon><Refresh /></el-icon>
          </el-button>
        </div>
      </template>

      <div class="job-list">
        <article
          v-for="job in jobs"
          :key="job.id"
          class="job-card"
        >
          <div class="job-summary">
            <span class="history-icon"><el-icon><Files /></el-icon></span>
            <div class="job-title">
              <strong>{{ $t('Batch enrichment') }} · {{ job.total_items }} {{ $t('Domains') }}</strong>
              <span>{{ formatDate(job.created_at) }} · v{{ job.connector_version }}</span>
            </div>
            <el-tag :type="statusType(job.status)">
              {{ $t(job.status) }}
            </el-tag>
          </div>

          <el-progress
            :percentage="jobProgress(job)"
            :status="job.status === 'completed' ? 'success' : undefined"
          />

          <dl class="job-metrics">
            <div><dt>{{ $t('Domains completed') }}</dt><dd>{{ job.completed_items + job.failed_items }}/{{ job.total_items }}</dd></div>
            <div><dt>{{ $t('Contacts found') }}</dt><dd>{{ job.contacts_found }}</dd></div>
            <div><dt>{{ $t('Request budget') }}</dt><dd>{{ job.requests_used }}/{{ job.request_budget }}</dd></div>
            <div><dt>{{ $t('Provider quota snapshot') }}</dt><dd>{{ job.provider_remaining ?? '—' }} {{ job.provider_usage_unit || '' }}</dd></div>
          </dl>

          <p
            v-if="job.error_code"
            class="job-error"
          >
            {{ $t('Action required') }}: {{ $t(job.error_code) }}
          </p>

          <div class="job-items">
            <button
              v-for="item in job.items"
              :key="item.id"
              type="button"
              class="job-item"
              @click="openSearch(item.search_id)"
            >
              <span><strong>{{ item.domain }}</strong><small>{{ item.contacts_found }} {{ $t('Contacts') }} · {{ item.pages_completed }}/{{ job.max_pages_per_domain }} {{ $t('Pages') }}</small></span>
              <el-tag
                size="small"
                :type="statusType(item.status)"
              >
                {{ $t(item.status) }}
              </el-tag>
            </button>
          </div>

          <div class="job-actions">
            <el-button
              v-if="canPauseJob(job.status)"
              size="small"
              @click="pauseJob(job.id)"
            >
              {{ $t('Pause job') }}
            </el-button>
            <template v-if="canResumeJob(job.status)">
              <el-input-number
                v-model="resumeBudgets[job.id]"
                size="small"
                :min="0"
                :max="500"
                :aria-label="$t('Additional requests')"
              />
              <el-button
                size="small"
                type="primary"
                @click="resumeJob(job.id)"
              >
                {{ $t('Resume job') }}
              </el-button>
            </template>
          </div>
        </article>
      </div>
    </el-card>

    <el-card
      class="results-panel"
      shadow="never"
    >
      <template #header>
        <div class="results-heading">
          <div>
            <span class="eyebrow">{{ $t('Search results') }}</span>
            <strong v-if="activeSearch">{{ $t('{count} contacts found', { count: activeSearch.result_count }) }}</strong>
            <strong v-else>{{ $t('Run a search to build an evidence-backed prospect list.') }}</strong>
          </div>
          <div class="result-actions">
            <span
              v-if="selectedIds.length"
              class="selection-count"
            >{{ selectedIds.length }} {{ $t('Selected') }}</span>
            <el-button
              type="primary"
              :disabled="!selectedIds.length"
              :loading="importing"
              @click="importSelected"
            >
              <el-icon><Download /></el-icon>
              {{ $t('Import selected') }}
            </el-button>
          </div>
        </div>
      </template>

      <el-table
        v-loading="resultLoading"
        :data="activeSearch?.contacts ?? []"
        row-key="id"
        @selection-change="onSelectionChange"
      >
        <el-table-column
          type="selection"
          width="48"
          :selectable="canSelect"
        />
        <el-table-column
          :label="$t('Contact')"
          min-width="220"
        >
          <template #default="{ row }">
            <div class="contact-cell">
              <strong>{{ contactName(row) }}</strong>
              <span>{{ row.email }}</span>
            </div>
          </template>
        </el-table-column>
        <el-table-column
          :label="$t('Company')"
          min-width="170"
        >
          <template #default="{ row }">
            <div class="company-cell">
              <strong>{{ row.company || row.domain || '—' }}</strong>
              <span>{{ row.position || '—' }}</span>
            </div>
          </template>
        </el-table-column>
        <el-table-column
          prop="department"
          :label="$t('Department')"
          min-width="110"
        />
        <el-table-column
          :label="$t('Verification status')"
          width="145"
        >
          <template #default="{ row }">
            <el-tag
              :type="verificationType(row.verification_status)"
              effect="light"
            >
              {{ $t(row.verification_status) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column
          :label="$t('Confidence')"
          width="105"
        >
          <template #default="{ row }">
            <span class="confidence">{{ row.confidence == null ? '—' : `${row.confidence}%` }}</span>
          </template>
        </el-table-column>
        <el-table-column
          :label="$t('Evidence')"
          min-width="130"
        >
          <template #default="{ row }">
            <div
              v-if="row.evidence.length"
              class="evidence-links"
            >
              <a
                v-for="source in row.evidence"
                :key="source.uri"
                :href="source.uri"
                target="_blank"
                rel="noopener noreferrer"
              >
                {{ source.domain }}
              </a>
            </div>
            <span v-else>—</span>
          </template>
        </el-table-column>
        <el-table-column
          :label="$t('Status')"
          width="95"
        >
          <template #default="{ row }">
            <el-tag
              v-if="row.imported_customer_id"
              type="success"
              size="small"
            >
              {{ $t('Imported') }}
            </el-tag>
            <span v-else>—</span>
          </template>
        </el-table-column>
        <template #empty>
          <div class="table-empty">
            {{ $t(activeSearch ? 'No contacts in this search' : 'Run a search to build an evidence-backed prospect list.') }}
          </div>
        </template>
      </el-table>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { translate } from '@/i18n'
import {
  prospectingApi,
  type ProspectingContact,
  type ProspectingJob,
  type ProspectingJobCreate,
  type ProspectingMode,
  type ProspectingSearch,
  type ProspectingSearchCreate,
} from '@/api/prospecting'

const modeOptions = computed(() => [
  { label: translate('Domain search'), value: 'domain_search' },
  { label: translate('Named person'), value: 'email_finder' },
  { label: translate('Batch enrichment'), value: 'batch_domain_search' },
])
const modeLabel = computed(() => ({
  domain_search: 'Domain search',
  email_finder: 'Named person',
  batch_domain_search: 'Batch enrichment',
}[form.mode]))
const departments = ['executive', 'sales', 'marketing', 'management', 'operations', 'finance', 'it']
const departmentLabel: Record<string, string> = {
  executive: 'Executive', sales: 'Sales', marketing: 'Marketing', management: 'Management',
  operations: 'Business operations', finance: 'Finance', it: 'IT',
}
const seniorities = ['executive', 'senior', 'junior']
const seniorityLabel: Record<string, string> = { executive: 'Executive', senior: 'Senior', junior: 'Junior' }

const form = reactive({
  mode: 'domain_search' as ProspectingMode,
  domain: '',
  company: '',
  first_name: '',
  last_name: '',
  full_name: '',
  limit: 20,
  contact_type: '' as '' | 'personal' | 'generic',
  seniorities: [] as string[],
  departments: [] as string[],
  decision_maker: false,
  verification_statuses: ['valid'] as string[],
  max_duration: 10,
  batch_domains: '',
  page_size: 10,
  max_pages_per_domain: 2,
  request_budget: 20,
})
const searches = ref<ProspectingSearch[]>([])
const jobs = ref<ProspectingJob[]>([])
const activeSearch = ref<ProspectingSearch | null>(null)
const resumeBudgets = reactive<Record<string, number>>({})
const selectedIds = ref<string[]>([])
const searching = ref(false)
const importing = ref(false)
const historyLoading = ref(false)
const resultLoading = ref(false)
const jobsLoading = ref(false)
let jobsTimer: number | undefined

function compact(value: string) {
  const result = value.trim()
  return result || undefined
}

function searchPayload(): ProspectingSearchCreate | null {
  if (form.mode === 'batch_domain_search') return null
  if (!compact(form.domain) && !compact(form.company)) {
    ElMessage.warning(translate('At least one company domain or company name is required.'))
    return null
  }
  if (form.mode === 'email_finder' && !compact(form.full_name) && !(compact(form.first_name) && compact(form.last_name))) {
    ElMessage.warning(translate('Enter a full name or both first and last name.'))
    return null
  }
  const base = { mode: form.mode, domain: compact(form.domain), company: compact(form.company) }
  if (form.mode === 'email_finder') {
    return {
      ...base,
      first_name: compact(form.first_name),
      last_name: compact(form.last_name),
      full_name: compact(form.full_name),
      max_duration: form.max_duration,
    }
  }
  return {
    ...base,
    limit: form.limit,
    contact_type: form.contact_type || undefined,
    seniorities: form.seniorities,
    departments: form.departments,
    decision_maker: form.decision_maker || undefined,
    verification_statuses: form.verification_statuses,
  }
}

async function runSearch() {
  if (form.mode === 'batch_domain_search') {
    await startBatchJob()
    return
  }
  const payload = searchPayload()
  if (!payload) return
  searching.value = true
  try {
    const result = await prospectingApi.createSearch(payload)
    activeSearch.value = result
    selectedIds.value = []
    searches.value = [result, ...searches.value.filter((item) => item.id !== result.id)]
    ElMessage.success(translate('Prospect search completed.'))
  } catch {
    ElMessage.error(translate('Prospect search failed. Check that Hunter is configured and enabled.'))
    await loadSearches()
  } finally {
    searching.value = false
  }
}

function batchPayload(): ProspectingJobCreate | null {
  const domains = form.batch_domains
    .split(/[\n,;]/)
    .map((domain) => domain.trim())
    .filter(Boolean)
  if (!domains.length) {
    ElMessage.warning(translate('Enter at least one company domain.'))
    return null
  }
  return {
    domains,
    page_size: form.page_size,
    max_pages_per_domain: form.max_pages_per_domain,
    request_budget: form.request_budget,
    contact_type: form.contact_type || undefined,
    seniorities: form.seniorities,
    departments: form.departments,
    decision_maker: form.decision_maker || undefined,
    verification_statuses: form.verification_statuses,
  }
}

async function startBatchJob() {
  const payload = batchPayload()
  if (!payload) return
  searching.value = true
  try {
    const job = await prospectingApi.createJob(payload)
    jobs.value = [job, ...jobs.value.filter((item) => item.id !== job.id)]
    resumeBudgets[job.id] = 0
    ElMessage.success(translate('Batch enrichment job queued.'))
  } catch {
    ElMessage.error(translate('Batch enrichment job could not be queued.'))
  } finally {
    searching.value = false
  }
}

async function loadSearches() {
  historyLoading.value = true
  try {
    searches.value = await prospectingApi.listSearches()
  } catch {
    ElMessage.error(translate('Recent searches could not be loaded.'))
  } finally {
    historyLoading.value = false
  }
}

async function loadJobs(silent = false) {
  if (!silent) jobsLoading.value = true
  try {
    jobs.value = await prospectingApi.listJobs()
    jobs.value.forEach((job) => {
      if (resumeBudgets[job.id] == null) resumeBudgets[job.id] = 0
    })
  } catch {
    if (!silent) ElMessage.error(translate('Batch jobs could not be loaded.'))
  } finally {
    jobsLoading.value = false
  }
}

function canPauseJob(status: string) {
  return ['queued', 'running', 'retry_wait'].includes(status)
}

function canResumeJob(status: string) {
  return ['paused', 'retry_wait', 'quota_blocked', 'budget_exhausted'].includes(status)
}

async function pauseJob(id: string) {
  try {
    const updated = await prospectingApi.pauseJob(id)
    replaceJob(updated)
    ElMessage.success(translate('Batch job paused.'))
  } catch {
    ElMessage.error(translate('Batch job could not be paused.'))
  }
}

async function resumeJob(id: string) {
  try {
    const updated = await prospectingApi.resumeJob(id, resumeBudgets[id] || 0)
    replaceJob(updated)
    resumeBudgets[id] = 0
    ElMessage.success(translate('Batch job resumed.'))
  } catch {
    ElMessage.error(translate('Batch job could not be resumed.'))
  }
}

function replaceJob(updated: ProspectingJob) {
  jobs.value = jobs.value.map((job) => job.id === updated.id ? updated : job)
}

function jobProgress(job: ProspectingJob) {
  if (!job.total_items) return 0
  return Math.round(((job.completed_items + job.failed_items) / job.total_items) * 100)
}

async function openSearch(id: string) {
  resultLoading.value = true
  try {
    activeSearch.value = await prospectingApi.getSearch(id)
    selectedIds.value = []
  } finally {
    resultLoading.value = false
  }
}

function onSelectionChange(rows: ProspectingContact[]) {
  selectedIds.value = rows.map((row) => row.id)
}

function canSelect(row: ProspectingContact) {
  return row.verification_status === 'valid' && !row.imported_customer_id
}

async function importSelected() {
  if (!selectedIds.value.length) {
    ElMessage.warning(translate('Select at least one contact.'))
    return
  }
  importing.value = true
  try {
    const result = await prospectingApi.importContacts(selectedIds.value)
    ElMessage.success(translate('Selected contacts imported: {created} new, {existing} existing.', {
      created: result.created,
      existing: result.existing,
    }))
    if (activeSearch.value) await openSearch(activeSearch.value.id)
    await loadSearches()
  } catch {
    ElMessage.error(translate('Contacts could not be imported.'))
  } finally {
    importing.value = false
  }
}

function contactName(row: ProspectingContact) {
  return [row.first_name, row.last_name].filter(Boolean).join(' ') || row.email.split('@')[0]
}

function searchTitle(item: ProspectingSearch) {
  const query = item.query as Record<string, unknown>
  return String(query.domain || query.company || translate(item.mode === 'domain_search' ? 'Domain search' : 'Named person'))
}

function formatDate(value: string) {
  return new Intl.DateTimeFormat(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }).format(new Date(value))
}

function statusType(status: string) {
  if (status === 'completed') return 'success'
  if (['failed', 'legal_restricted', 'completed_with_errors'].includes(status)) return 'danger'
  if (['queued', 'running'].includes(status)) return 'primary'
  return 'warning'
}

function verificationType(status: string) {
  return status === 'valid' ? 'success' : status === 'invalid' || status === 'disposable' ? 'danger' : 'warning'
}

onMounted(() => {
  void Promise.all([loadSearches(), loadJobs()])
  jobsTimer = window.setInterval(() => {
    if (jobs.value.some((job) => ['queued', 'running', 'retry_wait'].includes(job.status))) {
      void loadJobs(true)
    }
  }, 5000)
})

onUnmounted(() => {
  if (jobsTimer != null) window.clearInterval(jobsTimer)
})
</script>

<style scoped>
.prospecting { max-width: 1380px; margin: 0 auto; }
.page-heading { align-items: flex-start; }
.safety-note { display: flex; align-items: flex-start; gap: 10px; width: min(440px, 100%); padding: 13px 15px; border: 1px solid var(--border-hairline); border-radius: 16px; color: var(--text-secondary); font-size: 13px; line-height: 1.5; }
.safety-note .el-icon { flex: 0 0 auto; margin-top: 2px; color: var(--el-color-success); }
.workspace-grid { display: grid; grid-template-columns: minmax(0, 1fr) 350px; gap: 18px; }
.search-panel, .history-panel, .jobs-panel, .results-panel { border-radius: 20px; }
.panel-heading, .results-heading { display: flex; align-items: center; justify-content: space-between; gap: 16px; }
.panel-heading > div:first-child, .results-heading > div:first-child { display: grid; gap: 4px; }
.panel-heading strong, .results-heading strong { font-size: 17px; }
.eyebrow { color: var(--text-secondary); font-size: 11px; font-weight: 700; letter-spacing: .08em; text-transform: uppercase; }
.form-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 14px; }
.named-grid { grid-template-columns: repeat(3, minmax(0, 1fr)); }
.batch-settings { grid-template-columns: repeat(3, minmax(0, 1fr)); }
.compact-grid { align-items: end; }
.field-hint { margin: 7px 0 0; color: var(--text-secondary); font-size: 12px; }
.switch-field :deep(.el-form-item__content) { min-height: 32px; }
.search-panel :deep(.el-select), .search-panel :deep(.el-input-number) { width: 100%; }
.slider-row { display: flex; align-items: center; gap: 18px; width: 100%; }
.slider-row .el-slider { flex: 1; }
.slider-row span { min-width: 70px; color: var(--text-secondary); font-size: 13px; text-align: right; }
.search-button { min-width: 180px; margin-top: 4px; }
.history-list { display: grid; gap: 8px; max-height: 410px; overflow: auto; }
.history-item { appearance: none; display: flex; align-items: center; gap: 11px; width: 100%; padding: 11px; border: 1px solid transparent; border-radius: 14px; background: transparent; color: inherit; text-align: left; cursor: pointer; transition: .18s ease; }
.history-item:hover, .history-item.active { border-color: var(--border-hairline); background: var(--surface-hover); }
.history-icon { display: grid; place-items: center; width: 34px; height: 34px; border-radius: 10px; background: var(--el-color-primary-light-9); color: var(--el-color-primary); }
.history-copy { display: grid; flex: 1; min-width: 0; gap: 3px; }
.history-copy strong, .history-copy small { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.history-copy strong { font-size: 13px; }
.history-copy small { color: var(--text-secondary); font-size: 11px; }
.job-list { display: grid; gap: 14px; }
.job-card { display: grid; gap: 14px; padding: 16px; border: 1px solid var(--border-hairline); border-radius: 16px; background: var(--surface-sunken); }
.job-summary { display: flex; align-items: center; gap: 11px; }
.job-title { display: grid; flex: 1; gap: 3px; }
.job-title strong { font-size: 14px; }
.job-title span { color: var(--text-secondary); font-size: 11px; }
.job-metrics { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 10px; margin: 0; }
.job-metrics > div { display: grid; gap: 4px; padding: 10px; border-radius: 12px; background: var(--surface-elevated); }
.job-metrics dt { color: var(--text-secondary); font-size: 11px; }
.job-metrics dd { margin: 0; font-size: 14px; font-weight: 650; font-variant-numeric: tabular-nums; }
.job-error { margin: 0; padding: 10px 12px; border-radius: 11px; background: var(--el-color-danger-light-9); color: var(--el-color-danger); font-size: 12px; }
.job-items { display: grid; grid-template-columns: repeat(auto-fill, minmax(240px, 1fr)); gap: 7px; }
.job-item { appearance: none; display: flex; align-items: center; justify-content: space-between; gap: 10px; padding: 9px 10px; border: 1px solid var(--border-hairline); border-radius: 11px; color: inherit; background: var(--surface-elevated); text-align: left; cursor: pointer; }
.job-item:hover { border-color: color-mix(in srgb, var(--apple-blue) 45%, var(--border-hairline)); }
.job-item > span { display: grid; min-width: 0; gap: 3px; }
.job-item strong, .job-item small { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.job-item strong { font-size: 12px; }
.job-item small { color: var(--text-secondary); font-size: 10px; }
.job-actions { display: flex; align-items: center; justify-content: flex-end; gap: 8px; }
.job-actions :deep(.el-input-number) { width: 118px; }
.results-panel { min-height: 330px; }
.result-actions { display: flex; align-items: center; gap: 12px; }
.selection-count { color: var(--text-secondary); font-size: 13px; }
.contact-cell, .company-cell { display: grid; gap: 3px; }
.contact-cell strong, .company-cell strong { color: var(--text-primary); font-size: 13px; }
.contact-cell span, .company-cell span { color: var(--text-secondary); font-size: 12px; }
.confidence { font-variant-numeric: tabular-nums; font-weight: 650; }
.evidence-links { display: flex; flex-wrap: wrap; gap: 4px 8px; }
.evidence-links a { color: var(--el-color-primary); font-size: 12px; text-decoration: none; }
.evidence-links a:hover { text-decoration: underline; }
.table-empty { padding: 42px 0; color: var(--text-secondary); }
@media (max-width: 1050px) {
  .workspace-grid { grid-template-columns: 1fr; }
  .history-list { max-height: 270px; }
  .job-metrics { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}
@media (max-width: 720px) {
  .page-heading { display: grid; }
  .form-grid, .named-grid, .batch-settings { grid-template-columns: 1fr; gap: 0; }
  .panel-heading, .results-heading { align-items: stretch; flex-direction: column; }
  .panel-heading :deep(.el-segmented) { width: 100%; }
  .search-button { width: 100%; }
  .result-actions { justify-content: space-between; }
  .job-metrics { grid-template-columns: 1fr 1fr; }
  .job-actions { align-items: stretch; flex-direction: column; }
  .job-actions :deep(.el-input-number), .job-actions .el-button { width: 100%; }
}
</style>
