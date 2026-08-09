<template>
  <main class="login-page">
    <div
      class="login-ambient login-ambient--blue"
      aria-hidden="true"
    />
    <div
      class="login-ambient login-ambient--soft-blue"
      aria-hidden="true"
    />

    <section
      class="login-window apple-glass"
      aria-labelledby="login-title"
    >
      <header class="login-titlebar">
        <div
          class="window-controls"
          aria-hidden="true"
        >
          <span class="window-control window-control--close" />
          <span class="window-control window-control--minimize" />
          <span class="window-control window-control--expand" />
        </div>
        <span>B-agent</span>
        <el-button
          class="theme-button"
          circle
          text
          :aria-label="isDark ? 'Use light appearance' : 'Use dark appearance'"
          @click="themeStore.toggleTheme()"
        >
          <el-icon><Moon v-if="!isDark" /><Sunny v-else /></el-icon>
        </el-button>
      </header>

      <div class="login-content">
        <img
          class="app-icon"
          src="/b-agent-logo.svg"
          alt="B-agent"
        >
        <div class="login-heading">
          <h1 id="login-title">
            Welcome back
          </h1>
          <p>Sign in to manage workflows, conversations and AI operations.</p>
        </div>

        <el-form
          ref="formRef"
          :model="form"
          :rules="rules"
          label-position="top"
          @submit.prevent="handleLogin"
        >
          <el-form-item
            label="Username"
            prop="username"
          >
            <el-input
              v-model="form.username"
              autocomplete="username"
              placeholder="Enter your username"
              size="large"
              clearable
            >
              <template #prefix>
                <el-icon><User /></el-icon>
              </template>
            </el-input>
          </el-form-item>

          <el-form-item
            label="Password"
            prop="password"
          >
            <el-input
              v-model="form.password"
              type="password"
              autocomplete="current-password"
              placeholder="Enter your password"
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
            Sign in
          </el-button>
        </el-form>

        <p class="access-note">
          Access is managed by your workspace administrator.
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

const router = useRouter()
const route = useRoute()
const authStore = useAuthStore()
const themeStore = useThemeStore()
const isDark = computed(() => themeStore.isDark)
const formRef = ref<FormInstance>()
const loading = ref(false)
const form = reactive({ username: '', password: '' })

const rules: FormRules = {
  username: [{ required: true, message: 'Please enter your username', trigger: 'blur' }],
  password: [{ required: true, message: 'Please enter your password', trigger: 'blur' }],
}

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
    ElMessage.success('Signed in successfully')
    const redirect = (route.query.redirect as string) || '/dashboard'
    await router.push(redirect)
  } catch (error: any) {
    ElMessage.error(error.response?.data?.detail || 'Sign in failed')
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
  overflow: hidden;
  padding: 24px;
  background:
    linear-gradient(180deg, color-mix(in srgb, var(--surface-canvas) 84%, transparent), var(--surface-canvas)),
    var(--surface-canvas);
}

.login-ambient {
  position: absolute;
  width: min(46vw, 620px);
  aspect-ratio: 1;
  border-radius: 50%;
  opacity: 0.34;
  filter: blur(90px);
  pointer-events: none;
}

.login-ambient--blue {
  top: -28%;
  right: 4%;
  background: color-mix(in srgb, var(--apple-blue) 45%, transparent);
}

.login-ambient--soft-blue {
  bottom: -34%;
  left: 2%;
  background: color-mix(in srgb, var(--apple-blue) 22%, transparent);
}

.login-window {
  position: relative;
  z-index: 1;
  width: min(100%, 440px);
  overflow: hidden;
  border-radius: var(--radius-window);
  box-shadow: var(--shadow-window), inset 0 1px 0 var(--border-highlight);
  animation: login-enter 480ms var(--motion-spring) both;
}

.login-titlebar {
  display: grid;
  min-height: 52px;
  grid-template-columns: 1fr auto 1fr;
  align-items: center;
  padding: 0 16px;
  border-bottom: 1px solid var(--border-hairline);
  color: var(--text-secondary);
  font-size: 12px;
  font-weight: 600;
}

.theme-button {
  justify-self: end;
  color: var(--text-secondary);
}

.login-content {
  padding: 34px 38px 30px;
}

.app-icon {
  display: block;
  width: 58px;
  height: 58px;
  margin: 0 auto 20px;
  border-radius: 15px;
  box-shadow: inset 0 1px 0 rgb(255 255 255 / 0.35), 0 10px 24px rgb(0 122 255 / 0.24);
}

.login-heading {
  margin-bottom: 28px;
  text-align: center;
}

.login-heading h1 {
  margin: 0;
  color: var(--text-primary);
  font-size: 28px;
  font-weight: 690;
  letter-spacing: -0.035em;
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
  margin: 22px 0 0;
  color: var(--text-tertiary);
  font-size: 12px;
  text-align: center;
}

@keyframes login-enter {
  from { opacity: 0; transform: translateY(12px) scale(0.985); }
  to { opacity: 1; transform: translateY(0) scale(1); }
}

@media (max-width: 520px) {
  .login-page { padding: 12px; }
  .login-content { padding: 28px 22px 24px; }
  .login-window { border-radius: 18px; }
}
</style>
