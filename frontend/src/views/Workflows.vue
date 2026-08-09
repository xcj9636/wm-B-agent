<template>
  <div class="workflows page-stack">
    <header class="page-heading">
      <div>
        <p class="page-kicker">
          {{ $t('Automation') }}
        </p>
        <h1>{{ $t('Workflows') }}</h1>
        <p>{{ $t('Build, inspect and run reusable business automations.') }}</p>
      </div>
      <el-button
        type="primary"
        @click="showCreateDialog = true"
      >
        <el-icon><Plus /></el-icon>
        {{ $t('Create Workflow') }}
      </el-button>
    </header>

    <el-card shadow="never">
      <el-table
        v-loading="loading"
        :data="workflows"
      >
        <el-table-column
          prop="name"
          :label="$t('Name')"
        />
        <el-table-column
          prop="description"
          :label="$t('Description')"
          show-overflow-tooltip
        />
        <el-table-column
          prop="status"
          :label="$t('Status')"
        >
          <template #default="{ row }">
            <el-tag :type="getStatusType(row.status)">
              {{ $t(row.status) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column
          prop="version"
          :label="$t('Version')"
          width="100"
        />
        <el-table-column
          :label="$t('Actions')"
          width="200"
        >
          <template #default="{ row }">
            <el-button
              text
              @click="editWorkflow(row.id)"
            >
              {{ $t('Edit') }}
            </el-button>
            <el-button
              text
              @click="executeWorkflow(row.id)"
            >
              {{ $t('Execute') }}
            </el-button>
            <el-button
              text
              type="danger"
              @click="deleteWorkflow(row.id)"
            >
              {{ $t('Delete') }}
            </el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <el-dialog
      v-model="showCreateDialog"
      :title="$t('Create Workflow')"
      width="600px"
    >
      <el-form
        :model="form"
        label-position="top"
      >
        <el-form-item :label="$t('Name')">
          <el-input
            v-model="form.name"
            :placeholder="$t('Enter workflow name')"
          />
        </el-form-item>
        <el-form-item :label="$t('Description')">
          <el-input
            v-model="form.description"
            type="textarea"
            :placeholder="$t('Enter workflow description')"
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showCreateDialog = false">
          {{ $t('Cancel') }}
        </el-button>
        <el-button
          type="primary"
          @click="createWorkflow"
        >
          {{ $t('Create') }}
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessageBox, ElMessage } from 'element-plus'
import { useWorkflowStore } from '@/stores/workflow'
import { translate } from '@/i18n'

const router = useRouter()
const workflowStore = useWorkflowStore()

const loading = ref(false)
const showCreateDialog = ref(false)
const workflows = ref<any[]>([])

const form = ref({
  name: '',
  description: '',
})

async function fetchWorkflows() {
  loading.value = true
  try {
    await workflowStore.fetchWorkflows()
    workflows.value = workflowStore.workflows
  } finally {
    loading.value = false
  }
}

async function createWorkflow() {
  try {
    await workflowStore.createWorkflow({
      name: form.value.name,
      description: form.value.description,
      steps: [],
      transitions: [],
    })
    ElMessage.success(translate('Workflow created'))
    showCreateDialog.value = false
    form.value = { name: '', description: '' }
    await fetchWorkflows()
  } catch {
    ElMessage.error(translate('Failed to create workflow'))
  }
}

function editWorkflow(id: string) {
  router.push(`/workflows/${id}`)
}

async function executeWorkflow(id: string) {
  try {
    await ElMessageBox.confirm(translate('Execute this workflow?'), translate('Confirm'))
    await workflowStore.executeWorkflow(id, {})
    ElMessage.success(translate('Workflow execution started'))
  } catch {
    // Cancelled
  }
}

async function deleteWorkflow(id: string) {
  try {
    await ElMessageBox.confirm(translate('Delete this workflow?'), translate('Confirm'), {
      type: 'warning',
    })
    await workflowStore.deleteWorkflow(id)
    ElMessage.success(translate('Workflow deleted'))
    await fetchWorkflows()
  } catch {
    // Cancelled
  }
}

function getStatusType(status: string) {
  const types: Record<string, any> = {
    active: 'success',
    paused: 'warning',
    draft: 'info',
    archived: 'info',
  }
  return types[status] || 'info'
}

onMounted(() => {
  fetchWorkflows()
})
</script>
