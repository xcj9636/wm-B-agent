import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import path from 'node:path'
import test from 'node:test'

const frontendDir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')

function source(relativePath) {
  return readFileSync(path.join(frontendDir, relativePath), 'utf8')
}

test('Agent Center exposes the approval-gated delivery console', () => {
  const view = source('src/views/AgentCenter.vue')
  const api = source('src/api/agent.ts')

  assert.match(api, /createDelivery/)
  assert.match(api, /reviewDelivery/)
  assert.match(api, /\/api\/v1\/agent\/deliveries/)
  assert.match(view, /agentApi\.deliveries/)
  assert.match(view, /agentApi\.createDelivery/)
  assert.match(view, /agentApi\.reviewDelivery/)
  assert.match(view, /\$t\('Delivery control'\)/)
  assert.match(view, /\$t\('Sender account'\)/)
  assert.match(view, /\$t\('Approve and schedule'\)/)
  assert.match(view, /approval_pending/)
  assert.match(view, /awaiting_verification/)
})

test('delivery UI is bilingual and links to hot mailbox configuration', () => {
  const view = source('src/views/AgentCenter.vue')
  const messages = source('src/i18n/index.ts')

  assert.match(view, /router\.push\('\/settings'\)/)
  assert.match(messages, /'Delivery control': '投递控制台'/)
  assert.match(messages, /'Sender account': '发件账号'/)
  assert.match(messages, /'Approve and schedule': '批准并调度'/)
  assert.match(messages, /'awaiting_verification': '等待已发送核验'/)
})
