import assert from 'node:assert/strict'
import { spawnSync } from 'node:child_process'
import { fileURLToPath } from 'node:url'
import path from 'node:path'
import test from 'node:test'

const frontendDir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')

test('the frontend passes Vue and TypeScript type checking', () => {
  const result = spawnSync(
    process.execPath,
    ['node_modules/vue-tsc/bin/vue-tsc.js', '--noEmit'],
    {
      cwd: frontendDir,
      encoding: 'utf8',
    }
  )

  assert.equal(
    result.status,
    0,
    [result.stdout, result.stderr].filter(Boolean).join('\n')
  )
})
