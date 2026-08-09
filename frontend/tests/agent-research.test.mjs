import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import path from 'node:path'
import test from 'node:test'

const frontendDir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')

function source(relativePath) {
  return readFileSync(path.join(frontendDir, relativePath), 'utf8')
}

test('Agent Center exposes the sourced company research queue', () => {
  const view = source('src/views/AgentCenter.vue')
  const api = source('src/api/agent.ts')

  assert.match(api, /\/api\/v1\/agent\/research-jobs/)
  assert.match(api, /updateResearchEvidence/)
  assert.match(api, /reviewResearchJob/)
  assert.match(view, /agentApi\.researchJobs/)
  assert.match(view, /profile_evidence/)
  assert.match(view, /market_signals/)
  assert.match(view, /\$t\('Research queue'\)/)
  assert.match(view, /\$t\('Evidence review'\)/)
  assert.doesNotMatch(view, /mock|placeholder data/i)
})

test('Agent Center drafts through B-agent APIs and keeps human approval visible', () => {
  const view = source('src/views/AgentCenter.vue')
  const api = source('src/api/agent.ts')

  assert.match(api, /createOutreachDraft/)
  assert.match(api, /reviewOutreachDraft/)
  assert.match(api, /\/outreach-drafts\/\$\{draftId\}\/review/)
  assert.match(view, /agentApi\.createOutreachDraft/)
  assert.match(view, /agentApi\.reviewOutreachDraft/)
  assert.match(view, /\$t\('Generate outreach draft'\)/)
  assert.match(view, /\$t\('Approve draft'\)/)
  assert.match(view, /stale/)
})

test('research and drafting controls are bilingual and link to hot AI configuration', () => {
  const view = source('src/views/AgentCenter.vue')
  const messages = source('src/i18n/index.ts')

  assert.match(view, /router\.push\('\/settings'\)/)
  assert.match(messages, /'Research queue': '企业调研队列'/)
  assert.match(messages, /'Generate outreach draft': '生成触达草稿'/)
  assert.match(messages, /'Configure AI route': '配置 AI 路由'/)
})
