const { spawn } = require('node:child_process');
const { createHash } = require('node:crypto');
const { existsSync, readFileSync, readdirSync, writeFileSync } = require('node:fs');
const { join, relative } = require('node:path');

const frontend = join(__dirname, '..');
const output = join(frontend, 'dist-desktop');
const stamp = join(output, '.verbatrail-source');

function sourceFiles(directory) {
  return readdirSync(directory, { withFileTypes: true }).flatMap(entry => {
    const path = join(directory, entry.name);
    return entry.isDirectory() ? sourceFiles(path) : [path];
  });
}

function fingerprint() {
  const config = readdirSync(frontend).filter(name =>
    /^(?:index\.html|package(?:-lock)?\.json|tsconfig.*\.json|(?:vite|tailwind|postcss)\.config\..+|\.env(?:\..+)?)$/.test(name));
  const files = [
    ...sourceFiles(join(frontend, 'src')), ...sourceFiles(join(frontend, 'public')),
    ...config.map(name => join(frontend, name)), __filename,
  ];
  const hash = createHash('sha256');
  for (const file of files.sort()) {
    hash.update(relative(frontend, file)).update('\0');
    hash.update(createHash('sha256').update(readFileSync(file)).digest());
  }
  return hash.digest('hex');
}

function run(node, entry, args, env) {
  return new Promise((resolve, reject) => {
    const child = spawn(node, [join(frontend, 'node_modules', ...entry), ...args], {
      cwd: frontend, env, windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'],
    });
    let tail = '';
    const capture = stream => chunk => {
      stream.write(chunk);
      tail = (tail + chunk).slice(-4000);
    };
    child.stdout.on('data', capture(process.stdout));
    child.stderr.on('data', capture(process.stderr));
    child.once('error', reject);
    child.once('close', code => {
      if (code === 0) resolve();
      else reject(new Error(`桌面前端构建失败（${code}）。\n${tail}`));
    });
  });
}

async function ensureDesktopBuild({ electron = false, force = false } = {}) {
  const source = fingerprint();
  const index = join(output, 'index.html');
  if (!force && existsSync(index) && existsSync(stamp) && readFileSync(stamp, 'utf8') === source) return;

  console.log('正在更新 VerbaTrail 桌面前端…');
  const env = electron ? { ...process.env, ELECTRON_RUN_AS_NODE: '1' } : process.env;
  await run(process.execPath, ['typescript', 'bin', 'tsc'], ['-b'], env);
  await run(process.execPath, ['vite', 'bin', 'vite.js'], ['build', '--mode', 'desktop'], env);
  if (!existsSync(index) || fingerprint() !== source) {
    throw new Error('桌面前端构建期间源码已变化或缺少入口文件。请重新打开 VerbaTrail。');
  }
  writeFileSync(stamp, source, 'utf8');
}

module.exports = { ensureDesktopBuild, output };

if (require.main === module) {
  ensureDesktopBuild({ force: true }).catch(error => {
    console.error(error);
    process.exitCode = 1;
  });
}
