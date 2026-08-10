import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import path from 'node:path'
import test from 'node:test'

const frontendDir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')

function source(relativePath) {
  return readFileSync(path.join(frontendDir, relativePath), 'utf8')
}

test('global tokens expose a neutral AI workspace system in both themes', () => {
  const styles = source('src/styles/main.scss')

  for (const token of [
    '--brand-accent',
    '--surface-canvas',
    '--surface-sidebar',
    '--surface-elevated',
    '--surface-selected',
    '--text-primary',
    '--border-hairline',
  ]) {
    assert.match(styles, new RegExp(token))
  }

  assert.match(styles, /--brand-accent:\s*#10a37f/)
  assert.match(styles, /prefers-reduced-motion:\s*reduce/)
  assert.match(styles, /\.dark\s*\{/)
})

test('Element Plus primitives inherit the neutral control language', () => {
  const styles = source('src/styles/main.scss')

  for (const selector of ['.el-button', '.el-input__wrapper', '.el-card', '.el-dialog', '.el-table']) {
    assert.match(styles, new RegExp(selector.replace('.', '\\.')))
  }
  assert.match(styles, /cubic-bezier\(0\.2,\s*0,\s*0,\s*1\)/)
  assert.doesNotMatch(styles, /lighten\(/)
})

test('application shell and login use the minimal AI workspace conventions', () => {
  const layout = source('src/layouts/MainLayout.vue')
  const login = source('src/views/Login.vue')
  const theme = source('src/stores/theme.ts')

  assert.match(layout, /new-chat-control/)
  assert.match(layout, /background:\s*var\(--surface-sidebar\)/)
  assert.match(login, /login-panel/)
  assert.doesNotMatch(layout, /window-controls|sidebar-material/)
  assert.doesNotMatch(login, /window-controls|login-window|login-ambient/)
  assert.match(login, /\.login-page\s*\{[^}]*width:\s*100%/)
  assert.doesNotMatch(login, /#667eea|#764ba2/)
  assert.match(theme, /localStorage\.setItem\('theme'/)
})

test('B-agent brand mark is shared by the browser, login, and application shell', () => {
  const index = source('index.html')
  const layout = source('src/layouts/MainLayout.vue')
  const login = source('src/views/Login.vue')
  const logo = source('public/b-agent-logo.svg')

  assert.match(index, /href="\/b-agent-logo\.svg"/)
  assert.match(index, /href="\/apple-touch-icon\.png"/)
  assert.match(layout, /src="\/b-agent-logo\.svg"/)
  assert.match(login, /src="\/b-agent-logo\.svg"/)
  assert.match(login, /\.app-icon\s*\{[^}]*display:\s*block/)
  assert.match(logo, /viewBox="0 0 64 64"/)
  assert.match(logo, /#007aff/i)
})

test('visible console copy contains no forbidden dash typography', () => {
  const files = [
    'src/layouts/MainLayout.vue',
    'src/views/Login.vue',
    'src/views/Dashboard.vue',
    'src/views/Analytics.vue',
    'src/views/Operations.vue',
    'src/views/Settings.vue',
    'src/views/DeadLetters.vue',
  ]
  const combined = files.map(source).join('\n')
  assert.doesNotMatch(combined, /[—–]/)
})

test('all primary work surfaces use the shared workspace page rhythm', () => {
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
