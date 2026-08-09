import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import path from 'node:path'
import test from 'node:test'

const frontendDir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const repositoryDir = path.resolve(frontendDir, '..')

function source(relativePath, root = frontendDir) {
  return readFileSync(path.join(root, relativePath), 'utf8')
}

test('the console exposes every backend operating surface', () => {
  const router = source('src/router/index.ts')
  const layout = source('src/layouts/MainLayout.vue')
  const operations = source('src/views/Operations.vue')
  const skills = source('src/views/Skills.vue')

  for (const route of ['dashboard', 'workflows', 'skills', 'customers', 'conversations', 'analytics', 'operations', 'settings']) {
    assert.match(router, new RegExp(`path: '${route}`))
  }
  assert.match(layout, /index="\/skills"/)
  assert.match(layout, /index="\/operations"/)
  assert.match(operations, /ai-gateway\/status/)
  assert.match(operations, /reliable-execution\/status/)
  assert.match(operations, /\/api\/v1\/admin\/tasks/)
  assert.match(skills, /skillApi\.list/)
})

test('settings can configure and verify the backend API at runtime', () => {
  const runtimeConfig = source('src/api/runtimeConfig.ts')
  const api = source('src/api/index.ts')
  const mailboxes = source('src/api/mailboxes.ts')
  const settings = source('src/views/Settings.vue')

  assert.match(runtimeConfig, /backend_api_url/)
  assert.match(runtimeConfig, /resolveBackendApiUrl/)
  assert.match(runtimeConfig, /setBackendApiUrl/)
  assert.match(api, /resolveBackendApiUrl/)
  assert.match(settings, /testBackendConnection/)
  assert.match(settings, /\/health/)
  assert.match(settings, /mailboxApi\.list/)
  assert.match(mailboxes, /\/api\/v1\/mailboxes/)
  assert.doesNotMatch(settings, /apiProviders|your-api-key|Mock data/i)
})

test('active dashboard and analytics surfaces use real API responses', () => {
  const files = [
    'src/views/Dashboard.vue',
    'src/views/Analytics.vue',
    'src/components/Dashboard/RecentActivity.vue',
    'src/components/Dashboard/ConversionFunnel.vue',
    'src/components/Monitor/ExecutionMonitor.vue',
  ]
  const combined = files.map((file) => source(file)).join('\n')

  assert.doesNotMatch(combined, /Mock data|coming soon|replace with API/i)
  assert.match(combined, /\/api\/v1\/stats\/dashboard/)
  assert.match(combined, /\/api\/v1\/stats\/trends/)
  assert.match(combined, /\/api\/v1\/stats\/conversion-funnel/)
  assert.match(combined, /workflowApi\.getExecution/)
})

test('Docker development frontend uses Vite HMR and the backend proxy', () => {
  const dockerfile = source('Dockerfile')
  const vite = source('vite.config.ts')
  const compose = source('docker-compose.yml', repositoryDir)

  assert.match(dockerfile, /AS development/i)
  assert.match(compose, /target: development/)
  assert.match(compose, /VITE_API_PROXY_TARGET=http:\/\/backend:8000/)
  assert.match(compose, /\.\/frontend:\/app/)
  assert.match(compose, /\/app\/node_modules/)
  assert.match(vite, /VITE_API_PROXY_TARGET/)
  assert.match(vite, /usePolling/)
})

test('remaining interactive console actions do not fall back to placeholders', () => {
  const workflowEditor = source('src/views/WorkflowEditor.vue')
  const alertPanel = source('src/components/Monitor/AlertPanel.vue')
  const conversationChat = source('src/components/ConversationView/ConversationChat.vue')

  assert.match(workflowEditor, /workflowApi\.(create|update)/)
  assert.doesNotMatch(workflowEditor, /console\.log\('Saving workflow/)
  assert.match(alertPanel, /ai-gateway\/status/)
  assert.match(alertPanel, /reliable-execution\/status/)
  assert.doesNotMatch(alertPanel, /Mock data|replace with API/i)
  assert.doesNotMatch(conversationChat, /coming soon/i)
})
