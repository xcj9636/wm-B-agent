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
      <el-button @click="loadWorkspace">
        {{ $t('Refresh') }}
      </el-button>
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
            <el-tag
              effect="plain"
              :type="statusType(selectedProject.status)"
            >
              {{ $t(selectedProject.status) }}
            </el-tag>
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
                  <el-tag
                    size="small"
                    effect="plain"
                    :type="statusType(storyboard.status)"
                  >
                    {{ $t(storyboard.status) }}
                  </el-tag>
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
            <el-tag
              effect="plain"
              :type="statusType(selectedPersona.status)"
            >
              {{ $t(selectedPersona.status) }}
            </el-tag>
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
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import dayjs from 'dayjs'
import { videoApi } from '@/api/video'
import type {
  Paginated,
  VideoPersonaRevision,
  VideoProject,
  VideoProjectDetail,
} from '@/types/video'

type TagType = 'success' | 'warning' | 'info' | 'danger'

const loading = ref(false)
const loadError = ref(false)
const activeSection = ref<'projects' | 'personas'>('projects')
const personaResult = ref<Paginated<VideoPersonaRevision>>({ items: [], total: 0, limit: 50, offset: 0 })
const projectResult = ref<Paginated<VideoProject>>({ items: [], total: 0, limit: 50, offset: 0 })
const selectedProject = ref<VideoProjectDetail | null>(null)
const selectedPersona = ref<VideoPersonaRevision | null>(null)
const personaVersions = ref<VideoPersonaRevision[]>([])

const approvedPersonaCount = computed(
  () => personaResult.value.items.filter((item) => item.status === 'approved').length,
)
const approvedStoryboardCount = computed(
  () => selectedProject.value?.storyboards.filter((item) => item.status === 'approved').length || 0,
)

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

function statusType(value: string): TagType {
  if (value === 'approved') return 'success'
  if (value === 'draft') return 'warning'
  if (value === 'retired') return 'info'
  return 'info'
}

function formatDate(value: string) {
  return dayjs(value).format('YYYY-MM-DD HH:mm')
}

onMounted(loadWorkspace)
</script>

<style lang="scss" scoped>
.video-studio {
  min-height: calc(100dvh - 132px);
}

.studio-heading {
  align-items: flex-end;
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
  grid-template-columns: 28px minmax(0, 1fr);
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
}

@media (max-width: 640px) {
  .studio-heading {
    align-items: stretch;
  }

  .studio-heading .el-button {
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
  .persona-language {
    grid-template-columns: 1fr;
  }
}
</style>
