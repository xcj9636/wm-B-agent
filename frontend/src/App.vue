<template>
  <el-config-provider :locale="elementLocale">
    <div
      id="app"
      :class="{ dark: isDark }"
    >
      <RouterView v-if="authStore.isAuthenticated" />
      <div
        v-else
        class="auth-wrapper"
      >
        <RouterView />
      </div>
    </div>
  </el-config-provider>
</template>

<script setup lang="ts">
import { computed, onMounted, watchEffect } from 'vue'
import { useRoute } from 'vue-router'
import { useAuthStore } from './stores/auth'
import { useThemeStore } from './stores/theme'
import { elementLocale, locale, translate } from './i18n'

const route = useRoute()
const authStore = useAuthStore()
const themeStore = useThemeStore()

const isDark = computed(() => themeStore.isDark)

watchEffect(() => {
  const title = String(route.meta.title || 'B-agent')
  document.title = `${translate(title)} - B-agent`
  document.documentElement.lang = locale.value
})

onMounted(() => {
  themeStore.initialize()
})
</script>

<style lang="scss">
#app {
  min-height: 100dvh;
}

.auth-wrapper {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 100dvh;
  background: var(--surface-canvas);
}
</style>
