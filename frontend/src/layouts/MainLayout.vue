<template>
  <div class="console-shell" :class="{ 'is-collapsed': isCollapsed, 'mobile-open': mobileOpen }">
    <button
      v-if="mobileOpen"
      type="button"
      class="mobile-backdrop"
      aria-label="Close navigation"
      @click="mobileOpen = false"
    />

    <aside class="console-sidebar">
      <div class="brand">
        <div class="brand-mark">B</div>
        <div v-if="!isCollapsed" class="brand-copy">
          <strong>B-agent</strong>
          <span>Revenue operations</span>
        </div>
      </div>

      <el-menu
        :default-active="activeMenu"
        :router="true"
        :collapse="isCollapsed"
        class="console-menu"
      >
        <el-menu-item index="/dashboard">
          <el-icon><Odometer /></el-icon>
          <template #title>Overview</template>
        </el-menu-item>

        <el-menu-item-group title="Automation">
          <el-menu-item index="/workflows">
            <el-icon><Operation /></el-icon>
            <template #title>Workflows</template>
          </el-menu-item>
          <el-menu-item index="/skills">
            <el-icon><SetUp /></el-icon>
            <template #title>Skills</template>
          </el-menu-item>
        </el-menu-item-group>

        <el-menu-item-group title="Relationships">
          <el-menu-item index="/customers">
            <el-icon><User /></el-icon>
            <template #title>Customers</template>
          </el-menu-item>
          <el-menu-item index="/conversations">
            <el-icon><ChatDotRound /></el-icon>
            <template #title>Conversations</template>
          </el-menu-item>
        </el-menu-item-group>

        <el-menu-item index="/analytics">
          <el-icon><TrendCharts /></el-icon>
          <template #title>Analytics</template>
        </el-menu-item>

        <el-menu-item-group v-if="authStore.isAdmin" title="Administration">
          <el-menu-item index="/operations">
            <el-icon><Monitor /></el-icon>
            <template #title>Operations</template>
          </el-menu-item>
          <el-menu-item index="/operations/dead-letters">
            <el-icon><WarningFilled /></el-icon>
            <template #title>Dead Letters</template>
          </el-menu-item>
        </el-menu-item-group>

        <el-menu-item index="/settings">
          <el-icon><Setting /></el-icon>
          <template #title>Settings</template>
        </el-menu-item>
      </el-menu>

      <button
        type="button"
        class="collapse-control"
        :aria-label="isCollapsed ? 'Expand navigation' : 'Collapse navigation'"
        @click="isCollapsed = !isCollapsed"
      >
        <el-icon><Expand v-if="isCollapsed" /><Fold v-else /></el-icon>
        <span v-if="!isCollapsed">Collapse</span>
      </button>
    </aside>

    <div class="console-content">
      <header class="console-header">
        <div class="header-title">
          <el-button
            class="mobile-menu-button"
            text
            aria-label="Open navigation"
            @click="mobileOpen = true"
          >
            <el-icon><Menu /></el-icon>
          </el-button>
          <div>
            <span>Workspace</span>
            <strong>{{ currentTitle }}</strong>
          </div>
        </div>

        <div class="header-actions">
          <el-tooltip content="Toggle color theme" placement="bottom">
            <el-button
              circle
              text
              :aria-label="isDark ? 'Use light theme' : 'Use dark theme'"
              @click="themeStore.toggleTheme()"
            >
              <el-icon><Moon v-if="!isDark" /><Sunny v-else /></el-icon>
            </el-button>
          </el-tooltip>

          <el-dropdown trigger="click">
            <button type="button" class="user-control">
              <el-avatar :size="32">{{ userInitial }}</el-avatar>
              <span class="user-copy">
                <strong>{{ authStore.user?.username }}</strong>
                <small>{{ authStore.isAdmin ? 'Administrator' : authStore.user?.role }}</small>
              </span>
              <el-icon><ArrowDown /></el-icon>
            </button>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item @click="router.push('/settings')">Settings</el-dropdown-item>
                <el-dropdown-item divided @click="logout">Sign out</el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
        </div>
      </header>

      <main class="console-main">
        <RouterView />
      </main>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import { useThemeStore } from '@/stores/theme'

const route = useRoute()
const router = useRouter()
const authStore = useAuthStore()
const themeStore = useThemeStore()
const isCollapsed = ref(false)
const mobileOpen = ref(false)

const activeMenu = computed(() => route.path)
const currentTitle = computed(() => String(route.meta.title || 'B-agent'))
const isDark = computed(() => themeStore.isDark)
const userInitial = computed(() => authStore.user?.username?.charAt(0).toUpperCase() || 'U')

watch(() => route.fullPath, () => {
  mobileOpen.value = false
})

async function logout() {
  await authStore.logout()
  await router.push('/login')
}
</script>

<style lang="scss" scoped>
.console-shell {
  min-height: 100dvh;
  background: var(--el-bg-color-page);
}

.console-sidebar {
  position: fixed;
  inset: 0 auto 0 0;
  z-index: 30;
  display: flex;
  width: 232px;
  flex-direction: column;
  border-right: 1px solid var(--el-border-color-lighter);
  background: var(--el-bg-color);
  transition: width 180ms ease, transform 180ms ease;
}

.brand {
  display: flex;
  min-height: 64px;
  align-items: center;
  gap: 11px;
  padding: 0 16px;
  border-bottom: 1px solid var(--el-border-color-lighter);
}

.brand-mark {
  display: grid;
  width: 34px;
  height: 34px;
  flex: 0 0 auto;
  place-items: center;
  border-radius: 8px;
  color: #f7fbff;
  background: #255c99;
  font-weight: 800;
}

.brand-copy {
  display: grid;
  min-width: 0;
  gap: 1px;
}

.brand-copy strong {
  font-size: 15px;
}

.brand-copy span {
  overflow: hidden;
  color: var(--el-text-color-secondary);
  font-size: 11px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.console-menu {
  flex: 1;
  overflow-y: auto;
  border-right: 0;
  padding: 10px 8px;
}

.console-menu:not(.el-menu--collapse) {
  width: 100%;
}

.collapse-control {
  display: flex;
  min-height: 48px;
  align-items: center;
  gap: 10px;
  padding: 0 20px;
  border: 0;
  border-top: 1px solid var(--el-border-color-lighter);
  color: var(--el-text-color-secondary);
  background: transparent;
  cursor: pointer;
}

.collapse-control:hover,
.collapse-control:focus-visible {
  color: var(--el-color-primary);
  outline: none;
}

.console-content {
  min-width: 0;
  margin-left: 232px;
  transition: margin-left 180ms ease;
}

.console-header {
  position: sticky;
  top: 0;
  z-index: 20;
  display: flex;
  min-height: 64px;
  align-items: center;
  justify-content: space-between;
  gap: 20px;
  padding: 0 24px;
  border-bottom: 1px solid var(--el-border-color-lighter);
  background: color-mix(in srgb, var(--el-bg-color) 94%, transparent);
  backdrop-filter: blur(12px);
}

.header-title,
.header-actions,
.user-control {
  display: flex;
  align-items: center;
}

.header-title > div {
  display: grid;
  gap: 2px;
}

.header-title span,
.user-copy small {
  color: var(--el-text-color-secondary);
  font-size: 11px;
}

.header-title strong {
  font-size: 15px;
}

.header-actions {
  gap: 8px;
}

.user-control {
  gap: 9px;
  padding: 4px 6px;
  border: 0;
  border-radius: var(--console-radius);
  color: var(--el-text-color-primary);
  background: transparent;
  font: inherit;
  cursor: pointer;
}

.user-control:hover,
.user-control:focus-visible {
  background: var(--el-fill-color-light);
  outline: none;
}

.user-copy {
  display: grid;
  min-width: 100px;
  text-align: left;
}

.user-copy strong {
  font-size: 13px;
}

.console-main {
  width: min(100%, 1520px);
  margin: 0 auto;
  padding: 24px;
}

.is-collapsed .console-sidebar {
  width: 72px;
}

.is-collapsed .console-content {
  margin-left: 72px;
}

.is-collapsed .collapse-control {
  justify-content: center;
  padding: 0;
}

.mobile-menu-button,
.mobile-backdrop {
  display: none;
}

@media (max-width: 820px) {
  .console-sidebar {
    width: min(280px, 84vw);
    transform: translateX(-100%);
  }

  .mobile-open .console-sidebar {
    transform: translateX(0);
  }

  .console-content,
  .is-collapsed .console-content {
    margin-left: 0;
  }

  .mobile-menu-button,
  .mobile-open .mobile-backdrop {
    display: inline-flex;
  }

  .mobile-backdrop {
    position: fixed;
    inset: 0;
    z-index: 25;
    border: 0;
    background: rgb(15 23 42 / 0.38);
  }

  .collapse-control {
    display: none;
  }

  .console-main {
    padding: 18px 14px;
  }

  .console-header {
    padding: 0 12px;
  }

  .user-copy {
    display: none;
  }
}
</style>
