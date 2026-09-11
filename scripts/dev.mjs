import { spawn } from 'node:child_process';
import { existsSync } from 'node:fs';
import { resolve } from 'node:path';

const root = resolve(import.meta.dirname, '..');
const backendDir = resolve(root, 'backend');
const python = existsSync(resolve(backendDir, '.venv/bin/python'))
  ? resolve(backendDir, '.venv/bin/python')
  : 'python3';

const services = [
  {
    name: 'frontend',
    command: process.platform === 'win32' ? 'npm.cmd' : 'npm',
    args: ['run', 'dev:frontend'],
    cwd: root,
  },
  {
    name: 'backend',
    command: python,
    args: ['-m', 'uvicorn', 'app.main:app', '--reload', '--host', '127.0.0.1', '--port', '8000'],
    cwd: backendDir,
  },
];

const colors = { frontend: '\u001b[36m', backend: '\u001b[35m', reset: '\u001b[0m' };
const children = new Map();
let shuttingDown = false;
let exitCode = 0;

function log(name, line) {
  const color = process.stdout.isTTY ? colors[name] : '';
  const reset = process.stdout.isTTY ? colors.reset : '';
  process.stdout.write(`${color}[${name}]${reset} ${line}\n`);
}

function stopAll(signal = 'SIGTERM') {
  if (shuttingDown) return;
  shuttingDown = true;
  for (const child of children.values()) child.kill(signal);
}

for (const service of services) {
  const child = spawn(service.command, service.args, {
    cwd: service.cwd,
    env: process.env,
    stdio: ['inherit', 'pipe', 'pipe'],
  });
  children.set(service.name, child);

  for (const stream of [child.stdout, child.stderr]) {
    let pending = '';
    stream.setEncoding('utf8');
    stream.on('data', (chunk) => {
      pending += chunk;
      const lines = pending.split(/\r?\n/);
      pending = lines.pop() ?? '';
      for (const line of lines) if (line) log(service.name, line);
    });
    stream.on('end', () => {
      if (pending) log(service.name, pending);
    });
  }

  child.on('error', (error) => {
    exitCode = 1;
    log(service.name, `failed to start: ${error.message}`);
    stopAll();
  });

  child.on('exit', (code, signal) => {
    children.delete(service.name);
    if (!shuttingDown && code !== 0) {
      exitCode = code || 1;
      log(service.name, `exited with ${signal || `code ${code}`}; stopping other services`);
      stopAll();
    }
    if (shuttingDown && children.size === 0) process.exit(exitCode);
  });
}

process.on('SIGINT', () => stopAll('SIGINT'));
process.on('SIGTERM', () => stopAll('SIGTERM'));

log('frontend', 'starting Vite on http://127.0.0.1:5173');
log('backend', 'starting FastAPI on http://127.0.0.1:8000');
