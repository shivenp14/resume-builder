import { spawn } from 'node:child_process';
import { existsSync } from 'node:fs';
import { resolve } from 'node:path';
import { withCodexExecutable } from './codex-executable.mjs';

const root = resolve(import.meta.dirname, '..');
const backendDir = resolve(root, 'backend');
const python = existsSync(resolve(backendDir, '.venv/bin/python'))
  ? resolve(backendDir, '.venv/bin/python')
  : 'python3';
const childEnv = withCodexExecutable();

const child = spawn(
  python,
  ['-m', 'uvicorn', 'app.main:app', '--reload', '--host', '127.0.0.1', '--port', '8000'],
  {
    cwd: backendDir,
    env: childEnv,
    stdio: 'inherit',
  },
);

function stop(signal) {
  if (!child.killed) child.kill(signal);
}

process.on('SIGINT', () => stop('SIGINT'));
process.on('SIGTERM', () => stop('SIGTERM'));

child.on('error', (error) => {
  console.error(`Failed to start backend: ${error.message}`);
  process.exitCode = 1;
});

child.on('exit', (code, signal) => {
  if (signal) {
    process.kill(process.pid, signal);
  } else {
    process.exitCode = code ?? 1;
  }
});
