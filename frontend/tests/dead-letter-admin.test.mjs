import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import path from 'node:path'
import test from 'node:test'

const frontendDir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')

function source(relativePath) {
  return readFileSync(path.join(frontendDir, relativePath), 'utf8')
}

test('reliable execution API exposes the secret-free dead-letter workflow', () => {
  const api = source('src/api/reliableExecution.ts')

  assert.match(api, /reliable-execution\/dead-letters/)
  assert.match(api, /resolution-approvals/)
  assert.match(api, /confirmed_not_sent/)
  assert.match(api, /confirmed_sent/)
  assert.doesNotMatch(api, /payload_json|business_key/)
})

test('dead-letter view explains two-person approval and omits private payloads', () => {
  const view = source('src/views/DeadLetters.vue')

  assert.match(view, /Two different administrators/)
  assert.match(view, /Evidence reference/)
  assert.match(view, /Provider message ID/)
  assert.match(view, /aria-label="Refresh dead letters"/)
  assert.doesNotMatch(view, /payload_json|business_key|message body/i)
})

test('dead-letter operations route and navigation are administrator-only', () => {
  const router = source('src/router/index.ts')
  const layout = source('src/layouts/MainLayout.vue')

  assert.match(router, /path: 'operations\/dead-letters'/)
  assert.match(router, /requiresAdmin: true/)
  assert.match(layout, /v-if="authStore\.isAdmin"/)
  assert.match(layout, /index="\/operations\/dead-letters"/)
})
