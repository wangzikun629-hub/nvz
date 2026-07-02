import fs from 'node:fs'
import path from 'node:path'
import assert from 'node:assert/strict'

const appPath = path.resolve('src/App.vue')
const source = fs.readFileSync(appPath, 'utf8')

const createNewSessionBlock = source.match(/const createNewSession = \(\) => \{[\s\S]*?\n    \}/)?.[0] || ''
assert.ok(createNewSessionBlock, 'createNewSession block should exist')
assert.ok(
  !createNewSessionBlock.includes('sessions.value = ['),
  'createNewSession should not insert an empty local draft into history list'
)

assert.ok(
  !source.includes('当前活跃会话始终置顶') && !source.includes('sorted.unshift(sorted.splice(idx, 1)[0])'),
  'fetchUserSessions should preserve data ordering instead of forcing active session to the top'
)

const clearPendingBlock = source.match(/const clearPendingSessionState = \(sessionId\) => \{[\s\S]*?\n    \}/)?.[0] || ''
assert.ok(clearPendingBlock, 'clearPendingSessionState block should exist')
assert.ok(
  !clearPendingBlock.includes('!pendingSessionStates.value[sessionId]') || clearPendingBlock.indexOf('clearPersistedSessionDraft(sessionId)') < clearPendingBlock.indexOf('return'),
  'clearPendingSessionState should always remove persisted localStorage draft'
)
