const { spawn } = require('node:child_process');
const { existsSync, statSync } = require('node:fs');
const { isAbsolute, join } = require('node:path');
const { createInterface } = require('node:readline');
const { app, BrowserWindow, dialog, screen, shell } = require('electron');
const { ensureDesktopBuild, output: build } = require('./build.cjs');

const NAME = 'VerbaTrail';
const APP_ID = 'VerbaTrail.Desktop';
const PORT = 18191;
const ORIGIN = `http://127.0.0.1:${PORT}`;
const frontend = join(__dirname, '..');
const project = join(frontend, '..');
const icon = join(frontend, 'public', 'brand', 'verbatrail.ico');
const launchArgs = `"${frontend}"`;

app.setName(NAME);
app.setAppUserModelId(APP_ID);

// Keep local development and desktop verification sessions out of the usual user data.
const profile = process.env.VERBATRAIL_DESKTOP_PROFILE_DIR;
if (profile) {
  if (!isAbsolute(profile) || !statSync(profile, { throwIfNoEntry: false })?.isDirectory()) {
    throw new Error('隔离桌面数据目录必须是已存在的绝对路径。');
  }
  app.setPath('userData', profile);
}

let backend;
let window;
let quitting = false;

function createShortcut() {
  const path = join(app.getPath('desktop'), `${NAME}.lnk`);
  const written = shell.writeShortcutLink(path, {
    target: process.execPath,
    args: launchArgs,
    cwd: frontend,
    description: NAME,
    icon,
    iconIndex: 0,
    appUserModelId: APP_ID,
  });
  if (!written) throw new Error(`无法创建快捷方式：${path}`);
  console.log(path);
}

function startBackend() {
  const backendDir = join(project, 'backend');
  const python = [
    join(project, '.venv', 'Scripts', 'python.exe'),
    join(backendDir, '.venv', 'Scripts', 'python.exe'),
  ].find(existsSync);
  if (!python) throw new Error('找不到 Python 虚拟环境。请按 Windows 快速启动指南安装后端依赖。');

  const database = join(app.getPath('userData'), 'desktop.sqlite3').replaceAll('\\', '/');
  backend = spawn(python, ['-m', 'scripts.desktop_server', build, String(PORT)], {
    cwd: backendDir,
    windowsHide: true,
    stdio: ['pipe', 'pipe', 'pipe'],
    env: {
      ...process.env,
      APP_NAME: NAME,
      APP_ENV: 'development',
      API_V1_PREFIX: '/api/v1',
      DATABASE_URL: `sqlite:///${database}`,
      CORS_ORIGINS: ORIGIN,
      MODEL_PROVIDER: 'fake',
      MODEL_API_KEY: '',
      ALLOW_REMOTE_MODEL_CALLS: 'false',
      PYTHONIOENCODING: 'utf-8',
    },
  });
  backend.stdin.on('error', () => {});

  let stderr = '';
  backend.stderr.setEncoding('utf8');
  backend.stderr.on('data', chunk => { stderr = (stderr + chunk).slice(-4000); });

  return new Promise((resolve, reject) => {
    const lines = createInterface({ input: backend.stdout });
    const timeout = setTimeout(() => reject(new Error(`本地服务启动超时。\n${stderr}`)), 30000);
    lines.on('line', line => {
      if (line === 'VERBATRAIL_DESKTOP_READY') {
        clearTimeout(timeout);
        resolve();
      }
    });
    backend.once('error', error => {
      clearTimeout(timeout);
      reject(error);
    });
    backend.once('close', code => {
      backend = null;
      clearTimeout(timeout);
      reject(new Error(`本地服务已退出（${code}）。\n${stderr}`));
      if (window && !quitting) {
        dialog.showErrorBox(NAME, `本地服务已退出（${code}）。\n${stderr}`);
        app.quit();
      }
    });
  });
}

async function openWindow() {
  await ensureDesktopBuild({ electron: true });
  await startBackend();
  const { width, height } = screen.getPrimaryDisplay().workAreaSize;
  window = new BrowserWindow({
    title: NAME,
    icon,
    width: Math.min(1280, width),
    height: Math.min(880, height),
    minWidth: Math.min(640, width),
    minHeight: Math.min(480, height),
    show: false,
    webPreferences: { sandbox: true, contextIsolation: true, nodeIntegration: false },
  });
  window.setMenu(null);
  window.setAppDetails({
    appId: APP_ID,
    appIconPath: icon,
    appIconIndex: 0,
    relaunchCommand: `"${process.execPath}" ${launchArgs}`,
    relaunchDisplayName: NAME,
  });
  window.on('page-title-updated', event => event.preventDefault());
  window.webContents.on('will-navigate', (event, url) => {
    if (new URL(url).origin !== ORIGIN) event.preventDefault();
  });
  window.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));
  window.once('ready-to-show', () => window.show());
  await window.loadURL(ORIGIN);
}

app.on('before-quit', event => {
  if (!backend || quitting) return;
  event.preventDefault();
  quitting = true;
  const timeout = setTimeout(() => app.quit(), 5000);
  backend.once('close', () => {
    clearTimeout(timeout);
    app.quit();
  });
  backend.stdin.end();
});
app.on('window-all-closed', () => app.quit());

const shortcutOnly = process.argv.includes('--create-shortcut');
if (!shortcutOnly && !app.requestSingleInstanceLock()) {
  app.quit();
} else {
  app.on('second-instance', () => {
    if (!window) return;
    if (window.isMinimized()) window.restore();
    window.focus();
  });
  app.whenReady().then(async () => {
    if (shortcutOnly) {
      createShortcut();
      app.quit();
    } else {
      await openWindow();
    }
  }).catch(error => {
    console.error(error);
    dialog.showErrorBox(NAME, error.message);
    process.exitCode = 1;
    app.quit();
  });
}
