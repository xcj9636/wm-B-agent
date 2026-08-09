<template>
  <div
    class="console-shell"
    :class="{ 'is-collapsed': isCollapsed, 'mobile-open': mobileOpen }"
  >
    <button
      v-if="mobileOpen"
      type="button"
      class="mobile-backdrop"
      :aria-label="$t('Close navigation')"
      @click="mobileOpen = false"
    />

    <aside class="console-sidebar sidebar-material apple-glass">
      <div class="sidebar-titlebar">
        <div
          class="window-controls"
          aria-hidden="true"
        >
          <span class="window-control window-control--close" />
          <span class="window-control window-control--minimize" />
          <span class="window-control window-control--expand" />
        </div>
        <div
          v-if="!isCollapsed"
          class="brand"
        >
          <img
            class="brand-mark"
            src="/b-agent-logo.svg"
            alt=""
          >
          <div class="brand-copy">
            <strong>B-agent</strong>
            <span>{{ $t('Revenue operations') }}</span>
          </div>
        </div>
      </div>

      <el-menu
        :default-active="activeMenu"
        :router="true"
        :collapse="isCollapsed"
        class="console-menu"
      >
        <el-menu-item index="/agent">
          <el-icon><MagicStick /></el-icon>
          <template #title>
            {{ $t('Agent Center') }}
          </template>
        </el-menu-item>

        <el-menu-item index="/ai-chat">
          <el-icon><ChatLineRound /></el-icon>
          <template #title>
            {{ $t('AI Chat') }}
          </template>
        </el-menu-item>

        <el-menu-item index="/dashboard">
          <el-icon><Odometer /></el-icon>
          <template #title>
            {{ $t('Overview') }}
          </template>
        </el-menu-item>

        <el-menu-item-group :title="$t('Automation')">
          <el-menu-item index="/workflows">
            <el-icon><Operation /></el-icon>
            <template #title>
              {{ $t('Workflows') }}
            </template>
          </el-menu-item>
          <el-menu-item index="/skills">
            <el-icon><SetUp /></el-icon>
            <template #title>
              {{ $t('Skills') }}
            </template>
          </el-menu-item>
        </el-menu-item-group>

        <el-menu-item-group :title="$t('Relationships')">
          <el-menu-item index="/customers">
            <el-icon><User /></el-icon>
            <template #title>
              {{ $t('Customers') }}
            </template>
          </el-menu-item>
          <el-menu-item index="/conversations">
            <el-icon><ChatDotRound /></el-icon>
            <template #title>
              {{ $t('Conversations') }}
            </template>
          </el-menu-item>
        </el-menu-item-group>

        <el-menu-item index="/analytics">
          <el-icon><TrendCharts /></el-icon>
          <template #title>
            {{ $t('Analytics') }}
          </template>
        </el-menu-item>

        <el-menu-item-group
          v-if="authStore.isAdmin"
          :title="$t('Administration')"
        >
          <el-menu-item index="/operations">
            <el-icon><Monitor /></el-icon>
            <template #title>
              {{ $t('Operations') }}
            </template>
          </el-menu-item>
          <el-menu-item index="/operations/dead-letters">
            <el-icon><WarningFilled /></el-icon>
            <template #title>
              {{ $t('Dead Letters') }}
            </template>
          </el-menu-item>
        </el-menu-item-group>

        <el-menu-item index="/settings">
          <el-icon><Setting /></el-icon>
          <template #title>
            {{ $t('Settings') }}
          </template>
        </el-menu-item>
      </el-menu>

      <button
        type="button"
        class="collapse-control"
        :aria-label="$t(isCollapsed ? 'Expand navigation' : 'Collapse navigation')"
        @click="isCollapsed = !isCollapsed"
      >
        <el-icon><Expand v-if="isCollapsed" /><Fold v-else /></el-icon>
        <span v-if="!isCollapsed">{{ $t('Collapse') }}</span>
      </button>
    </aside>

    <div class="console-content">
      <header class="console-header">
        <div class="header-title">
          <el-button
            class="mobile-menu-button"
            text
            :aria-label="$t('Open navigation')"
            @click="mobileOpen = true"
          >
            <el-icon><Menu /></el-icon>
          </el-button>
          <div>
            <span>{{ $t('Workspace') }}</span>
            <strong>{{ currentTitle }}</strong>
          </div>
        </div>

        <div class="header-actions">
          <el-tooltip
            :content="$t(locale === 'zh-CN' ? 'Switch to English' : 'Switch to Chinese')"
            placement="bottom"
          >
            <el-button
              class="toolbar-button locale-button"
              circle
              text
              :aria-label="$t(locale === 'zh-CN' ? 'Switch to English' : 'Switch to Chinese')"
              @click="toggleLocale"
            >
              {{ locale === 'zh-CN' ? 'EN' : '中' }}
            </el-button>
          </el-tooltip>

          <el-tooltip
            :content="$t('Toggle color theme')"
            placement="bottom"
          >
            <el-button
              class="toolbar-button"
              circle
              text
              :aria-label="$t(isDark ? 'Use light theme' : 'Use dark theme')"
              @click="themeStore.toggleTheme()"
            >
              <el-icon><Moon v-if="!isDark" /><Sunny v-else /></el-icon>
            </el-button>
          </el-tooltip>

          <el-dropdown trigger="click">
            <button
              type="button"
              class="user-control apple-glass"
            >
              <el-avatar :size="32">
                {{ userInitial }}
              </el-avatar>
              <span class="user-copy">
                <strong>{{ authStore.user?.username }}</strong>
                <small>{{ authStore.isAdmin ? $t('Administrator') : authStore.user?.role }}</small>
              </span>
              <el-icon><ArrowDown /></el-icon>
            </button>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item @click="router.push('/settings')">
                  {{ $t('Settings') }}
                </el-dropdown-item>
                <el-dropdown-item
                  divided
                  @click="logout"
                >
                  {{ $t('Sign out') }}
                </el-dropdown-item>
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
import { locale, toggleLocale, translate } from '@/i18n'

const route = useRoute()
const router = useRouter()
const authStore = useAuthStore()
const themeStore = useThemeStore()
const isCollapsed = ref(false)
const mobileOpen = ref(false)

const activeMenu = computed(() => route.path)
const currentTitle = computed(() => translate(String(route.meta.title || 'B-agent')))
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
  background:
    radial-gradient(circle at 76% -10%, color-mix(in srgb, var(--apple-blue) 7%, transparent), transparent 34%),
    var(--surface-canvas);
}

.console-sidebar {
  position: fixed;
  inset: 0 auto 0 0;
  z-index: 30;
  display: flex;
  width: 248px;
  flex-direction: column;
  border-width: 0 1px 0 0;
  border-radius: 0;
  background: var(--surface-sidebar);
  box-shadow: inset -1px 0 0 var(--border-highlight);
  -webkit-backdrop-filter: blur(32px) saturate(170%);
  backdrop-filter: blur(32px) saturate(170%);
  transition: width 260ms var(--motion-spring), transform 260ms var(--motion-spring);
}

.sidebar-titlebar {
  display: flex;
  min-height: 68px;
  align-items: center;
  gap: 14px;
  padding: 0 18px;
  border-bottom: 1px solid var(--border-hairline);
}

.brand {
  display: flex;
  min-width: 0;
  align-items: center;
  gap: 9px;
}

.brand-mark {
  width: 30px;
  height: 30px;
  flex: 0 0 auto;
  border-radius: 8px;
  box-shadow: inset 0 1px 0 rgb(255 255 255 / 0.3), 0 2px 6px rgb(0 122 255 / 0.2);
}

.brand-copy {
  display: grid;
  min-width: 0;
  gap: 1px;
}

.brand-copy strong {
  font-size: 14px;
  font-weight: 650;
  letter-spacing: -0.01em;
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
  padding: 12px 10px;
  background: transparent;
}

.console-menu:not(.el-menu--collapse) {
  width: 100%;
}

.console-menu :deep(.el-menu-item),
.console-menu :deep(.el-sub-menu__title) {
  height: 38px;
  margin: 2px 0;
  border-radius: 9px;
  color: var(--text-secondary);
  line-height: 38px;
}

.console-menu :deep(.el-menu-item:hover),
.console-menu :deep(.el-menu-item:focus) {
  color: var(--text-primary);
  background: var(--surface-hover);
}

.console-menu :deep(.el-menu-item.is-active) {
  color: var(--text-primary);
  background: var(--surface-selected);
  font-weight: 590;
}

.console-menu :deep(.el-menu-item.is-active .el-icon) {
  color: var(--apple-blue);
}

.console-menu :deep(.el-menu-item-group__title) {
  padding: 16px 12px 5px;
  color: var(--text-tertiary);
  font-size: 10px;
  font-weight: 650;
  letter-spacing: 0.04em;
  text-transform: uppercase;
}

.collapse-control {
  display: flex;
  min-height: 52px;
  align-items: center;
  gap: 10px;
  padding: 0 20px;
  border: 0;
  border-top: 1px solid var(--border-hairline);
  color: var(--text-secondary);
  background: transparent;
  cursor: pointer;
}

.collapse-control:hover,
.collapse-control:focus-visible {
  color: var(--apple-blue);
  background: var(--surface-hover);
  outline: none;
}

.console-content {
  min-width: 0;
  margin-left: 248px;
  transition: margin-left 260ms var(--motion-spring);
}

.console-header {
  position: sticky;
  top: 0;
  z-index: 20;
  display: flex;
  min-height: 68px;
  align-items: center;
  justify-content: space-between;
  gap: 20px;
  padding: 0 26px;
  border-bottom: 1px solid var(--border-hairline);
  background: color-mix(in srgb, var(--surface-glass) 90%, transparent);
  box-shadow: inset 0 1px 0 var(--border-highlight);
  -webkit-backdrop-filter: blur(28px) saturate(170%);
  backdrop-filter: blur(28px) saturate(170%);
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
  color: var(--text-secondary);
  font-size: 11px;
}

.header-title strong {
  font-size: 15px;
  font-weight: 650;
  letter-spacing: -0.015em;
}

.header-actions {
  gap: 7px;
}

.toolbar-button {
  width: 34px;
  height: 34px;
  color: var(--text-secondary);
  background: var(--surface-sunken);
}

.locale-button {
  font-size: 12px;
  font-weight: 680;
}

.user-control {
  gap: 9px;
  min-height: 40px;
  padding: 3px 9px 3px 4px;
  border-radius: var(--radius-pill);
  color: var(--text-primary);
  background: var(--surface-glass);
  font: inherit;
  cursor: pointer;
}

.user-control:hover,
.user-control:focus-visible {
  background: color-mix(in srgb, var(--surface-glass) 68%, var(--surface-hover));
  outline: none;
}

.user-copy {
  display: grid;
  min-width: 100px;
  text-align: left;
}

.user-copy strong {
  font-size: 13px;
  font-weight: 610;
}

.console-main {
  width: min(100%, 1520px);
  margin: 0 auto;
  padding: 28px clamp(20px, 3vw, 40px) 44px;
}

.is-collapsed .console-sidebar {
  width: 82px;
}

.is-collapsed .console-content {
  margin-left: 82px;
}

.is-collapsed .sidebar-titlebar {
  justify-content: center;
  padding: 0;
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
    background: rgb(15 15 17 / 0.42);
    -webkit-backdrop-filter: blur(4px);
    backdrop-filter: blur(4px);
  }

  .collapse-control {
    display: none;
  }

  .console-main {
    padding: 20px 14px 36px;
  }

  .console-header {
    padding: 0 14px;
  }

  .user-copy {
    display: none;
  }
}
</style>
