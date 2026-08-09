<template>
  <header class="workflow-toolbar apple-glass">
    <div class="toolbar-identity">
      <el-button
        circle
        text
        :aria-label="$t('Back to workflows')"
        @click="emit('back')"
      >
        <el-icon><ArrowLeft /></el-icon>
      </el-button>
      <div>
        <strong>{{ title || $t('Workflow editor') }}</strong>
        <span>{{ $t('Visual automation canvas') }}</span>
      </div>
    </div>

    <div class="toolbar-actions">
      <el-button @click="emit('export')">
        <el-icon><Download /></el-icon>
        {{ $t('Export') }}
      </el-button>
      <el-button @click="emit('execute')">
        <el-icon><VideoPlay /></el-icon>
        {{ $t('Execute') }}
      </el-button>
      <el-button
        type="primary"
        @click="emit('save')"
      >
        <el-icon><DocumentChecked /></el-icon>
        {{ $t('Save') }}
      </el-button>
    </div>
  </header>
</template>

<script setup lang="ts">
defineProps<{ title?: string }>()
const emit = defineEmits<{
  back: []
  save: []
  execute: []
  export: []
}>()
</script>

<style lang="scss" scoped>
.workflow-toolbar {
  display: flex;
  min-height: 62px;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 9px 12px;
  border-width: 0 0 1px;
  border-radius: 0;
  background: var(--surface-glass);
  box-shadow: inset 0 1px 0 var(--border-highlight);
}

.toolbar-identity,
.toolbar-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

.toolbar-identity > div {
  display: grid;
  gap: 2px;
}

.toolbar-identity strong {
  max-width: 360px;
  overflow: hidden;
  font-size: 13px;
  font-weight: 650;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.toolbar-identity span {
  color: var(--text-secondary);
  font-size: 11px;
}

@media (max-width: 680px) {
  .workflow-toolbar { align-items: stretch; flex-direction: column; }
  .toolbar-actions { display: grid; grid-template-columns: repeat(3, 1fr); }
  .toolbar-actions .el-button { margin: 0; }
}
</style>
