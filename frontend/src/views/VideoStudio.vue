<template>
  <div
    v-loading="loading"
    class="page-stack video-studio"
  >
    <div class="page-heading studio-heading">
      <div>
        <h1>{{ $t('Video Studio') }}</h1>
        <p>{{ $t('Build approved personas, evidence-bound projects and production-ready storyboards.') }}</p>
      </div>
      <div class="heading-actions">
        <el-button @click="loadWorkspace">
          {{ $t('Refresh') }}
        </el-button>
        <el-button @click="personaDialogOpen = true">
          {{ $t('New persona') }}
        </el-button>
        <el-button
          type="primary"
          :disabled="!approvedPersonas.length"
          @click="openProjectDialog"
        >
          {{ $t('New video project') }}
        </el-button>
      </div>
    </div>

    <el-alert
      v-if="loadError"
      class="load-alert"
      type="error"
      :closable="false"
      :title="$t('Video workspace could not be loaded.')"
      show-icon
    />

    <section
      class="studio-metrics"
      :aria-label="$t('Video workspace summary')"
    >
      <div>
        <strong>{{ personaResult.total }}</strong>
        <span>{{ $t('Personas') }}</span>
      </div>
      <div>
        <strong>{{ approvedPersonaCount }}</strong>
        <span>{{ $t('Approved personas') }}</span>
      </div>
      <div>
        <strong>{{ projectResult.total }}</strong>
        <span>{{ $t('Video projects') }}</span>
      </div>
      <div>
        <strong>{{ approvedStoryboardCount }}</strong>
        <span>{{ $t('Approved storyboards') }}</span>
      </div>
    </section>

    <section class="studio-workspace">
      <aside class="studio-index">
        <div
          class="index-switcher"
          role="tablist"
          :aria-label="$t('Video workspace sections')"
        >
          <button
            type="button"
            :class="{ active: activeSection === 'projects' }"
            role="tab"
            :aria-selected="activeSection === 'projects'"
            @click="activeSection = 'projects'"
          >
            {{ $t('Projects') }}
          </button>
          <button
            type="button"
            :class="{ active: activeSection === 'personas' }"
            role="tab"
            :aria-selected="activeSection === 'personas'"
            @click="activeSection = 'personas'"
          >
            {{ $t('Personas') }}
          </button>
        </div>

        <div
          v-if="activeSection === 'projects'"
          class="index-list"
        >
          <button
            v-for="project in projectResult.items"
            :key="project.id"
            type="button"
            class="index-row"
            :class="{ selected: selectedProject?.id === project.id }"
            @click="selectProject(project.id)"
          >
            <span class="row-title">{{ project.brief.title }}</span>
            <span class="row-meta">
              {{ project.brief.language }}
              <el-tag
                size="small"
                effect="plain"
                :type="statusType(project.status)"
              >
                {{ $t(project.status) }}
              </el-tag>
            </span>
          </button>
          <div
            v-if="!projectResult.items.length"
            class="empty-state compact-empty"
          >
            <strong>{{ $t('No video projects yet') }}</strong>
            <span>{{ $t('Create a project after a persona has been approved.') }}</span>
          </div>
        </div>

        <div
          v-else
          class="index-list"
        >
          <button
            v-for="persona in personaResult.items"
            :key="persona.persona_id"
            type="button"
            class="index-row"
            :class="{ selected: selectedPersona?.persona_id === persona.persona_id }"
            @click="selectPersona(persona.persona_id)"
          >
            <span class="row-title">{{ persona.spec.identity.name }}</span>
            <span class="row-meta">
              {{ $t('Revision') }} {{ persona.revision }}
              <el-tag
                size="small"
                effect="plain"
                :type="statusType(persona.status)"
              >
                {{ $t(persona.status) }}
              </el-tag>
            </span>
          </button>
          <div
            v-if="!personaResult.items.length"
            class="empty-state compact-empty"
          >
            <strong>{{ $t('No video personas yet') }}</strong>
            <span>{{ $t('Create a governed brand persona to begin planning.') }}</span>
          </div>
        </div>
      </aside>

      <main class="studio-detail">
        <template v-if="activeSection === 'projects' && selectedProject">
          <div class="detail-heading">
            <div>
              <span>{{ selectedProject.brief.language }}</span>
              <h2>{{ selectedProject.brief.title }}</h2>
              <p>{{ selectedProject.brief.objective }}</p>
            </div>
            <div class="detail-actions">
              <el-button
                size="small"
                @click="storyboardDialogOpen = true"
              >
                {{ $t('New storyboard') }}
              </el-button>
              <el-tag
                effect="plain"
                :type="statusType(selectedProject.status)"
              >
                {{ $t(selectedProject.status) }}
              </el-tag>
            </div>
          </div>

          <dl class="brief-grid">
            <div>
              <dt>{{ $t('Audience') }}</dt>
              <dd>{{ selectedProject.brief.target_audience }}</dd>
            </div>
            <div>
              <dt>{{ $t('Markets') }}</dt>
              <dd>{{ selectedProject.brief.markets.join(', ') }}</dd>
            </div>
            <div>
              <dt>{{ $t('Channels') }}</dt>
              <dd>{{ selectedProject.brief.channels.join(', ') }}</dd>
            </div>
            <div>
              <dt>{{ $t('Target duration') }}</dt>
              <dd>{{ selectedProject.brief.target_duration_seconds }}s</dd>
            </div>
          </dl>

          <section class="detail-section">
            <div class="section-heading">
              <h3>{{ $t('Storyboard revisions') }}</h3>
              <span>{{ selectedProject.storyboards.length }}</span>
            </div>
            <div
              v-if="selectedProject.storyboards.length"
              class="revision-list"
            >
              <article
                v-for="storyboard in selectedProject.storyboards"
                :key="storyboard.version_id"
                class="revision-block"
              >
                <div class="revision-heading">
                  <div>
                    <strong>{{ storyboard.storyboard.title }}</strong>
                    <span>{{ $t('Revision') }} {{ storyboard.revision }}</span>
                  </div>
                  <div class="revision-actions">
                    <el-button
                      v-if="authStore.isAdmin && storyboard.status === 'draft'"
                      size="small"
                      @click="approveStoryboard(storyboard.version_id)"
                    >
                      {{ $t('Approve') }}
                    </el-button>
                    <el-tag
                      size="small"
                      effect="plain"
                      :type="statusType(storyboard.status)"
                    >
                      {{ $t(storyboard.status) }}
                    </el-tag>
                  </div>
                </div>
                <div class="shot-strip">
                  <div
                    v-for="shot in storyboard.storyboard.shots"
                    :key="shot.shot_id || shot.sequence"
                    class="shot-item"
                  >
                    <span>{{ shot.sequence }}</span>
                    <div>
                      <strong>{{ shot.purpose }}</strong>
                      <small>{{ $t(shot.workflow_mode) }} · {{ shot.duration_seconds }}s</small>
                    </div>
                    <div
                      v-if="storyboard.status === 'approved' && shot.shot_id"
                      class="shot-actions"
                    >
                      <el-button
                        class="compile-button"
                        size="small"
                        text
                        @click="compileShot(storyboard.version_id, shot.shot_id)"
                      >
                        {{ $t('Compile shot') }}
                      </el-button>
                      <el-button
                        v-if="shot.workflow_mode === 'text_to_video'"
                        type="primary"
                        size="small"
                        :loading="generationSubmittingShotId === shot.shot_id"
                        @click="startShotGeneration(storyboard.version_id, shot.shot_id)"
                      >
                        {{ $t('Generate video') }}
                      </el-button>
                    </div>
                  </div>
                </div>
              </article>
            </div>
            <div
              v-else
              class="empty-state"
            >
              <strong>{{ $t('No storyboard revisions') }}</strong>
              <span>{{ $t('Add a storyboard to turn the approved brief into shots.') }}</span>
            </div>
          </section>

          <section class="detail-section generation-section">
            <div class="section-heading generation-heading">
              <div>
                <h3>{{ $t('Generation timeline') }}</h3>
                <span>{{ $t('Durable, resumable and filtered for this browser.') }}</span>
              </div>
              <div class="generation-heading-actions">
                <span
                  class="live-indicator"
                  :class="`is-${liveState}`"
                >
                  {{ $t(liveStateLabel) }}
                </span>
                <el-button
                  v-if="liveState === 'paused'"
                  size="small"
                  @click="resumeMediaJob"
                >
                  {{ $t('Resume live updates') }}
                </el-button>
              </div>
            </div>

            <el-alert
              v-if="streamError"
              type="warning"
              :closable="false"
              :title="$t('Live updates paused')"
              :description="$t('The job is safe. Resume to continue from the last durable event.')"
              show-icon
            />

            <div
              v-if="visibleMediaJob"
              class="job-summary"
            >
              <div>
                <span>{{ $t('Job status') }}</span>
                <el-tag
                  size="small"
                  effect="plain"
                  :type="statusType(visibleMediaJob.status)"
                >
                  {{ $t(visibleMediaJob.status) }}
                </el-tag>
              </div>
              <div>
                <span>{{ $t('Generation mode') }}</span>
                <strong>{{ $t(visibleMediaJob.mode) }}</strong>
              </div>
              <div>
                <span>{{ $t('Model') }}</span>
                <strong>{{ visibleMediaJob.model_id }}</strong>
              </div>
              <div>
                <span>{{ $t('Budget reservation ceiling') }}</span>
                <strong>{{ formatReservation(visibleMediaJob.reservation_ceiling_microusd) }}</strong>
              </div>
            </div>

            <ol
              v-if="visibleMediaJob && mediaJobEvents.length"
              class="generation-timeline"
              :aria-label="$t('Generation timeline')"
            >
              <li
                v-for="event in mediaJobEvents"
                :key="event.sequence"
              >
                <span class="timeline-dot" />
                <div>
                  <strong>{{ $t(mediaEventLabel(event.event_type)) }}</strong>
                  <small>{{ formatDate(event.created_at) }}</small>
                  <p v-if="event.data.error_code">
                    {{ $t('Error code') }}: {{ event.data.error_code }}
                  </p>
                </div>
              </li>
            </ol>
            <div
              v-else
              class="empty-state compact-empty"
            >
              <strong>{{ $t('No generation events yet') }}</strong>
              <span>{{ $t('Generate an approved text-to-video shot to start a durable job.') }}</span>
            </div>
          </section>

          <section class="detail-section">
            <div class="section-heading">
              <h3>{{ $t('Approved evidence') }}</h3>
              <span>{{ selectedProject.evidence.length }}</span>
            </div>
            <div
              v-if="selectedProject.evidence.length"
              class="evidence-grid"
            >
              <article
                v-for="evidence in selectedProject.evidence"
                :key="evidence.id"
              >
                <strong>{{ evidence.title }}</strong>
                <span>{{ evidence.source_ref }}</span>
                <small>{{ evidence.authority }} · {{ evidence.sensitivity }}</small>
              </article>
            </div>
            <div
              v-else
              class="empty-state compact-empty"
            >
              <strong>{{ $t('No evidence attached') }}</strong>
              <span>{{ $t('Claims cannot be added until approved knowledge is attached.') }}</span>
            </div>
          </section>
        </template>

        <template v-else-if="activeSection === 'personas' && selectedPersona">
          <div class="detail-heading">
            <div>
              <span>{{ selectedPersona.spec.identity.brand_name }}</span>
              <h2>{{ selectedPersona.spec.identity.name }}</h2>
              <p>{{ selectedPersona.spec.audience_segments.join(', ') }}</p>
            </div>
            <div class="detail-actions">
              <el-button
                v-if="authStore.isAdmin && selectedPersona.status === 'draft'"
                size="small"
                @click="approvePersona(selectedPersona.version_id)"
              >
                {{ $t('Approve') }}
              </el-button>
              <el-tag
                effect="plain"
                :type="statusType(selectedPersona.status)"
              >
                {{ $t(selectedPersona.status) }}
              </el-tag>
            </div>
          </div>

          <dl class="brief-grid persona-grid">
            <div>
              <dt>{{ $t('Markets') }}</dt>
              <dd>{{ selectedPersona.spec.identity.markets.join(', ') || $t('Not set') }}</dd>
            </div>
            <div>
              <dt>{{ $t('Languages') }}</dt>
              <dd>{{ selectedPersona.spec.identity.languages.join(', ') || $t('Not set') }}</dd>
            </div>
            <div>
              <dt>{{ $t('Default workflow') }}</dt>
              <dd>{{ $t(selectedPersona.spec.default_workflow) }}</dd>
            </div>
            <div>
              <dt>{{ $t('Created') }}</dt>
              <dd>{{ formatDate(selectedPersona.created_at) }}</dd>
            </div>
          </dl>

          <section class="persona-language">
            <div>
              <h3>{{ $t('Narrative') }}</h3>
              <p>{{ selectedPersona.spec.narrative.value_propositions.join(', ') }}</p>
              <small>{{ $t('Tone') }}: {{ selectedPersona.spec.narrative.tone.join(', ') }}</small>
            </div>
            <div>
              <h3>{{ $t('Visual bible') }}</h3>
              <p>{{ selectedPersona.spec.visual_bible.style.join(', ') }}</p>
              <small>{{ $t('Camera language') }}: {{ selectedPersona.spec.visual_bible.camera_language.join(', ') }}</small>
            </div>
          </section>

          <section class="detail-section">
            <div class="section-heading">
              <h3>{{ $t('Version history') }}</h3>
              <span>{{ personaVersions.length }}</span>
            </div>
            <div class="version-history">
              <div
                v-for="version in personaVersions"
                :key="version.version_id"
              >
                <div>
                  <strong>{{ $t('Revision') }} {{ version.revision }}</strong>
                  <span>{{ formatDate(version.created_at) }}</span>
                </div>
                <el-tag
                  size="small"
                  effect="plain"
                  :type="statusType(version.status)"
                >
                  {{ $t(version.status) }}
                </el-tag>
              </div>
            </div>
          </section>
        </template>

        <div
          v-else
          class="empty-state workspace-empty"
        >
          <strong>{{ $t('Select a video workspace item') }}</strong>
          <span>{{ $t('Choose a project or persona to inspect its governed production state.') }}</span>
        </div>
      </main>
    </section>

    <el-dialog
      v-model="personaDialogOpen"
      :title="$t('New video persona')"
      width="min(720px, 94vw)"
    >
      <el-form
        label-position="top"
        class="studio-form two-column-form"
        @submit.prevent="submitPersona"
      >
        <el-form-item
          :label="$t('Persona name')"
          required
        >
          <el-input
            v-model="personaForm.name"
            maxlength="160"
          />
        </el-form-item>
        <el-form-item
          :label="$t('Brand name')"
          required
        >
          <el-input
            v-model="personaForm.brandName"
            maxlength="160"
          />
        </el-form-item>
        <el-form-item
          :label="$t('Markets, comma separated')"
          required
        >
          <el-input v-model="personaForm.markets" />
        </el-form-item>
        <el-form-item
          :label="$t('Languages, comma separated')"
          required
        >
          <el-input v-model="personaForm.languages" />
        </el-form-item>
        <el-form-item
          :label="$t('Audience segments')"
          required
        >
          <el-input v-model="personaForm.audiences" />
        </el-form-item>
        <el-form-item
          :label="$t('Default workflow')"
          required
        >
          <el-select v-model="personaForm.defaultWorkflow">
            <el-option
              v-for="option in workflowOptions"
              :key="option"
              :label="$t(option)"
              :value="option"
            />
          </el-select>
        </el-form-item>
        <el-form-item
          :label="$t('Value propositions')"
          required
        >
          <el-input
            v-model="personaForm.valuePropositions"
            type="textarea"
            :rows="2"
          />
        </el-form-item>
        <el-form-item
          :label="$t('Calls to action')"
          required
        >
          <el-input
            v-model="personaForm.callsToAction"
            type="textarea"
            :rows="2"
          />
        </el-form-item>
        <el-form-item
          :label="$t('Tone')"
          required
        >
          <el-input v-model="personaForm.tone" />
        </el-form-item>
        <el-form-item :label="$t('Prohibited claims')">
          <el-input v-model="personaForm.prohibitedClaims" />
        </el-form-item>
        <el-form-item
          :label="$t('Visual style')"
          required
        >
          <el-input v-model="personaForm.visualStyle" />
        </el-form-item>
        <el-form-item
          :label="$t('Camera language')"
          required
        >
          <el-input v-model="personaForm.cameraLanguage" />
        </el-form-item>
        <button
          class="sr-only"
          type="submit"
        >
          {{ $t('Create persona') }}
        </button>
      </el-form>
      <template #footer>
        <el-button @click="personaDialogOpen = false">
          {{ $t('Cancel') }}
        </el-button>
        <el-button
          type="primary"
          :loading="submitting"
          @click="submitPersona"
        >
          {{ $t('Create persona') }}
        </el-button>
      </template>
    </el-dialog>

    <el-dialog
      v-model="projectDialogOpen"
      :title="$t('New video project')"
      width="min(720px, 94vw)"
    >
      <el-form
        label-position="top"
        class="studio-form two-column-form"
        @submit.prevent="submitProject"
      >
        <el-form-item
          class="full-field"
          :label="$t('Approved persona')"
          required
        >
          <el-select v-model="projectForm.personaVersionId">
            <el-option
              v-for="persona in approvedPersonas"
              :key="persona.version_id"
              :label="persona.spec.identity.name"
              :value="persona.version_id"
            />
          </el-select>
        </el-form-item>
        <el-form-item
          :label="$t('Project title')"
          required
        >
          <el-input
            v-model="projectForm.title"
            maxlength="200"
          />
        </el-form-item>
        <el-form-item
          :label="$t('Language')"
          required
        >
          <el-input
            v-model="projectForm.language"
            maxlength="35"
          />
        </el-form-item>
        <el-form-item
          class="full-field"
          :label="$t('Objective')"
          required
        >
          <el-input
            v-model="projectForm.objective"
            type="textarea"
            :rows="2"
          />
        </el-form-item>
        <el-form-item
          class="full-field"
          :label="$t('Product summary')"
          required
        >
          <el-input
            v-model="projectForm.productSummary"
            type="textarea"
            :rows="2"
          />
        </el-form-item>
        <el-form-item
          :label="$t('Target audience')"
          required
        >
          <el-input v-model="projectForm.targetAudience" />
        </el-form-item>
        <el-form-item
          :label="$t('Target duration')"
          required
        >
          <el-input-number
            v-model="projectForm.duration"
            :min="1"
            :max="3600"
          />
        </el-form-item>
        <el-form-item
          :label="$t('Markets, comma separated')"
          required
        >
          <el-input v-model="projectForm.markets" />
        </el-form-item>
        <el-form-item
          :label="$t('Channels, comma separated')"
          required
        >
          <el-input v-model="projectForm.channels" />
        </el-form-item>
        <el-form-item
          class="full-field"
          :label="$t('Knowledge record IDs')"
        >
          <el-input
            v-model="projectForm.evidenceRecordIds"
            type="textarea"
            :rows="2"
            :placeholder="$t('One approved knowledge record UUID per line')"
          />
        </el-form-item>
        <button
          class="sr-only"
          type="submit"
        >
          {{ $t('Create project') }}
        </button>
      </el-form>
      <template #footer>
        <el-button @click="projectDialogOpen = false">
          {{ $t('Cancel') }}
        </el-button>
        <el-button
          type="primary"
          :loading="submitting"
          @click="submitProject"
        >
          {{ $t('Create project') }}
        </el-button>
      </template>
    </el-dialog>

    <el-dialog
      v-model="storyboardDialogOpen"
      :title="$t('New storyboard revision')"
      width="min(680px, 94vw)"
    >
      <el-alert
        type="info"
        :closable="false"
        :title="$t('This first production form creates one shot. Add later revisions for additional shots.')"
        show-icon
      />
      <el-form
        label-position="top"
        class="studio-form two-column-form dialog-form-gap"
        @submit.prevent="submitStoryboard"
      >
        <el-form-item
          :label="$t('Storyboard title')"
          required
        >
          <el-input
            v-model="storyboardForm.title"
            maxlength="200"
          />
        </el-form-item>
        <el-form-item
          :label="$t('Shot purpose')"
          required
        >
          <el-input
            v-model="storyboardForm.purpose"
            maxlength="160"
          />
        </el-form-item>
        <el-form-item
          :label="$t('Workflow')"
          required
        >
          <el-select v-model="storyboardForm.workflowMode">
            <el-option
              v-for="option in textWorkflowOptions"
              :key="option"
              :label="$t(option)"
              :value="option"
            />
          </el-select>
        </el-form-item>
        <el-form-item
          :label="$t('Duration seconds')"
          required
        >
          <el-input-number
            v-model="storyboardForm.duration"
            :min="1"
            :max="120"
          />
        </el-form-item>
        <el-form-item
          class="full-field"
          :label="$t('Visual direction')"
          required
        >
          <el-input
            v-model="storyboardForm.visualPrompt"
            type="textarea"
            :rows="3"
          />
        </el-form-item>
        <el-form-item
          class="full-field"
          :label="$t('Motion direction')"
        >
          <el-input
            v-model="storyboardForm.motionPrompt"
            type="textarea"
            :rows="2"
          />
        </el-form-item>
        <el-form-item :label="$t('Spoken copy')">
          <el-input
            v-model="storyboardForm.spokenCopy"
            type="textarea"
            :rows="2"
          />
        </el-form-item>
        <el-form-item :label="$t('On-screen copy')">
          <el-input
            v-model="storyboardForm.onScreenCopy"
            type="textarea"
            :rows="2"
          />
        </el-form-item>
        <el-form-item
          class="full-field"
          :label="$t('Business claim')"
        >
          <el-input v-model="storyboardForm.businessClaim" />
        </el-form-item>
        <el-form-item
          v-if="storyboardForm.businessClaim.trim()"
          class="full-field"
          :label="$t('Claim evidence')"
          required
        >
          <el-select
            v-model="storyboardForm.claimEvidenceIds"
            multiple
          >
            <el-option
              v-for="evidence in selectedProject?.evidence || []"
              :key="evidence.id"
              :label="evidence.title"
              :value="evidence.id"
            />
          </el-select>
        </el-form-item>
        <button
          class="sr-only"
          type="submit"
        >
          {{ $t('Create storyboard') }}
        </button>
      </el-form>
      <template #footer>
        <el-button @click="storyboardDialogOpen = false">
          {{ $t('Cancel') }}
        </el-button>
        <el-button
          type="primary"
          :loading="submitting"
          @click="submitStoryboard"
        >
          {{ $t('Create storyboard') }}
        </el-button>
      </template>
    </el-dialog>

    <el-dialog
      v-model="receiptDialogOpen"
      :title="$t('Compiled shot receipt')"
      width="min(560px, 94vw)"
    >
      <div
        v-if="compiledReceipt"
        class="receipt-grid"
      >
        <div><span>{{ $t('Mode') }}</span><strong>{{ $t(compiledReceipt.mode) }}</strong></div>
        <div><span>{{ $t('Sensitivity') }}</span><strong>{{ compiledReceipt.sensitivity }}</strong></div>
        <div class="full-field">
          <span>{{ $t('Prompt hash') }}</span><code>{{ compiledReceipt.prompt_hash }}</code>
        </div>
        <div class="full-field">
          <span>{{ $t('Evidence snapshot hash') }}</span><code>{{ compiledReceipt.evidence_snapshot_hash }}</code>
        </div>
      </div>
      <el-alert
        type="success"
        :closable="false"
        :title="$t('The protected provider prompt remains on the backend.')"
        show-icon
      />
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import dayjs from 'dayjs'
import { ElMessage, ElMessageBox } from 'element-plus'
import { videoApi } from '@/api/video'
import { useMediaJobTimeline } from '@/composables/useMediaJobTimeline'
import { translate } from '@/i18n'
import { useAuthStore } from '@/stores/auth'
import type {
  CompiledShotReceipt,
  Paginated,
  VideoPersonaRevision,
  VideoWorkflowMode,
  VideoProject,
  VideoProjectDetail,
} from '@/types/video'

type TagType = 'success' | 'warning' | 'info' | 'danger'

const loading = ref(false)
const submitting = ref(false)
const loadError = ref(false)
const activeSection = ref<'projects' | 'personas'>('projects')
const personaDialogOpen = ref(false)
const projectDialogOpen = ref(false)
const storyboardDialogOpen = ref(false)
const receiptDialogOpen = ref(false)
const personaResult = ref<Paginated<VideoPersonaRevision>>({ items: [], total: 0, limit: 50, offset: 0 })
const projectResult = ref<Paginated<VideoProject>>({ items: [], total: 0, limit: 50, offset: 0 })
const selectedProject = ref<VideoProjectDetail | null>(null)
const selectedPersona = ref<VideoPersonaRevision | null>(null)
const personaVersions = ref<VideoPersonaRevision[]>([])
const compiledReceipt = ref<CompiledShotReceipt | null>(null)
const generationSubmittingShotId = ref<string | null>(null)
const authStore = useAuthStore()
const {
  mediaJob,
  mediaJobEvents,
  liveState,
  streamError,
  startMediaJob,
  restoreMediaJob,
  resumeMediaJob,
} = useMediaJobTimeline()

const workflowOptions: VideoWorkflowMode[] = [
  'auto',
  'text_to_video',
  'text_to_image_then_image_to_video',
  'image_to_video',
  'reference_to_video',
]
const textWorkflowOptions: VideoWorkflowMode[] = [
  'auto',
  'text_to_video',
  'text_to_image_then_image_to_video',
]

const personaForm = reactive({
  name: '',
  brandName: '',
  markets: '',
  languages: '',
  audiences: '',
  defaultWorkflow: 'text_to_video' as VideoWorkflowMode,
  valuePropositions: '',
  callsToAction: '',
  tone: '',
  prohibitedClaims: '',
  visualStyle: '',
  cameraLanguage: '',
})
const projectForm = reactive({
  personaVersionId: '',
  title: '',
  objective: '',
  productSummary: '',
  targetAudience: '',
  markets: '',
  channels: '',
  language: 'en-US',
  duration: 15,
  evidenceRecordIds: '',
})
const storyboardForm = reactive({
  title: '',
  purpose: '',
  workflowMode: 'text_to_video' as VideoWorkflowMode,
  duration: 8,
  visualPrompt: '',
  motionPrompt: '',
  spokenCopy: '',
  onScreenCopy: '',
  businessClaim: '',
  claimEvidenceIds: [] as string[],
})

const approvedPersonaCount = computed(
  () => personaResult.value.items.filter((item) => item.status === 'approved').length,
)
const approvedStoryboardCount = computed(
  () => selectedProject.value?.storyboards.filter((item) => item.status === 'approved').length || 0,
)
const approvedPersonas = computed(
  () => personaResult.value.items.filter((item) => item.status === 'approved'),
)
const visibleMediaJob = computed(
  () => mediaJob.value?.project_id === selectedProject.value?.id
    ? mediaJob.value
    : null,
)
const liveStateLabel = computed(() => {
  if (liveState.value === 'connecting') return 'Connecting live updates'
  if (liveState.value === 'live') return 'Live updates active'
  if (liveState.value === 'paused') return 'Live updates paused'
  if (liveState.value === 'complete') return 'Generation complete'
  return 'No active generation'
})

async function loadWorkspace() {
  loading.value = true
  loadError.value = false
  try {
    const [personas, projects] = await Promise.all([
      videoApi.listPersonas(),
      videoApi.listProjects(),
    ])
    personaResult.value = personas
    projectResult.value = projects
    if (projects.items.length) {
      await selectProject(projects.items[0].id)
    } else if (personas.items.length) {
      activeSection.value = 'personas'
      await selectPersona(personas.items[0].persona_id)
    }
  } catch {
    loadError.value = true
  } finally {
    loading.value = false
  }
}

async function selectProject(projectId: string) {
  loading.value = true
  loadError.value = false
  try {
    selectedProject.value = await videoApi.getProject(projectId)
  } catch {
    loadError.value = true
  } finally {
    loading.value = false
  }
}

async function selectPersona(personaId: string) {
  loading.value = true
  loadError.value = false
  try {
    const versions = await videoApi.listPersonaVersions(personaId)
    personaVersions.value = versions.items
    selectedPersona.value = versions.items[0] || null
  } catch {
    loadError.value = true
  } finally {
    loading.value = false
  }
}

function openProjectDialog() {
  if (!projectForm.personaVersionId) {
    projectForm.personaVersionId = approvedPersonas.value[0]?.version_id || ''
  }
  projectDialogOpen.value = true
}

async function submitPersona() {
  const required = [
    personaForm.name,
    personaForm.brandName,
    personaForm.markets,
    personaForm.languages,
    personaForm.audiences,
    personaForm.valuePropositions,
    personaForm.callsToAction,
    personaForm.tone,
    personaForm.visualStyle,
    personaForm.cameraLanguage,
  ]
  if (required.some((value) => !value.trim())) {
    ElMessage.warning(translate('Complete every required persona field.'))
    return
  }
  submitting.value = true
  try {
    const created = await videoApi.createPersona({
      idempotency_key: idempotencyKey('persona'),
      spec: {
        identity: {
          name: personaForm.name.trim(),
          brand_name: personaForm.brandName.trim(),
          markets: splitValues(personaForm.markets),
          languages: splitValues(personaForm.languages),
        },
        audience_segments: splitValues(personaForm.audiences),
        narrative: {
          tone: splitValues(personaForm.tone),
          value_propositions: splitValues(personaForm.valuePropositions),
          calls_to_action: splitValues(personaForm.callsToAction),
          prohibited_claims: splitValues(personaForm.prohibitedClaims),
        },
        visual_bible: {
          style: splitValues(personaForm.visualStyle),
          palette: [],
          camera_language: splitValues(personaForm.cameraLanguage),
          forbidden_visuals: [],
        },
        reference_asset_ids: [],
        default_workflow: personaForm.defaultWorkflow,
      },
    })
    personaDialogOpen.value = false
    await loadWorkspace()
    activeSection.value = 'personas'
    await selectPersona(created.persona_id)
    ElMessage.success(translate('Video persona created for review.'))
  } finally {
    submitting.value = false
  }
}

async function submitProject() {
  const required = [
    projectForm.personaVersionId,
    projectForm.title,
    projectForm.objective,
    projectForm.productSummary,
    projectForm.targetAudience,
    projectForm.markets,
    projectForm.channels,
    projectForm.language,
  ]
  if (required.some((value) => !value.trim())) {
    ElMessage.warning(translate('Complete every required project field.'))
    return
  }
  submitting.value = true
  try {
    const created = await videoApi.createProject({
      idempotency_key: idempotencyKey('project'),
      persona_version_id: projectForm.personaVersionId,
      brief: {
        title: projectForm.title.trim(),
        objective: projectForm.objective.trim(),
        product_summary: projectForm.productSummary.trim(),
        target_audience: projectForm.targetAudience.trim(),
        markets: splitValues(projectForm.markets),
        channels: splitValues(projectForm.channels),
        language: projectForm.language.trim(),
        target_duration_seconds: projectForm.duration,
      },
      evidence_record_ids: splitValues(projectForm.evidenceRecordIds),
    })
    projectDialogOpen.value = false
    activeSection.value = 'projects'
    await loadWorkspace()
    await selectProject(created.id)
    ElMessage.success(translate('Video project created.'))
  } finally {
    submitting.value = false
  }
}

async function submitStoryboard() {
  if (!selectedProject.value) return
  const required = [
    storyboardForm.title,
    storyboardForm.purpose,
    storyboardForm.visualPrompt,
  ]
  if (required.some((value) => !value.trim())) {
    ElMessage.warning(translate('Complete every required storyboard field.'))
    return
  }
  if (
    storyboardForm.businessClaim.trim()
    && !storyboardForm.claimEvidenceIds.length
  ) {
    ElMessage.warning(translate('Select approved evidence for every business claim.'))
    return
  }
  submitting.value = true
  try {
    await videoApi.createStoryboard(selectedProject.value.id, {
      idempotency_key: idempotencyKey('storyboard'),
      storyboard: {
        title: storyboardForm.title.trim(),
        total_duration_seconds: storyboardForm.duration,
        shots: [{
          sequence: 1,
          duration_seconds: storyboardForm.duration,
          purpose: storyboardForm.purpose.trim(),
          workflow_mode: storyboardForm.workflowMode,
          visual_prompt: storyboardForm.visualPrompt.trim(),
          motion_prompt: storyboardForm.motionPrompt.trim(),
          spoken_copy: storyboardForm.spokenCopy.trim(),
          on_screen_copy: storyboardForm.onScreenCopy.trim(),
          reference_asset_ids: [],
          business_claims: storyboardForm.businessClaim.trim()
            ? [storyboardForm.businessClaim.trim()]
            : [],
          claim_evidence_ids: storyboardForm.claimEvidenceIds,
          constraints: [],
        }],
      },
    })
    storyboardDialogOpen.value = false
    await selectProject(selectedProject.value.id)
    ElMessage.success(translate('Storyboard revision created for review.'))
  } finally {
    submitting.value = false
  }
}

async function approvePersona(versionId: string) {
  const confirmed = await confirmAction(
    translate('Approve this exact persona revision for video projects?'),
    translate('Approve persona'),
  )
  if (!confirmed) return
  await videoApi.approvePersona(versionId)
  if (selectedPersona.value) await selectPersona(selectedPersona.value.persona_id)
  personaResult.value = await videoApi.listPersonas()
  ElMessage.success(translate('Persona revision approved.'))
}

async function approveStoryboard(versionId: string) {
  if (!selectedProject.value) return
  const confirmed = await confirmAction(
    translate('Approve this exact storyboard revision for generation?'),
    translate('Approve storyboard'),
  )
  if (!confirmed) return
  await videoApi.approveStoryboard(versionId)
  await selectProject(selectedProject.value.id)
  ElMessage.success(translate('Storyboard revision approved.'))
}

async function compileShot(storyboardVersionId: string, shotId: string) {
  if (!selectedProject.value) return
  submitting.value = true
  try {
    compiledReceipt.value = await videoApi.compileShot(
      selectedProject.value.id,
      storyboardVersionId,
      shotId,
    )
    receiptDialogOpen.value = true
  } finally {
    submitting.value = false
  }
}

async function startShotGeneration(storyboardVersionId: string, shotId: string) {
  if (!selectedProject.value) return
  const confirmed = await confirmAction(
    translate('Start this approved shot? This can submit work to the configured media provider.'),
    translate('Generate video'),
  )
  if (!confirmed) return
  generationSubmittingShotId.value = shotId
  try {
    await startMediaJob(selectedProject.value.id, storyboardVersionId, shotId)
    ElMessage.success(translate('Generation job created.'))
  } finally {
    generationSubmittingShotId.value = null
  }
}

function splitValues(value: string) {
  return value
    .split(/[\n,]/)
    .map((item) => item.trim())
    .filter(Boolean)
}

function idempotencyKey(scope: string) {
  return `video:${scope}:${crypto.randomUUID()}`
}

async function confirmAction(message: string, title: string) {
  try {
    await ElMessageBox.confirm(message, title, {
      confirmButtonText: translate('Approve'),
      cancelButtonText: translate('Cancel'),
      type: 'warning',
    })
    return true
  } catch {
    return false
  }
}

function statusType(value: string): TagType {
  if (['approved', 'succeeded'].includes(value)) return 'success'
  if (['draft', 'queued', 'running', 'submitting', 'submitted'].includes(value)) return 'warning'
  if (['failed', 'submission_unknown'].includes(value)) return 'danger'
  if (['retired', 'cancelled', 'cancel_requested'].includes(value)) return 'info'
  return 'info'
}

function mediaEventLabel(eventType: string) {
  const labels: Record<string, string> = {
    'job.created': 'Generation job created',
    'job.claimed': 'Generation job claimed',
    'job.requeued': 'Generation job requeued',
    'job.cancelled': 'Generation cancelled',
    'job.cancel_requested': 'Cancellation requested',
    'job.succeeded': 'Generation succeeded',
    'job.failed': 'Generation failed',
    'submission.started': 'Provider submission started',
    'submission.accepted': 'Provider submission accepted',
    'submission.unknown': 'Provider submission needs review',
    'submission.manually_confirmed': 'Provider submission manually confirmed',
    'submission.not_created_confirmed': 'Provider submission confirmed absent',
  }
  return labels[eventType] || 'Generation state updated'
}

function formatReservation(microusd: number) {
  return `$${(microusd / 1_000_000).toFixed(2)} USD`
}

function formatDate(value: string) {
  return dayjs(value).format('YYYY-MM-DD HH:mm')
}

onMounted(async () => {
  await loadWorkspace()
  const restored = await restoreMediaJob()
  if (restored && restored.project_id !== selectedProject.value?.id) {
    await selectProject(restored.project_id)
  }
})
</script>

<style lang="scss" scoped>
.video-studio {
  min-height: calc(100dvh - 132px);
}

.studio-heading {
  align-items: flex-end;
}

.heading-actions,
.detail-actions,
.revision-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

.heading-actions {
  flex-wrap: wrap;
  justify-content: flex-end;
}

.studio-heading p {
  max-width: 720px;
}

.load-alert {
  border-radius: 12px;
}

.studio-metrics {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  border-top: 1px solid var(--border-hairline);
  border-bottom: 1px solid var(--border-hairline);
}

.studio-metrics > div {
  display: grid;
  gap: 4px;
  padding: 18px 20px;
}

.studio-metrics > div + div {
  border-left: 1px solid var(--border-hairline);
}

.studio-metrics strong {
  color: var(--text-primary);
  font-size: 24px;
  font-weight: 640;
  letter-spacing: -0.04em;
}

.studio-metrics span,
.row-meta,
.detail-heading span,
.revision-heading span,
.section-heading span,
.evidence-grid span,
.evidence-grid small,
.persona-language small,
.version-history span {
  color: var(--text-secondary);
  font-size: 12px;
}

.studio-workspace {
  display: grid;
  min-height: 620px;
  grid-template-columns: minmax(240px, 0.32fr) minmax(0, 1fr);
  overflow: hidden;
  border: 1px solid var(--border-hairline);
  border-radius: 14px;
  background: var(--surface-elevated);
}

.studio-index {
  min-width: 0;
  border-right: 1px solid var(--border-hairline);
  background: var(--surface-sidebar);
}

.index-switcher {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 4px;
  margin: 12px;
  padding: 3px;
  border-radius: 10px;
  background: var(--surface-canvas);
}

.index-switcher button,
.index-row {
  border: 0;
  color: var(--text-secondary);
  background: transparent;
  font: inherit;
  cursor: pointer;
}

.index-switcher button {
  min-height: 34px;
  border-radius: 8px;
  font-size: 13px;
  font-weight: 580;
}

.index-switcher button:hover,
.index-switcher button:focus-visible,
.index-switcher button.active {
  color: var(--text-primary);
  background: var(--surface-selected);
  outline: none;
}

.index-list {
  display: grid;
  align-content: start;
  gap: 3px;
  padding: 0 8px 12px;
}

.index-row {
  display: grid;
  gap: 7px;
  width: 100%;
  padding: 12px;
  border-radius: 10px;
  text-align: left;
}

.index-row:hover,
.index-row:focus-visible,
.index-row.selected {
  color: var(--text-primary);
  background: var(--surface-selected);
  outline: none;
}

.row-title {
  overflow: hidden;
  font-size: 13px;
  font-weight: 590;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.row-meta,
.revision-heading,
.section-heading,
.version-history > div {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
}

.studio-detail {
  min-width: 0;
  padding: clamp(22px, 4vw, 48px);
}

.detail-heading {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 24px;
}

.detail-heading h2 {
  margin: 4px 0 8px;
  color: var(--text-primary);
  font-size: clamp(24px, 3vw, 34px);
  font-weight: 640;
  letter-spacing: -0.04em;
}

.detail-heading p {
  max-width: 760px;
  margin: 0;
  color: var(--text-secondary);
  line-height: 1.65;
}

.brief-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 20px;
  margin: 32px 0 38px;
}

.brief-grid div {
  min-width: 0;
}

.brief-grid dt {
  margin-bottom: 7px;
  color: var(--text-tertiary);
  font-size: 11px;
}

.brief-grid dd {
  margin: 0;
  color: var(--text-primary);
  font-size: 13px;
  line-height: 1.5;
}

.detail-section + .detail-section {
  margin-top: 34px;
}

.section-heading {
  margin-bottom: 14px;
}

.section-heading h3,
.persona-language h3 {
  margin: 0;
  color: var(--text-primary);
  font-size: 15px;
  font-weight: 620;
}

.revision-list,
.version-history {
  display: grid;
  gap: 10px;
}

.revision-block {
  padding: 16px;
  border: 1px solid var(--border-hairline);
  border-radius: 12px;
}

.revision-heading > div {
  display: grid;
  gap: 3px;
}

.shot-strip {
  display: grid;
  gap: 8px;
  margin-top: 14px;
}

.shot-item {
  display: grid;
  grid-template-columns: 28px minmax(0, 1fr) auto;
  align-items: center;
  gap: 10px;
}

.shot-item > span {
  display: grid;
  width: 28px;
  height: 28px;
  place-items: center;
  border-radius: 8px;
  color: var(--text-secondary);
  background: var(--surface-selected);
  font-size: 12px;
}

.shot-item div {
  display: grid;
  gap: 2px;
}

.shot-item strong {
  color: var(--text-primary);
  font-size: 13px;
}

.shot-item small {
  color: var(--text-tertiary);
  font-size: 11px;
}

.shot-actions,
.generation-heading-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

.generation-heading > div:first-child {
  display: grid;
  gap: 4px;
}

.live-indicator {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  color: var(--text-tertiary);
  font-size: 11px;
}

.live-indicator::before {
  width: 7px;
  height: 7px;
  border-radius: 999px;
  background: var(--text-tertiary);
  content: '';
}

.live-indicator.is-live::before,
.live-indicator.is-complete::before {
  background: var(--el-color-success);
}

.live-indicator.is-connecting::before {
  background: var(--el-color-warning);
}

.live-indicator.is-paused::before {
  background: var(--el-color-danger);
}

.job-summary {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 1px;
  margin-top: 14px;
  overflow: hidden;
  border: 1px solid var(--border-hairline);
  border-radius: 12px;
  background: var(--border-hairline);
}

.job-summary > div {
  display: grid;
  gap: 7px;
  min-width: 0;
  padding: 14px;
  background: var(--surface-elevated);
}

.job-summary span,
.generation-timeline small {
  color: var(--text-tertiary);
  font-size: 11px;
}

.job-summary strong {
  overflow: hidden;
  color: var(--text-primary);
  font-size: 12px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.generation-timeline {
  display: grid;
  gap: 0;
  margin: 18px 0 0;
  padding: 0;
  list-style: none;
}

.generation-timeline li {
  position: relative;
  display: grid;
  grid-template-columns: 18px minmax(0, 1fr);
  gap: 10px;
  min-height: 58px;
}

.generation-timeline li:not(:last-child)::before {
  position: absolute;
  top: 13px;
  bottom: -1px;
  left: 4px;
  width: 1px;
  background: var(--border-hairline);
  content: '';
}

.timeline-dot {
  z-index: 1;
  width: 9px;
  height: 9px;
  margin-top: 4px;
  border: 2px solid var(--surface-elevated);
  border-radius: 999px;
  background: var(--text-secondary);
  box-shadow: 0 0 0 1px var(--border-hairline);
}

.generation-timeline li > div {
  display: grid;
  gap: 3px;
  padding-bottom: 14px;
}

.generation-timeline strong {
  color: var(--text-primary);
  font-size: 13px;
}

.generation-timeline p {
  margin: 2px 0 0;
  color: var(--el-color-danger);
  font-size: 11px;
}

.evidence-grid,
.persona-language {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}

.evidence-grid article,
.persona-language > div {
  display: grid;
  gap: 6px;
  padding: 16px;
  border: 1px solid var(--border-hairline);
  border-radius: 12px;
}

.evidence-grid strong {
  overflow: hidden;
  color: var(--text-primary);
  font-size: 13px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.persona-language {
  margin: 0 0 36px;
}

.persona-language p {
  margin: 2px 0 0;
  color: var(--text-secondary);
  font-size: 13px;
  line-height: 1.6;
}

.version-history > div {
  min-height: 48px;
  padding: 8px 0;
  border-bottom: 1px solid var(--border-hairline);
}

.version-history > div > div {
  display: grid;
  gap: 3px;
}

.version-history strong {
  color: var(--text-primary);
  font-size: 13px;
}

.empty-state {
  display: grid;
  min-height: 150px;
  place-content: center;
  gap: 6px;
  padding: 24px;
  color: var(--text-secondary);
  text-align: center;
}

.empty-state strong {
  color: var(--text-primary);
  font-size: 14px;
}

.empty-state span {
  max-width: 360px;
  font-size: 12px;
  line-height: 1.6;
}

.compact-empty {
  min-height: 120px;
}

.workspace-empty {
  min-height: 520px;
}

.studio-form {
  margin-top: 8px;
}

.two-column-form {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 0 16px;
}

.two-column-form .full-field {
  grid-column: 1 / -1;
}

.receipt-grid .full-field {
  grid-column: 1 / -1;
}

.studio-form :deep(.el-select),
.studio-form :deep(.el-input-number) {
  width: 100%;
}

.dialog-form-gap {
  margin-top: 18px;
}

.receipt-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
  margin-bottom: 18px;
}

.receipt-grid > div {
  display: grid;
  gap: 5px;
  padding: 12px;
  border: 1px solid var(--border-hairline);
  border-radius: 10px;
}

.receipt-grid span {
  color: var(--text-tertiary);
  font-size: 11px;
}

.receipt-grid strong,
.receipt-grid code {
  color: var(--text-primary);
  font-size: 12px;
}

.receipt-grid code {
  overflow-wrap: anywhere;
}

.sr-only {
  position: absolute;
  width: 1px;
  height: 1px;
  overflow: hidden;
  clip: rect(0, 0, 0, 0);
  white-space: nowrap;
}

@media (max-width: 980px) {
  .studio-metrics {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .studio-metrics > div:nth-child(3) {
    border-left: 0;
  }

  .studio-metrics > div:nth-child(n + 3) {
    border-top: 1px solid var(--border-hairline);
  }

  .studio-workspace {
    grid-template-columns: 1fr;
  }

  .studio-index {
    border-right: 0;
    border-bottom: 1px solid var(--border-hairline);
  }

  .index-list {
    grid-auto-columns: minmax(220px, 72vw);
    grid-auto-flow: column;
    overflow-x: auto;
  }

  .brief-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .job-summary {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 640px) {
  .studio-heading {
    align-items: stretch;
  }

  .studio-heading .el-button {
    flex: 1;
  }

  .heading-actions {
    width: 100%;
  }

  .studio-metrics > div {
    padding: 14px;
  }

  .studio-detail {
    padding: 20px 16px 28px;
  }

  .detail-heading {
    flex-direction: column;
    gap: 12px;
  }

  .brief-grid,
  .evidence-grid,
  .persona-language,
  .two-column-form,
  .receipt-grid {
    grid-template-columns: 1fr;
  }


  .job-summary {
    grid-template-columns: 1fr;
  }

  .shot-item {
    grid-template-columns: 28px minmax(0, 1fr);
  }

  .shot-actions {
    grid-column: 2;
    flex-wrap: wrap;
  }

  .two-column-form .full-field,
  .receipt-grid .full-field {
    grid-column: 1;
  }
}
</style>
