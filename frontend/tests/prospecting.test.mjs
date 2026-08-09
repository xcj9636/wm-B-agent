import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import path from 'node:path'
import test from 'node:test'

const frontendDir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const source = (relativePath) => readFileSync(path.join(frontendDir, relativePath), 'utf8')

test('prospecting workspace exposes domain search, named finder and selective import', () => {
  const router = source('src/router/index.ts')
  const layout = source('src/layouts/MainLayout.vue')
  const view = source('src/views/Prospecting.vue')

  assert.match(router, /path: 'prospecting'/)
  assert.match(router, /Prospecting\.vue/)
  assert.match(layout, /index="\/prospecting"/)
  assert.match(view, /mode.*domain_search/)
  assert.match(view, /mode.*email_finder/)
  assert.match(view, /prospectingApi\.createSearch/)
  assert.match(view, /prospectingApi\.importContacts/)
})

test('prospecting browser surface uses B-agent APIs and excludes restricted LinkedIn inputs', () => {
  const api = source('src/api/prospecting.ts')
  const view = source('src/views/Prospecting.vue')
  const browserSource = `${api}\n${view}`

  assert.match(api, /\/api\/v1\/prospecting\/searches/)
  assert.match(api, /\/api\/v1\/prospecting\/contacts\/import/)
  assert.doesNotMatch(browserSource, /api\.hunter\.io/)
  assert.doesNotMatch(browserSource, /linkedin_handle/)
  assert.doesNotMatch(browserSource, /localStorage\.(setItem|getItem)\([^)]*(secret|api.key)/i)
})

test('prospecting workspace is bilingual', () => {
  const messages = source('src/i18n/index.ts')
  assert.match(messages, /Prospecting: '智能获客'/)
  assert.match(messages, /'Domain search': '域名搜索'/)
  assert.match(messages, /'Import selected': '导入所选联系人'/)
})

test('prospecting workspace exposes resumable batch enrichment jobs', () => {
  const api = source('src/api/prospecting.ts')
  const view = source('src/views/Prospecting.vue')
  const messages = source('src/i18n/index.ts')

  assert.match(api, /\/api\/v1\/prospecting\/jobs/)
  assert.match(api, /createJob/)
  assert.match(api, /resumeJob/)
  assert.match(view, /batch_domain_search/)
  assert.match(view, /prospectingApi\.createJob/)
  assert.match(view, /prospectingApi\.resumeJob/)
  assert.match(view, /request_budget/)
  assert.match(view, /pages_completed/)
  assert.match(messages, /'Batch enrichment': '批量补全'/)
  assert.match(messages, /'Request budget': '请求预算'/)
  assert.doesNotMatch(`${api}\n${view}`, /linkedin_handle/)
})
