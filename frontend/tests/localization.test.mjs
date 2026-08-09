import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import path from 'node:path'
import test from 'node:test'

const frontendDir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const source = (relativePath) => readFileSync(path.join(frontendDir, relativePath), 'utf8')

test('locale service supports browser-default Chinese and persistent language selection', () => {
  const locale = source('src/i18n/index.ts')

  assert.match(locale, /navigator\.language/)
  assert.match(locale, /localStorage\.getItem\('ui_locale'\)/)
  assert.match(locale, /localStorage\.setItem\('ui_locale'/)
  assert.match(locale, /zh-CN/)
  assert.match(locale, /en-US/)
})

test('application shell exposes locale switching and Element Plus localization', () => {
  const app = source('src/App.vue')
  const layout = source('src/layouts/MainLayout.vue')
  const login = source('src/views/Login.vue')

  assert.match(app, /el-config-provider/)
  assert.match(app, /elementLocale/)
  assert.match(layout, /toggleLocale/)
  assert.match(login, /toggleLocale/)
  assert.match(layout, /\$t\(/)
  assert.match(login, /\$t\(/)
})

test('all primary operating views opt into translated copy', () => {
  const views = [
    'Dashboard.vue',
    'Workflows.vue',
    'Skills.vue',
    'Customers.vue',
    'CustomerDetail.vue',
    'Conversations.vue',
    'ConversationDetail.vue',
    'Analytics.vue',
    'Operations.vue',
    'DeadLetters.vue',
    'Settings.vue',
    'WorkflowEditor.vue',
  ]

  for (const view of views) {
    assert.match(source(`src/views/${view}`), /\$t\(/, `${view} must render translated copy`)
  }
})
