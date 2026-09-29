import { existsSync } from 'node:fs';
import { posix, win32 } from 'node:path';

/** Copy an environment with the Codex CLI matching the active Node runtime. */
export function withCodexExecutable(
  sourceEnv = process.env,
  executablePath = process.execPath,
  platform = process.platform,
  exists = existsSync,
) {
  const env = { ...sourceEnv };
  if (env.CODEX_EXECUTABLE) return env;

  const path = platform === 'win32' ? win32 : posix;
  const binDir = path.dirname(executablePath);
  const candidates = platform === 'win32'
    ? ['codex.exe', 'codex.cmd']
    : ['codex'];
  for (const name of candidates) {
    const candidate = path.resolve(binDir, name);
    if (exists(candidate)) {
      env.CODEX_EXECUTABLE = candidate;
      break;
    }
  }
  return env;
}
