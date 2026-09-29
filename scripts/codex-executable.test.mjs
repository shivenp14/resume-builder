import assert from 'node:assert/strict';
import test from 'node:test';
import { resolve, win32 } from 'node:path';
import { withCodexExecutable } from './codex-executable.mjs';

test('selects Codex beside the active Node executable when available', () => {
  const env = withCodexExecutable({}, '/runtime/bin/node', 'linux', (path) =>
    path === resolve('/runtime/bin/codex'));

  assert.equal(env.CODEX_EXECUTABLE, resolve('/runtime/bin/codex'));
});

test('preserves an explicit Codex executable override', () => {
  let checkedForSibling = false;
  const env = withCodexExecutable(
    { CODEX_EXECUTABLE: '/custom/codex' },
    '/runtime/bin/node',
    'linux',
    () => { checkedForSibling = true; return true; },
  );

  assert.equal(env.CODEX_EXECUTABLE, '/custom/codex');
  assert.equal(checkedForSibling, false);
});

test('leaves PATH fallback intact when no sibling CLI exists', () => {
  const env = withCodexExecutable({ PATH: '/system/bin' }, '/runtime/bin/node', 'linux', () => false);

  assert.equal(env.CODEX_EXECUTABLE, undefined);
  assert.equal(env.PATH, '/system/bin');
});

test('supports Windows command shims beside Node', () => {
  const env = withCodexExecutable({}, 'C:\\runtime\\bin\\node.exe', 'win32',
    (path) => path.endsWith('codex.cmd'));

  assert.equal(env.CODEX_EXECUTABLE, win32.resolve('C:\\runtime\\bin\\codex.cmd'));
});
