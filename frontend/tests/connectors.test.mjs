import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import path from 'node:path'
import test from 'node:test'

const frontendDir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const source = (relativePath) => readFileSync(path.join(frontendDir, relativePath), 'utf8')

test('connectors are an administrator-only first-class product surface', () => {
  const router = source('src/router/index.ts')
  const layout = source('src/layouts/MainLayout.vue')
  const view = source('src/views/Connectors.vue')

  assert.match(router, /path: 'connectors'/)
  assert.match(router, /Connectors\.vue/)
  assert.match(router, /requiresAdmin: true/)
  assert.match(layout, /index="\/connectors"/)
  assert.match(view, /connectorsApi\.create/)
  assert.match(view, /connectorsApi\.test/)
  assert.match(view, /connectorsApi\.setEnabled/)
})

test('browser calls only B-agent connector APIs and never persists provider secrets', () => {
  const connectorApi = source('src/api/connectors.ts')
  const view = source('src/views/Connectors.vue')
  const browserSource = `${connectorApi}\n${view}`

  assert.match(connectorApi, /\/api\/v1\/connectors/)
  assert.doesNotMatch(browserSource, /api\.hunter\.io/)
  assert.doesNotMatch(browserSource, /localStorage\.(setItem|getItem)\([^)]*(secret|api.key)/i)
  assert.doesNotMatch(browserSource, /secret_ref/)
})

test('connector control plane is bilingual', () => {
  const messages = source('src/i18n/index.ts')
  assert.match(messages, /Connectors: '连接器'/)
  assert.match(messages, /'Test connection': '测试连接'/)
  assert.match(messages, /'API key is write-only': 'API 密钥仅可写入'/)
})
