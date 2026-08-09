import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import path from 'node:path'
import test from 'node:test'

const frontendDir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')

function source(relativePath) {
  return readFileSync(path.join(frontendDir, relativePath), 'utf8')
}

test('Settings provides OAuth mailbox connection without browser-side tokens', () => {
  const settings = source('src/views/Settings.vue')
  const api = source('src/api/mailboxes.ts')

  assert.match(api, /\/api\/v1\/mailboxes\/oauth\/providers/)
  assert.match(api, /\/api\/v1\/mailboxes\/oauth\/start/)
  assert.match(api, /\/api\/v1\/mailboxes/)
  assert.match(settings, /mailboxApi\.startOAuth/)
  assert.match(settings, /window\.location\.assign\(result\.authorization_url\)/)
  assert.match(settings, /\$t\('Connect Gmail'\)/)
  assert.match(settings, /\$t\('Connect Microsoft'\)/)
  assert.doesNotMatch(settings, /access_token|refresh_token|client_secret/)
})

test('mailbox OAuth states and callback outcomes are bilingual', () => {
  const settings = source('src/views/Settings.vue')
  const messages = source('src/i18n/index.ts')

  assert.match(settings, /mailbox_oauth/)
  assert.match(messages, /'Connect Gmail': '连接 Gmail'/)
  assert.match(messages, /'Connect Microsoft': '连接 Microsoft'/)
  assert.match(messages, /'Mailbox connected successfully.': '邮箱连接成功。'/)
  assert.match(messages, /'Reconnect required': '需要重新连接'/)
})
