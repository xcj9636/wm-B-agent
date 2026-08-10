<template>
  <main class="login-page">
    <section
      class="login-panel"
      aria-labelledby="login-title"
    >
      <header class="login-toolbar">
        <div class="titlebar-actions">
          <el-button
            class="locale-button"
            circle
            text
            :aria-label="$t(locale === 'zh-CN' ? 'Switch to English' : 'Switch to Chinese')"
            @click="toggleLocale"
          >
            {{ locale === 'zh-CN' ? 'EN' : '中' }}
          </el-button>
          <el-button
            class="theme-button"
            circle
            text
            :aria-label="$t(isDark ? 'Use light theme' : 'Use dark theme')"
            @click="themeStore.toggleTheme()"
          >
            <el-icon><Moon v-if="!isDark" /><Sunny v-else /></el-icon>
          </el-button>
        </div>
      </header>

      <div class="login-content">
        <img
          class="app-icon"
          src="/b-agent-logo.svg"
          alt="B-agent"
        >
        <div class="login-heading">
          <h1 id="login-title">
            {{ $t('Welcome back') }}
          </h1>
          <p>{{ $t('Sign in to manage workflows, conversations and AI operations.') }}</p>
        </div>

        <el-form
          ref="formRef"
          :model="form"
          :rules="rules"
          label-position="top"
          @submit.prevent="handleLogin"
        >
          <el-form-item
            :label="$t('Username')"
            prop="username"
          >
            <el-input
              v-model="form.username"
              autocomplete="username"
              :placeholder="$t('Enter your username')"
              size="large"
              clearable
            >
              <template #prefix>
                <el-icon><User /></el-icon>
              </template>
            </el-input>
          </el-form-item>

          <el-form-item
            :label="$t('Password')"
            prop="password"
          >
            <el-input
              v-model="form.password"
              type="password"
              autocomplete="current-password"
              :placeholder="$t('Enter your password')"
              size="large"
              show-password
              @keyup.enter="handleLogin"
            >
              <template #prefix>
                <el-icon><Lock /></el-icon>
              </template>
            </el-input>
          </el-form-item>

          <el-button
            type="primary"
            size="large"
            native-type="submit"
            :loading="loading"
            class="login-button"
          >
            {{ $t('Sign in') }}
          </el-button>
        </el-form>

        <p class="access-note">
          {{ $t('Access is managed by your workspace administrator.') }}
        </p>
      </div>
    </section>
  </main>
</template>

<script setup lang="ts">
import { computed, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import type { FormInstance, FormRules } from 'element-plus'
import { useAuthStore } from '@/stores/auth'
import { useThemeStore } from '@/stores/theme'
import { locale, toggleLocale, translate } from '@/i18n'

const router = useRouter()
const route = useRoute()
const authStore = useAuthStore()
const themeStore = useThemeStore()
const isDark = computed(() => themeStore.isDark)
const formRef = ref<FormInstance>()
const loading = ref(false)
const form = reactive({ username: '', password: '' })

const rules = computed<FormRules>(() => ({
  username: [{ required: true, message: translate('Please enter your username'), trigger: 'blur' }],
  password: [{ required: true, message: translate('Please enter your password'), trigger: 'blur' }],
}))

async function handleLogin() {
  if (!formRef.value) return
  try {
    await formRef.value.validate()
  } catch {
    return
  }

  loading.value = true
  try {
    await authStore.login(form)
    ElMessage.success(translate('Signed in successfully'))
    const redirect = (route.query.redirect as string) || '/dashboard'
    await router.push(redirect)
  } catch (error: any) {
    ElMessage.error(error.response?.data?.detail || translate('Sign in failed'))
  } finally {
    loading.value = false
  }
}
</script>

<style lang="scss" scoped>
.login-page {
  position: relative;
  display: grid;
  width: 100%;
  min-height: 100dvh;
  place-items: center;
  padding: 24px;
  background: var(--surface-canvas);
}

.login-panel {
  width: min(100%, 400px);
  animation: login-enter 260ms var(--motion-spring) both;
}

.login-toolbar {
  display: flex;
  min-height: 44px;
  align-items: center;
  justify-content: flex-end;
}

.titlebar-actions {
  display: flex;
  justify-self: end;
}

.theme-button,
.locale-button {
  justify-self: end;
  color: var(--text-secondary);
}

.locale-button {
  font-size: 11px;
  font-weight: 680;
}

.login-content {
  padding: 22px 18px 18px;
}

.app-icon {
  display: block;
  width: 58px;
  height: 58px;
  margin: 0 auto 20px;
  border-radius: 15px;
}

.login-heading {
  margin-bottom: 30px;
  text-align: center;
}

.login-heading h1 {
  margin: 0;
  color: var(--text-primary);
  font-size: 30px;
  font-weight: 650;
  letter-spacing: -0.03em;
}

.login-heading p {
  max-width: 320px;
  margin: 8px auto 0;
  color: var(--text-secondary);
  line-height: 1.5;
}

.login-button {
  width: 100%;
  margin-top: 2px;
}

.access-note {
  margin: 24px 0 0;
  color: var(--text-tertiary);
  font-size: 12px;
  text-align: center;
}

@keyframes login-enter {
  from { opacity: 0; transform: translateY(8px); }
  to { opacity: 1; transform: translateY(0); }
}

@media (max-width: 520px) {
  .login-page { padding: 12px; }
  .login-content { padding: 18px 8px; }
}
</style>
