import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import path from 'node:path'
import test from 'node:test'

const frontendDir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const source = (relativePath) => readFileSync(path.join(frontendDir, relativePath), 'utf8')

test('media runtime client exposes revision, probe and activation APIs', () => {
  const api = source('src/api/mediaRuntime.ts')

  for (const method of [
    'getState',
    'getCapabilities',
    'listRevisions',
    'createRevision',
    'probeRevision',
    'activateRevision',
  ]) {
    assert.match(api, new RegExp(`async ${method}\\(`))
  }
  assert.match(api, /\/api\/v1\/admin\/media\/runtime/)
  assert.doesNotMatch(api, /queue\.fal\.run|fal\.run/)
})

test('settings presents secret-safe hot media configuration to administrators', () => {
  const view = source('src/views/Settings.vue')

  assert.match(view, /mediaRuntimeApi\.getCapabilities/)
  assert.match(view, /mediaRuntimeApi\.createRevision/)
  assert.match(view, /mediaRuntimeApi\.probeRevision/)
  assert.match(view, /mediaRuntimeApi\.activateRevision/)
  assert.match(view, /type="password"/)
  assert.match(view, /activeMediaRevision/)
  assert.match(view, /latest_probe/)
  assert.doesNotMatch(view, /allow-create[^>]*media/i)
  assert.doesNotMatch(view, /localStorage\.(setItem|getItem)\([^)]*(fal|media.*key)/i)
})

test('media runtime settings are bilingual and explain next-job activation', () => {
  const messages = source('src/i18n/index.ts')

  assert.match(messages, /'Media generation runtime': '媒体生成运行时'/)
  assert.match(messages, /'Create immutable revision': '创建不可变版本'/)
  assert.match(messages, /'Activate for new jobs': '对新任务启用'/)
  assert.match(messages, /'fal API key is write-only': 'fal API 密钥仅可写入'/)
})
