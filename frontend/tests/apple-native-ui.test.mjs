import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import path from 'node:path'
import test from 'node:test'

const frontendDir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')

function source(relativePath) {
  return readFileSync(path.join(frontendDir, relativePath), 'utf8')
}

test('global tokens expose an Apple-native surface system in both themes', () => {
  const styles = source('src/styles/main.scss')

  for (const token of [
    '--apple-blue',
    '--surface-canvas',
    '--surface-glass',
    '--surface-elevated',
    '--radius-window',
    '--shadow-window',
  ]) {
    assert.match(styles, new RegExp(token))
  }

  assert.match(styles, /backdrop-filter:\s*blur/)
  assert.match(styles, /prefers-reduced-transparency:\s*reduce/)
  assert.match(styles, /prefers-reduced-motion:\s*reduce/)
  assert.match(styles, /\.dark\s*\{/)
})

test('Element Plus primitives inherit the native control language', () => {
  const styles = source('src/styles/main.scss')

  for (const selector of ['.el-button', '.el-input__wrapper', '.el-card', '.el-dialog', '.el-table']) {
    assert.match(styles, new RegExp(selector.replace('.', '\\.')))
  }
  assert.match(styles, /cubic-bezier\(0\.16,\s*1,\s*0\.3,\s*1\)/)
  assert.doesNotMatch(styles, /lighten\(/)
})

test('application shell and login use macOS window conventions without legacy purple gradients', () => {
  const layout = source('src/layouts/MainLayout.vue')
  const login = source('src/views/Login.vue')
  const theme = source('src/stores/theme.ts')

  assert.match(layout, /window-controls/)
  assert.match(layout, /sidebar-material/)
  assert.match(login, /login-window/)
  assert.match(login, /window-controls/)
  assert.match(login, /\.login-page\s*\{[^}]*width:\s*100%/)
  assert.doesNotMatch(login, /#667eea|#764ba2/)
  assert.match(theme, /localStorage\.setItem\('theme'/)
})

test('visible console copy contains no forbidden dash typography', () => {
  const files = [
    'src/layouts/MainLayout.vue',
    'src/views/Login.vue',
    'src/views/Dashboard.vue',
    'src/views/Analytics.vue',
    'src/views/Operations.vue',
    'src/views/Settings.vue',
  ]
  const combined = files.map(source).join('\n')
  assert.doesNotMatch(combined, /[—–]/)
})

test('all primary work surfaces use the shared native page rhythm', () => {
  const views = [
    'Workflows.vue',
    'Customers.vue',
    'Conversations.vue',
    'DeadLetters.vue',
    'CustomerDetail.vue',
    'ConversationDetail.vue',
  ]

  for (const view of views) {
    const contents = source(`src/views/${view}`)
    assert.match(contents, /page-stack/, `${view} must use page-stack`)
    assert.match(contents, /page-heading/, `${view} must use page-heading`)
  }
})
