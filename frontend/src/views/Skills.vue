<template>
  <section class="skills page-stack" aria-labelledby="skills-title">
    <header class="page-heading">
      <div>
        <p class="page-kicker">Automation catalog</p>
        <h1 id="skills-title">Skills</h1>
        <p>Inspect every registered capability available to workflow authors.</p>
      </div>
      <el-button :loading="loading" aria-label="Refresh skills" @click="loadSkills">
        <el-icon><Refresh /></el-icon>
        Refresh
      </el-button>
    </header>

    <div class="filters">
      <el-input v-model="query" clearable placeholder="Search skills" aria-label="Search skills">
        <template #prefix><el-icon><Search /></el-icon></template>
      </el-input>
      <el-select v-model="category" clearable placeholder="All categories" aria-label="Filter skills">
        <el-option v-for="item in categories" :key="item" :label="item" :value="item" />
      </el-select>
      <span aria-live="polite">{{ filteredSkills.length }} capabilities</span>
    </div>

    <div v-loading="loading" class="skill-grid">
      <button
        v-for="skill in filteredSkills"
        :key="skill.name"
        type="button"
        class="skill-item"
        @click="selectedSkill = skill"
      >
        <span class="skill-category">{{ skill.category }}</span>
        <strong>{{ skill.displayName }}</strong>
        <p>{{ skill.description }}</p>
        <span class="skill-meta">{{ skill.name }} / {{ skill.version }}</span>
      </button>
    </div>
    <el-empty v-if="!loading && filteredSkills.length === 0" description="No skills match the filter" />

    <el-drawer v-model="drawerOpen" title="Skill contract" size="min(520px, 92vw)">
      <template v-if="selectedSkill">
        <el-descriptions :column="1" border>
          <el-descriptions-item label="Display name">{{ selectedSkill.displayName }}</el-descriptions-item>
          <el-descriptions-item label="Registry name"><code>{{ selectedSkill.name }}</code></el-descriptions-item>
          <el-descriptions-item label="Category">{{ selectedSkill.category }}</el-descriptions-item>
          <el-descriptions-item label="Version">{{ selectedSkill.version }}</el-descriptions-item>
        </el-descriptions>
        <h3>Input schema</h3>
        <pre>{{ formatSchema(selectedSkill.inputSchema) }}</pre>
        <h3>Output schema</h3>
        <pre>{{ formatSchema(selectedSkill.outputSchema) }}</pre>
        <h3>Configuration</h3>
        <pre>{{ formatSchema(selectedSkill.configTemplate || {}) }}</pre>
      </template>
    </el-drawer>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { skillApi } from '@/api/skill'
import type { Skill } from '@/types'

const loading = ref(false)
const skills = ref<Skill[]>([])
const categories = ref<string[]>([])
const query = ref('')
const category = ref('')
const selectedSkill = ref<Skill | null>(null)
const drawerOpen = computed({
  get: () => selectedSkill.value !== null,
  set: (value) => {
    if (!value) selectedSkill.value = null
  },
})

const filteredSkills = computed(() => {
  const needle = query.value.trim().toLowerCase()
  return skills.value.filter((skill) => {
    const matchesCategory = !category.value || skill.category === category.value
    const matchesQuery = !needle || [skill.name, skill.displayName, skill.description]
      .some((value) => value.toLowerCase().includes(needle))
    return matchesCategory && matchesQuery
  })
})

async function loadSkills() {
  loading.value = true
  try {
    const result = await skillApi.list()
    skills.value = result.skills
    categories.value = result.categories
  } finally {
    loading.value = false
  }
}

function formatSchema(schema: Record<string, any>) {
  return JSON.stringify(schema, null, 2)
}

onMounted(loadSkills)
</script>

<style scoped lang="scss">
.filters {
  display: grid;
  grid-template-columns: minmax(220px, 1fr) 220px auto;
  align-items: center;
  gap: 12px;
}

.filters > span {
  color: var(--el-text-color-secondary);
  font-size: 13px;
  text-align: right;
}

.skill-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
  min-height: 120px;
}

.skill-item {
  display: grid;
  gap: 8px;
  padding: 18px;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: var(--console-radius);
  color: var(--el-text-color-primary);
  background: var(--el-bg-color);
  font: inherit;
  text-align: left;
  cursor: pointer;
  transition: border-color 150ms ease, transform 150ms ease;
}

.skill-item:hover,
.skill-item:focus-visible {
  border-color: var(--el-color-primary);
  outline: none;
  transform: translateY(-1px);
}

.skill-category,
.skill-meta {
  color: var(--el-text-color-secondary);
  font-size: 12px;
}

.skill-item p {
  margin: 0;
  color: var(--el-text-color-regular);
  line-height: 1.55;
}

h3 {
  margin: 24px 0 10px;
  font-size: 14px;
}

pre {
  overflow: auto;
  max-height: 260px;
  padding: 14px;
  border-radius: calc(var(--console-radius) - 2px);
  background: var(--el-fill-color-light);
  font-size: 12px;
}

@media (max-width: 760px) {
  .filters,
  .skill-grid {
    grid-template-columns: 1fr;
  }

  .filters > span {
    text-align: left;
  }
}
</style>
