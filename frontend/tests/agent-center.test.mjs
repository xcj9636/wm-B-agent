import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import path from 'node:path'
import test from 'node:test'

const frontendDir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')

function source(relativePath) {
  return readFileSync(path.join(frontendDir, relativePath), 'utf8')
}

test('Agent Center is the primary authenticated product surface', () => {
  const router = source('src/router/index.ts')
  const layout = source('src/layouts/MainLayout.vue')

  assert.match(router, /redirect: '\/agent'/)
  assert.match(router, /path: 'agent'/)
  assert.match(router, /AgentCenter\.vue/)
  assert.match(layout, /index="\/agent"/)
  assert.match(layout, /\$t\('Agent Center'\)/)
})

test('Agent Center renders real orchestration state and business pipelines', () => {
  const view = source('src/views/AgentCenter.vue')
  const api = source('src/api/agent.ts')

  assert.match(api, /\/api\/v1\/agent\/overview/)
  assert.match(api, /\/api\/v1\/agent\/runs/)
  assert.match(view, /agentApi\.overview/)
  assert.match(view, /agentApi\.runs/)
  assert.match(view, /pipeline\.stages/)
  assert.match(view, /registered_skill_count/)
  assert.match(view, /routing/)
  assert.doesNotMatch(view, /mock|coming soon|placeholder data/i)
})

test('Agent Center is bilingual and uses the shared AI workspace page system', () => {
  const view = source('src/views/AgentCenter.vue')
  const messages = source('src/i18n/index.ts')

  assert.match(view, /page-stack/)
  assert.match(view, /\$t\('Business pipelines'\)/)
  assert.match(view, /\$t\('Live agent runs'\)/)
  assert.match(messages, /'Agent Center': 'Agent 中心'/)
  assert.match(messages, /'Business pipelines': '业务流水线'/)
})
