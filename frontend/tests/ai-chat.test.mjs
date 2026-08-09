import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import path from 'node:path'
import test from 'node:test'

const frontendDir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const source = (relativePath) => readFileSync(path.join(frontendDir, relativePath), 'utf8')

test('AI Chat is a first-class authenticated product surface', () => {
  const router = source('src/router/index.ts')
  const layout = source('src/layouts/MainLayout.vue')
  const view = source('src/views/AIChat.vue')

  assert.match(router, /path: 'ai-chat'/)
  assert.match(router, /AIChat\.vue/)
  assert.match(layout, /index="\/ai-chat"/)
  assert.match(layout, /\$t\('AI Chat'\)/)
  assert.match(view, /chatApi\.createSession/)
  assert.match(view, /chatApi\.streamMessage/)
})

test('browser uses only B-agent AI endpoints and never stores OmniRoute credentials', () => {
  const chatApi = source('src/api/ai.ts')
  const settings = source('src/views/Settings.vue')
  const allBrowserSource = [
    chatApi,
    settings,
    source('src/views/AIChat.vue'),
    source('src/api/runtimeConfig.ts'),
  ].join('\n')

  assert.match(chatApi, /\/api\/v1\/ai\/chat\/sessions/)
  assert.match(chatApi, /text\/event-stream/)
  assert.match(settings, /aiApi\.getConfig/)
  assert.match(settings, /aiApi\.updateConfig/)
  assert.match(settings, /aiApi\.testConfig/)
  assert.doesNotMatch(allBrowserSource, /OMNIROUTE_API_KEY/)
  assert.doesNotMatch(allBrowserSource, /localStorage\.(setItem|getItem)\([^)]*api.key/i)
})

test('AI chat and route configuration are bilingual', () => {
  const messages = source('src/i18n/index.ts')
  assert.match(messages, /'AI Chat': 'AI 对话'/)
  assert.match(messages, /'AI route configuration': 'AI 路由配置'/)
  assert.match(messages, /'Send message': '发送消息'/)
})
