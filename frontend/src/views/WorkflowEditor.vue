<template>
  <div class="workflow-editor">
    <WorkflowToolbar
      @save="onSave"
      @execute="onExecute"
      @export="onExport"
    />

    <div class="editor-layout">
      <WorkflowCanvas
        v-model="workflowSteps"
        v-model:connections="connections"
        :skills="skills"
        @update:connections="onUpdateConnections"
      />

      <!-- WorkflowEditor右侧面板 -->
      <div class="editor-panel">
        <el-card class="properties-card">
          <template #header>
            <span>Workflow Properties</span>
          </template>

          <el-form
            :model="workflowForm"
            label-position="top"
          >
            <el-form-item label="Workflow Name">
              <el-input
                v-model="workflowForm.name"
              />
            </el-form-item>

            <el-form-item label="Description">
              <el-input
                v-model="workflowForm.description"
                type="textarea"
                placeholder="Describe this workflow..."
              />
            </el-form-item>

            <el-form-item label="Tags">
              <el-select
                v-model="workflowForm.tags"
                multiple
                filterable
                allow-create
                placeholder="Add tags..."
              >
                <el-option
                  v-for="tag in availableTags"
                  :key="tag"
                  :label="tag"
                  :value="tag"
                />
              </el-select>
            </el-form-item>
            <el-divider />

            <div class="action-buttons">
              <el-button @click="validateWorkflow">
                <el-icon><CircleCheck /></el-icon>
                Validate
              </el-button>
              <el-button @click="resetWorkflow">
                <el-icon><RefreshLeft /></el-icon>
                Reset
              </el-button>
            </div>
          </el-form>
        </el-card>
      </div>
    </div>

    <!-- Execution Panel -->
    <el-dialog
      v-model="showExecutionPanel"
      title="Execution Progress"
      width="700px"
      :close-on-click-modal="false"
    >
      <ExecutionProgress
        :execution="currentExecution"
        @pause="onPauseExecution"
        @resume="onResumeExecution"
        @cancel="onCancelExecution"
        @view-details="onViewExecutionDetails"
      />
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, computed, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'

import WorkflowToolbar from '@/components/WorkflowEditor/WorkflowToolbar.vue'
import WorkflowCanvas from '@/components/WorkflowEditor/WorkflowCanvas.vue'
import ExecutionProgress from '@/components/Monitor/ExecutionMonitor.vue'
import type { Skill, WorkflowStep } from '@/types'
import { workflowApi } from '@/api/workflow'
import { skillApi } from '@/api/skill'

const route = useRoute()

// Workflow form
interface WorkflowForm {
  name: string
  description: string
  tags: string[]
}

const workflowForm = reactive<WorkflowForm>({
  name: 'New Workflow',
  description: '',
  tags: [],
})

// Workflow data
const workflowSteps = ref<WorkflowStep[]>([])
const connections = ref<any[]>([])

const availableTags = ref([
  'outreach',
  'lead_generation',
  'customer_engagement',
  'follow_up',
  'nurturing',
])

const skills = ref<Skill[]>([])
const currentExecution = ref<any>(null)
const showExecutionPanel = ref(false)

const workflowId = computed(() => route.params.id as string)

async function loadWorkflow() {
  try {
    if (workflowId.value) {
      const workflow = await workflowApi.get(workflowId.value)
      workflowForm.name = workflow.name
      workflowForm.description = workflow.description ?? ''
      workflowForm.tags = workflow.tags || []

      // Parse steps from config
      const config = workflow.config_json
      if (config && config.steps) {
        workflowSteps.value = config.steps.map((s: any) => ({
          id: s.id,
          name: s.name,
          skillName: s.skill_name,
          skillDisplayName: s.skill_display_name,
          x: s.x || 100,
          y: s.y || 50,
          config: s.config || {},
          retryOnFailure: s.retry_on_failure ?? true,
          maxRetries: s.max_retries || 3,
          timeout: s.timeout || 300,
          condition: s.condition || 'always',
          conditionExpression: s.condition_expression,
          onFailureAction: s.on_failure_action || 'skip',
        }))
      }

      if (config && config.transitions) {
        connections.value = config.transitions.map((transition: any, index: number) => ({
          ...transition,
          id: transition.id || `transition-${index}`,
          from: transition.from || transition.from_step,
          to: transition.to || transition.to_step,
        }))
      }
    }
  } catch (error) {
    console.error('Failed to load workflow:', error)
    ElMessage.error('Failed to load workflow')
  }
}

async function loadSkills() {
  try {
    const response = await skillApi.list()
    skills.value = response.skills
  } catch (error) {
    console.error('Failed to load skills:', error)
  }
}

async function onSave() {
  if (!workflowId.value) {
    ElMessage.error('Create a workflow before opening the editor')
    return
  }
  if (!workflowForm.name.trim()) {
    ElMessage.error('Workflow name is required')
    return
  }

  const workflowData = {
    name: workflowForm.name.trim(),
    description: workflowForm.description,
    steps: workflowSteps.value.map((s) => ({
      name: s.name,
      skill_name: s.skillName,
      config: s.config,
      condition: s.condition,
      condition_expression: s.conditionExpression,
      retry_on_failure: s.retryOnFailure,
      max_retries: s.maxRetries,
      timeout: s.timeout,
      on_failure_action: s.onFailureAction,
    })),
    transitions: connections.value.map((connection) => ({
      from_step: connection.from_step || connection.from,
      to_step: connection.to_step || connection.to,
      condition: connection.condition,
    })),
    tags: workflowForm.tags,
  }

  try {
    await workflowApi.update(workflowId.value, workflowData)
    ElMessage.success('Workflow saved successfully')
  } catch (error) {
    console.error('Failed to save workflow:', error)
    ElMessage.error('Failed to save workflow')
  }
}

async function onExecute() {
  try {
    const execution = await workflowApi.execute(workflowId.value, {})
    showExecutionPanel.value = true
    currentExecution.value = {
      id: execution.execution_id,
    }
  } catch (error) {
    console.error('Failed to execute workflow:', error)
    ElMessage.error('Failed to execute workflow')
  }
}

function onExport() {
  const workflowData = {
    name: workflowForm.name,
    description: workflowForm.description,
    steps: workflowSteps.value,
    connections: connections.value,
    tags: workflowForm.tags,
  }

  const blob = new Blob([JSON.stringify(workflowData, null, 2)], { type: 'application/json' })
  const url = URL.createObjectURL(blob)

  const link = document.createElement('a')
  link.href = url
  link.download = `${workflowForm.name}.json`
  link.click()

  URL.revokeObjectURL(url)
  ElMessage.success('Workflow exported')
}

function onUpdateConnections(newConnections: any[]) {
  connections.value = newConnections
}

function validateWorkflow() {
  // Validate workflow structure
  const errors: string[] = []

  if (workflowSteps.value.length === 0) {
    errors.push('Add at least one step to the workflow')
  }

  if (!errors.length) {
    ElMessage.success('Workflow is valid')
  } else {
    ElMessage.error(`Workflow validation failed: ${errors.join(', ')}`)
  }
}

function resetWorkflow() {
  workflowSteps.value = []
  connections.value = []
  workflowForm.name = 'New Workflow'
  workflowForm.description = ''
  workflowForm.tags = []
  ElMessage.info('Workflow reset')
}

function onPauseExecution() {
  // Pause execution
}

function onResumeExecution() {
  // Resume execution
}

function onCancelExecution() {
  // Cancel execution
  showExecutionPanel.value = false
}

function onViewExecutionDetails() {
  // View execution details
}

onMounted(() => {
  loadWorkflow()
  loadSkills()
})
</script>

<style lang="scss" scoped>
.workflow-editor {
  display: flex;
  flex-direction: column;
  height: calc(100vh - 60px);
}

.editor-layout {
  display: flex;
  flex: 1;
  overflow: hidden;
}

.editor-panel {
  width: 350px;
  padding: 16px;
  border-left: 1px solid var(--el-border-color);
  background: var(--el-bg-color-page);
}

.properties-card {
  :deep(.el-card__body) {
    padding: 20px;
  }

  h4 {
    margin: 20px 0 12px;
    font-size: 14px;
    color: var(--el-text-color-secondary);
  }

  .action-buttons {
    display: flex;
    gap: 8px;
    margin-top: 20px;
  }
}
</style>
