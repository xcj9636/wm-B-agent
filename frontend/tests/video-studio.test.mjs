import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import path from 'node:path'
import test from 'node:test'

const frontendDir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')

function source(relativePath) {
  return readFileSync(path.join(frontendDir, relativePath), 'utf8')
}

test('video studio exposes the complete governed planning API contract', () => {
  const api = source('src/api/video.ts')

  for (const method of [
    'listPersonas',
    'listPersonaVersions',
    'createPersona',
    'revisePersona',
    'approvePersona',
    'listProjects',
    'getProject',
    'createProject',
    'createStoryboard',
    'approveStoryboard',
    'compileShot',
  ]) {
    assert.match(api, new RegExp(`async ${method}\\(`))
  }

  assert.match(api, /\/api\/v1\/video\/personas/)
  assert.match(api, /\/api\/v1\/video\/projects/)
  assert.match(api, /\/approve/)
  assert.match(api, /\/compile/)
})

test('video studio is routed, localized, and visible in primary navigation', () => {
  const router = source('src/router/index.ts')
  const layout = source('src/layouts/MainLayout.vue')
  const i18n = source('src/i18n/index.ts')

  assert.match(router, /path: 'video-studio'/)
  assert.match(router, /views\/VideoStudio\.vue/)
  assert.match(layout, /index="\/video-studio"/)
  assert.match(i18n, /'Video Studio': '视频工作室'/)
})

test('video studio loads real read models with complete UI states', () => {
  const view = source('src/views/VideoStudio.vue')

  assert.match(view, /videoApi\.listPersonas/)
  assert.match(view, /videoApi\.listProjects/)
  assert.match(view, /videoApi\.getProject/)
  assert.match(view, /v-loading="loading"/)
  assert.match(view, /loadError/)
  assert.match(view, /empty-state/)
  assert.doesNotMatch(view, /Mock data|coming soon|SYSTEM_CONSTRAINTS|intent\.prompt/i)
  assert.doesNotMatch(view, /[—–]/)
})

test('video studio drives the approval-gated planning workflow from the browser', () => {
  const view = source('src/views/VideoStudio.vue')

  for (const action of [
    'videoApi.createPersona',
    'videoApi.createProject',
    'videoApi.createStoryboard',
    'videoApi.approvePersona',
    'videoApi.approveStoryboard',
    'videoApi.compileShot',
  ]) {
    assert.match(view, new RegExp(action.replace('.', '\\.')))
  }

  assert.match(view, /personaDialogOpen/)
  assert.match(view, /projectDialogOpen/)
  assert.match(view, /storyboardDialogOpen/)
  assert.match(view, /compiledReceipt/)
  assert.doesNotMatch(view, /compiledReceipt[^\n]*prompt|prompt:\s*compiledReceipt/i)
})
