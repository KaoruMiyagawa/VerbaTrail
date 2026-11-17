import assert from 'node:assert/strict';
import { writeFile } from 'node:fs/promises';
import { join } from 'node:path';

const { SMOKE_UI_URL: ui, VITE_API_BASE_URL: api, SMOKE_CDP_URL: primary,
  SMOKE_SECOND_CDP: second, SMOKE_OUTPUT_DIR: out } = process.env;
const runs = JSON.parse(process.env.SMOKE_RUNS);
const root = '/api/v1/assessment-runs';
const speaking = id => `${root}/${id}/speaking`;
const report = { status: 'running', risks: Object.fromEntries(['response_loss', 'isolation', 'faults', 'mobile'].map(key => [key, 'not_run'])),
  runs, registrations: {}, requests: [], timeline: [], screenshots: [], checks: {} };
const clients = [];
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
const q = JSON.stringify;
const panel = "document.querySelector('[data-assessment-skill=\"speaking\"]')";
const audio = `${panel}?.querySelector('audio')`;
const savedMessage = '本地录音已保存，尚未登记';
const doneMessage = '任务已登记 · 口语能力未评分';
const missingMessage = '录音任务已登记，但当前浏览器没有可播放的本地录音';
const selected = "localStorage.getItem('assessment:selected:default_learner')";
const button = (label, scope = 'document', exact = true) =>
  `Array.from((${scope})?.querySelectorAll('button')??[]).find(e=>!e.disabled&&e.getClientRects().length&&${exact ? 'e.textContent.trim()===' + q(label) : 'e.textContent.includes(' + q(label) + ')'})`;
const log = (event, details = {}) => report.timeline.push({ at_ms: Date.now(), event, ...details });

async function connect(url, profile) {
  const target = (await (await fetch(url + '/json/list')).json()).find(item => item.type === 'page');
  const socket = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((resolve, reject) => { socket.onopen = resolve; socket.onerror = reject; });
  const calls = new Map(), trace = new Map();
  let serial = 0;
  const send = (method, params = {}) => new Promise((resolve, reject) => {
    const id = ++serial;
    const timer = setTimeout(() => { calls.delete(id); reject(Error(`CDP ${method} timeout`)); }, 20000);
    calls.set(id, reply => { clearTimeout(timer); reply.error ? reject(Error(JSON.stringify(reply.error))) : resolve(reply.result); });
    socket.send(q({ id, method, params }));
  });
  const client = { send, profile, socket, onPause: null, mobile: false, scrolls: [], taps: [], async permission(setting) {
    await send('Browser.setPermission', { permission: { name: 'microphone' }, setting, origin: ui });
  }};
  clients.push(client);
  let interceptError;
  socket.onmessage = ({ data }) => {
    const msg = JSON.parse(data);
    if (msg.id) { calls.get(msg.id)?.(msg); calls.delete(msg.id); return; }
    const p = msg.params;
    if (msg.method === 'Network.requestWillBeSent') {
      const path = new URL(p.request.url).pathname;
      if (!path.startsWith('/api/v1/')) return;
      const entry = { profile, method: p.request.method, path, at_ms: Date.now(), body: p.request.postData };
      trace.set(p.requestId, entry);
      report.requests.push(entry);
    } else if (msg.method === 'Network.responseReceived' && trace.has(p.requestId)) {
      trace.get(p.requestId).status = p.response.status;
    } else if (msg.method === 'Network.loadingFailed' && trace.has(p.requestId)) {
      trace.get(p.requestId).failure = p.errorText;
    } else if (msg.method === 'Fetch.requestPaused') {
      Promise.resolve().then(async () => {
        if (await client.onPause?.(p)) return;
        await send(p.responseStatusCode ? 'Fetch.continueResponse' : 'Fetch.continueRequest', { requestId: p.requestId });
      }).catch(error => { interceptError = error; });
    }
  };
  client.ev = async expression => {
    if (interceptError) throw interceptError;
    const reply = await send('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true });
    if (reply.exceptionDetails) throw Error(reply.result.description ?? 'Browser expression failed');
    return reply.result.value;
  };
  client.wait = async expression => {
    for (let i = 0; i < 110; i++) {
      try { if (await client.ev(expression)) return; }
      catch (error) { if (!/context|Cannot read properties/i.test(String(error))) throw error; }
      await pause(100);
    }
    throw Error(`Timed out: ${expression}`);
  };
  client.text = label => client.wait(`${panel}?.innerText.includes(${q(label)})`);
  client.visibleDone = () => client.wait(`${panel}?.querySelector('h3')?.textContent===${q(doneMessage)}`);
  client.shot = async name => {
    const { data } = await send('Page.captureScreenshot', { format: 'png' });
    await writeFile(join(out, name), Buffer.from(data, 'base64'));
    report.screenshots.push(name);
  };
  for (const domain of ['Runtime', 'Page', 'Network']) await send(`${domain}.enable`);
  await send('Page.addScriptToEvaluateOnNewDocument', { source: `(${probe.toString()})()` });
  return client;
}

// Only observe or fault-inject at browser media and storage interfaces.
function probe() {
  const log = window.c3 = { media: [], chunks: [], tracks: [], abortedSaves: 0, pendingReads: [] };
  if (navigator.mediaDevices) {
    const get = navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
    navigator.mediaDevices.getUserMedia = async constraints => {
      try {
        const stream = await get(constraints);
        log.tracks.push(...stream.getTracks());
        log.media.push({ acquired: stream.active, tracks: stream.getTracks().length });
        return stream;
      } catch (error) { log.media.push({ error: error.name }); throw error; }
    };
    const Recorder = MediaRecorder;
    window.MediaRecorder = class extends Recorder {
      constructor(...args) {
        super(...args);
        this.addEventListener('dataavailable', event => {
          log.chunks.push({ bytes: event.data.size, mime: event.data.type, discarded: !!log.discardData });
          if (log.discardData) event.stopImmediatePropagation();
        });
      }
    };
  }
  const put = IDBObjectStore.prototype.put;
  IDBObjectStore.prototype.put = function (...args) {
    const request = put.apply(this, args);
    if (log.abortNextSave && this.transaction.db.name === 'verbatrail-speaking-assessment-v1') {
      log.abortNextSave = false;
      log.abortedSaves++;
      this.transaction.abort();
    }
    return request;
  };
  const held = new WeakMap();
  const getAll = IDBIndex.prototype.getAll;
  IDBIndex.prototype.getAll = function (key, ...args) {
    const request = getAll.call(this, key, ...args);
    if (this.objectStore.transaction.db.name === 'verbatrail-speaking-assessment-v1' && log.holdLocalRun && key?.[1] === log.holdLocalRun) {
      held.set(this.objectStore.transaction, log.holdLocalRun);
      log.holdLocalRun = null;
    }
    return request;
  };
  const completion = Object.getOwnPropertyDescriptor(IDBTransaction.prototype, 'oncomplete');
  Object.defineProperty(IDBTransaction.prototype, 'oncomplete', { ...completion, set(handler) {
    const run = held.get(this);
    completion.set.call(this, run ? function (event) {
      log.pendingReads.push({ run, release: () => handler.call(this, event) });
    } : handler);
  }});
}

const localRows = `new Promise((resolve,reject)=>{
  const request=indexedDB.open('verbatrail-speaking-assessment-v1',1);
  request.onupgradeneeded=()=>request.transaction.abort();
  request.onerror=()=>reject(request.error);
  request.onsuccess=()=>{const db=request.result;const tx=db.transaction('recordings');const store=tx.objectStore('recordings');const all=store.getAll();
    tx.oncomplete=()=>{db.close();resolve(all.result.map(({runId,blob,metadata,registration})=>({runId,bytes:blob.size,metadata,registration})));};
  };
})`;
const rows = c => c.ev(localRows);
const forRun = async (c, run) => (await rows(c)).find(row => row.runId === run);
const apiRead = async path => {
  const response = await fetch(api + path);
  assert.equal(response.status, 200);
  return response.json();
};

async function point(c, expression, inset = null) {
  return c.ev(`(()=>{const el=(${expression});if(!el)return null;const r=el.getBoundingClientRect();
    const x=r.left+${inset ?? 'r.width/2'}, y=r.top+r.height/2, top=document.elementFromPoint(x,y);
    const header=document.querySelector('.focus-top-nav');
    const boundary=header?.contains(el)?0:(header?.getBoundingClientRect().bottom??0);
    return {x,y,width:r.width,height:r.height,visible:r.top>=boundary&&r.bottom<=innerHeight&&x>=0&&x<innerWidth&&(el===top||el.contains(top))};})()`);
}

async function input(c, expression, label, inset) {
  await c.wait(`!!(${expression})`);
  if (!c.mobile) {
    await c.ev(`(${expression}).scrollIntoView({block:'center',behavior:'instant'})`);
    await pause(100);
    const { x, y } = await point(c, expression, inset);
    for (const type of ['mousePressed', 'mouseReleased']) await c.send('Input.dispatchMouseEvent', { type, button: 'left', clickCount: 1, x, y });
    return;
  }
  let box;
  for (let i = 0; i < 18; i++) {
    box = await point(c, expression, inset);
    if (box?.visible) break;
    assert.ok(box, label);
    const start = box.y > 430 ? 735 : 265;
    const end = box.y > 430 ? 285 : 720;
    const before = await c.ev('scrollY');
    await c.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [{ x: 385, y: start }] });
    for (let step = 1; step <= 7; step++) {
      await pause(45);
      await c.send('Input.dispatchTouchEvent', { type: 'touchMove', touchPoints: [{ x: 385, y: start + (end - start) * step / 7 }] });
    }
    await c.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] });
    await pause(300);
    c.scrolls.push({ label, start, end, before, after: await c.ev('scrollY') });
  }
  assert.ok(box?.visible, `Hidden or obscured touch target ${label}: ${q(box)}`);
  const { x, y } = box;
  await c.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [{ x, y }] });
  await c.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] });
  c.taps.push({ label, x, y });
}

const click = (c, label, scope, exact) => input(c, button(label, scope, exact), label);
const taskClick = (c, label) => click(c, label, panel);
async function open(c, run) {
  await c.send('Page.navigate', { url: ui + '/learn/assessment' });
  await c.wait(`document.title.length>0&&document.querySelector('.assessment-page')!==null`);
  // Select the corresponding history button; the order is verified against the real API.
  await selectRun(c, run);
  await click(c, 'Speaking：', 'document', false);
}
async function selectRun(c, run) {
  const history = await apiRead(root);
  const index = history.findIndex(item => item.run_id === run);
  assert.ok(index >= 0);
  await input(c, `Array.from(document.querySelectorAll('.assessment-page > section:first-of-type li button'))[${index}]?.matches(':enabled')&&document.querySelectorAll('.assessment-page > section:first-of-type li button')[${index}]`, `history:${run}`);
  await c.wait(`${selected}===${q(run)}`);
}
async function record(c, label = '开始录音', beforeStop = () => {}) {
  await taskClick(c, label);
  await c.text('正在录音');
  await pause(1200);
  await beforeStop();
  await taskClick(c, '停止录音');
}
async function play(c) {
  await c.wait(`${audio}?.readyState>=2`);
  await input(c, audio, 'native audio play', 23);
  await c.wait(`${audio}?.currentTime>0.2`);
  const reading = await c.ev(`({time:${audio}.currentTime, local:${audio}.currentSrc.startsWith('blob:')})`);
  assert.equal(reading.local, true);
  return reading;
}
const postsFor = id => report.requests.filter(item => item.method === 'POST' && item.path === speaking(id) + '/complete');
const requestPattern = (id, suffix, stage) => ({ urlPattern: `*${speaking(id)}${suffix}`, requestStage: stage });
async function reload(c) { await c.send('Page.reload'); await pause(350); }

async function recoverLostResponse(c) {
  await open(c, runs.a);
  await taskClick(c, '开始 Speaking');
  await record(c);
  await c.text(savedMessage);
  const local = await forRun(c, runs.a);
  assert.ok(local.bytes > 0 && local.registration === 'local');
  const observed = await c.ev('({media:c3.media,chunks:c3.chunks,tracks:c3.tracks.map(t=>t.readyState)})');
  assert.ok(observed.chunks.some(chunk => chunk.bytes > 0));
  assert.deepEqual(observed.tracks, ['ended']);
  await reload(c);
  await c.text(savedMessage);
  assert.deepEqual(await forRun(c, runs.a), local);
  const intercepted = [];
  c.onPause = async p => {
    if (p.request.method === 'OPTIONS' || intercepted.length >= 2) return false;
    const entry = { kind: p.request.method === 'POST' ? 'committed_post_response' : 'automatic_reconciliation_get_response', status: p.responseStatusCode };
    assert.equal(entry.status, 200);
    intercepted.push(entry);
    log('response_dropped', entry);
    await c.send('Fetch.failRequest', { requestId: p.requestId, errorReason: 'ConnectionReset' });
    return true;
  };
  await c.send('Fetch.enable', { patterns: [requestPattern(runs.a, '/complete', 'Response'), requestPattern(runs.a, '?*', 'Response')] });
  await taskClick(c, '登记本次录音任务');
  await c.text('录音登记尚未确认，本地录音已保留');
  assert.equal(await c.ev(`!!${panel}.querySelector('h3')`), false);
  assert.deepEqual(intercepted.map(entry => entry.kind), ['committed_post_response', 'automatic_reconciliation_get_response']);
  const upstream = await apiRead(speaking(runs.a));
  assert.equal(upstream.task_status, 'completed');
  assert.equal(upstream.recording.recording_id, local.metadata.recording_id);
  assert.deepEqual(await forRun(c, runs.a), { ...local, registration: 'pending' });
  await c.shot('response-lost-pending.png');
  await c.send('Fetch.disable');
  const retry = report.requests.length;
  log('manual_retry');
  await taskClick(c, '重试登记录音任务');
  await c.visibleDone();
  assert.equal(report.requests.slice(retry).find(item => item.path.startsWith(speaking(runs.a))).method, 'GET');
  assert.equal(postsFor(runs.a).length, 1);
  const firstPlayback = await play(c);
  await reload(c);
  await c.visibleDone();
  const refreshedPlayback = await play(c);
  assert.deepEqual(await forRun(c, runs.a), { ...local, registration: 'registered' });
  report.registrations[runs.a] = local.metadata;
  report.checks.response_loss = { local, observed, intercepted, upstream, firstPlayback, refreshedPlayback, browser_complete_posts: postsFor(runs.a).length };
}

async function isolate(c, fresh) {
  await selectRun(c, runs.b);
  await taskClick(c, '开始 Speaking');
  await c.text('当前浏览器尚无录音');
  assert.equal(await c.ev(`!!(${audio})`), false);
  assert.equal(await forRun(c, runs.b), undefined);
  const a = await apiRead(speaking(runs.a));
  const b = await apiRead(speaking(runs.b));
  assert.equal(a.task_version, b.task_version);
  assert.equal(b.recording, null);

  let held;
  c.onPause = p => {
    if (!held && p.request.method === 'GET' && p.responseStatusCode === 200) {
      held = p.requestId;
      log('A_http_response_held', { run: runs.a, status: p.responseStatusCode });
      return true;
    }
    return false;
  };
  await c.send('Fetch.enable', { patterns: [requestPattern(runs.a, '?*', 'Response')] });
  await selectRun(c, runs.a);
  for (let i = 0; !held && i < 60; i++) await pause(100);
  assert.ok(held, 'expected a real A task response to be held');
  await selectRun(c, runs.b);
  await c.text('当前浏览器尚无录音');
  await c.send('Fetch.continueResponse', { requestId: held });
  log('A_http_response_released_after_B_selection');
  await pause(300);
  await c.text('当前浏览器尚无录音');
  assert.equal(await c.ev(selected), runs.b);
  assert.equal(await c.ev(`!!(${audio})`), false);
  await c.send('Fetch.disable');

  await c.ev(`c3.holdLocalRun=${q(runs.a)}`);
  await selectRun(c, runs.a);
  await c.wait('c3.pendingReads.length===1');
  log('A_IndexedDB_completion_held');
  await selectRun(c, runs.b);
  await c.text('当前浏览器尚无录音');
  await c.ev('c3.pendingReads.shift().release()');
  log('A_IndexedDB_completion_released_after_B_selection');
  await pause(300);
  assert.equal(await c.ev(selected), runs.b);
  assert.equal(await c.ev(`!!(${audio})`), false);
  await c.text('当前浏览器尚无录音');

  await selectRun(c, runs.legacy);
  await c.text('此 Speaking 任务的历史版本或登记记录无法恢复');
  assert.ok(report.requests.some(req => req.path === speaking(runs.legacy) && req.status === 409));
  const legacy = await apiRead(`${root}/${runs.legacy}`);
  assert.ok(legacy.tasks.every(task => task.status === 'not_started'));
  await selectRun(c, runs.a);
  await c.visibleDone();
  await click(c, 'Reading：completed');
  await c.wait("document.querySelector('[data-assessment-skill=reading]:not([hidden])')?.innerText.includes('本次答对 4 / 4')");
  await click(c, 'Listening：completed');
  await c.wait("document.querySelector('[data-assessment-skill=listening]:not([hidden])')?.innerText.includes('本次答对 6 / 6')");
  await c.wait("document.querySelector('[data-assessment-skill=listening] audio')?.readyState>=2");
  await click(c, '← 返回 Learn');
  await input(c, "document.querySelector('a[href=\"/learn/speaking\"]')", 'ordinary Speaking');
  await c.wait("document.querySelector('#speaking-prompt')?.options.length>0");
  assert.equal(await c.ev(`!!${panel}`), false);
  await open(c, runs.b);
  await c.text('当前浏览器尚无录音');

  await open(fresh, runs.a);
  await fresh.text(missingMessage);
  await fresh.visibleDone();
  assert.equal(await fresh.ev(`!!(${audio})`), false);
  assert.deepEqual(await rows(fresh), []);
  await fresh.shot('separate-profile-no-audio.png');
  report.checks.isolation = { a, b, legacy, fresh_profile_has_blob: false,
    healthy_after_409: ['Reading 4/4', 'Listening 6/6 and audio loaded', 'ordinary Speaking prompt catalogue'] };
}

async function exerciseFaults(c) {
  await c.permission('denied');
  await taskClick(c, '开始录音');
  await c.text('麦克风权限被拒绝');
  assert.deepEqual(await c.ev('c3.media.at(-1)'), { error: 'NotAllowedError' });
  assert.equal(await c.ev('c3.tracks.length'), 0);
  const denied = await c.ev(`${panel}.querySelector('[role=alert]').innerText`);
  await c.permission('granted');
  await taskClick(c, '开始录音');
  await c.text('正在录音');
  await c.ev('c3.tracks.at(-1).stop()');
  await c.text('录音已中断，本次录音未保存');
  assert.equal(await c.ev('c3.tracks.at(-1).readyState'), 'ended');
  assert.equal(await forRun(c, runs.b), undefined);
  const interrupted = await c.ev(`${panel}.querySelector('[role=alert]').innerText`);

  await c.ev('c3.discardData=true');
  await record(c);
  await c.text('录音为空或时间太短');
  assert.equal(await c.ev('c3.chunks.at(-1).discarded'), true);
  await c.ev('c3.discardData=false');
  assert.equal(await forRun(c, runs.b), undefined);
  assert.equal(postsFor(runs.b).length, 0);

  await c.ev('c3.abortNextSave=true');
  await record(c);
  await c.text('录音保存失败');
  assert.equal(await c.ev('c3.abortedSaves'), 1);
  assert.equal(await forRun(c, runs.b), undefined);
  assert.equal(await c.ev(`!!(${button('登记本次录音任务', panel)})`), false);
  assert.equal(postsFor(runs.b).length, 0);
  const storage = await c.ev(`${panel}.querySelector('[role=alert]').innerText`);
  await c.shot('indexeddb-save-fault.png');
  await taskClick(c, '重试保存录音');
  await c.text(savedMessage);
  const saved = await forRun(c, runs.b);
  assert.ok(saved.bytes > 0);
  assert.notEqual(saved.metadata.recording_id, report.registrations[runs.a].recording_id);
  let blocked = 0;
  c.onPause = async p => {
    if (p.request.method !== 'POST') return false;
    blocked++;
    log('B_post_blocked_before_upstream', { run: runs.b });
    await c.send('Fetch.failRequest', { requestId: p.requestId, errorReason: 'ConnectionReset' });
    return true;
  };
  await c.send('Fetch.enable', { patterns: [requestPattern(runs.b, '/complete', 'Request')] });
  await taskClick(c, '登记本次录音任务');
  await c.text('录音登记尚未确认，本地录音已保留');
  assert.equal(blocked, 1);
  assert.equal((await apiRead(speaking(runs.b))).task_status, 'in_progress');
  assert.deepEqual(await forRun(c, runs.b), { ...saved, registration: 'pending' });
  const pendingPlayback = await play(c);
  await c.send('Fetch.disable');
  await reload(c);
  await c.text('本地录音已保存，登记尚未确认');
  assert.deepEqual(await forRun(c, runs.b), { ...saved, registration: 'pending' });
  const retry = report.requests.length;
  await taskClick(c, '重试登记录音任务');
  await c.visibleDone();
  const retried = report.requests.slice(retry).filter(req => req.path.startsWith(speaking(runs.b)));
  assert.deepEqual(retried.slice(0, 2).map(req => req.method), ['GET', 'POST']);
  assert.equal(postsFor(runs.b).length, 2);
  assert.deepEqual(postsFor(runs.b).map(req => JSON.parse(req.body)), [saved.metadata, saved.metadata]);
  assert.deepEqual(await forRun(c, runs.b), { ...saved, registration: 'registered' });
  report.registrations[runs.b] = saved.metadata;
  report.checks.faults = { denied, interrupted, storage, blocked, pendingPlayback, saved,
    empty_capture_rejected: true, browser_complete_posts: postsFor(runs.b).length };
}

async function tabTo(c, label) {
  for (let i = 0; i < 48; i++) {
    await c.send('Input.dispatchKeyEvent', { type: 'rawKeyDown', key: 'Tab', code: 'Tab', windowsVirtualKeyCode: 9 });
    await c.send('Input.dispatchKeyEvent', { type: 'keyUp', key: 'Tab', code: 'Tab', windowsVirtualKeyCode: 9 });
    if (!await c.ev(`document.activeElement.tagName==='BUTTON'&&document.activeElement.textContent.trim()===${q(label)}`)) continue;
    await pause(500); // Let the browser finish its native smooth focus scroll.
    const result = await c.ev(`(()=>{const el=document.activeElement;
      const style=getComputedStyle(el);const r=el.getBoundingClientRect();const header=document.querySelector('.focus-top-nav');
      return {label:el.textContent.trim(),focus_visible:el.matches(':focus-visible'),outline:style.outlineStyle,outline_width:style.outlineWidth,
        top:r.top,bottom:r.bottom,header_bottom:header?.getBoundingClientRect().bottom??null,
        actionable:el.contains(document.elementFromPoint(r.left+r.width/2,r.top+r.height/2))};})()`);
    assert.ok(result.focus_visible && result.outline !== 'none' && parseFloat(result.outline_width) > 0, q(result));
    assert.ok(result.actionable && result.top >= result.header_bottom && result.bottom <= 844, q(result));
    return result;
  }
  throw Error(`Tab could not reach ${label}`);
}

async function touchTheme(c, theme) {
  await c.wait(`document.documentElement.dataset.theme===${q(theme)}`);
  const start = c.taps.length, scrollStart = c.scrolls.length;
  const oldRun = await c.ev(selected);
  await click(c, '开始新一轮评估');
  await c.wait(`!!${selected}&&${selected}!==${q(oldRun)}`);
  const run = await c.ev(selected);
  await click(c, 'Speaking：not_started');
  const focus = [await tabTo(c, '开始 Speaking')];
  await taskClick(c, '开始 Speaking');
  await c.text('Your neighborhood is starting a weekly skill exchange.');
  focus.push(await tabTo(c, '开始录音'));
  await record(c, '开始录音', async () => { focus.push(await tabTo(c, '停止录音')); });
  await c.text(savedMessage);
  const local = await forRun(c, run);
  assert.ok(local.bytes > 0);
  const playback = await play(c);
  focus.push(await tabTo(c, '登记本次录音任务'));
  await taskClick(c, '登记本次录音任务');
  await c.visibleDone();
  await reload(c);
  await c.visibleDone();
  await c.wait(`${audio}?.readyState>=2`);
  assert.deepEqual(await forRun(c, run), { ...local, registration: 'registered' });
  const layout = await c.ev(`({width:innerWidth,height:innerHeight,scrollWidth:document.documentElement.scrollWidth,theme:document.documentElement.dataset.theme})`);
  assert.equal(layout.width, 390);
  assert.equal(layout.height, 844);
  assert.ok(layout.scrollWidth <= 390);
  const target = `${panel}.querySelector('h3')`;
  // Bring the result into the touch viewport using the same gesture helper.
  await input(c, target, 'registered result');
  await c.shot(`touch-${theme}-restored.png`);
  report.registrations[run] = local.metadata;
  return { theme, run, layout, record: local, playback, focus, taps: c.taps.slice(start), gestures: c.scrolls.slice(scrollStart) };
}

let current;
try {
  const c = await connect(primary, 'primary');
  const fresh = await connect(second, 'separate-profile');
  await c.send('Emulation.setDeviceMetricsOverride', { width: 1440, height: 1000, deviceScaleFactor: 1, mobile: false });
  await c.permission('granted');
  current = 'response_loss'; await recoverLostResponse(c); report.risks[current] = 'pass';
  current = 'isolation'; await isolate(c, fresh); report.risks[current] = 'pass';
  current = 'faults'; await exerciseFaults(c); report.risks[current] = 'pass';
  current = 'mobile';
  await fresh.permission('granted');
  fresh.mobile = true;
  await fresh.send('Emulation.setDeviceMetricsOverride', { width: 390, height: 844, deviceScaleFactor: 1, mobile: true });
  await fresh.send('Emulation.setTouchEmulationEnabled', { enabled: true, maxTouchPoints: 1 });
  report.checks.mobile = [await touchTheme(fresh, 'premium')];
  await input(fresh, "document.querySelector('.focus-theme-toggle')", 'theme toggle');
  report.checks.mobile.push(await touchTheme(fresh, 'noir'));
  report.risks[current] = 'pass';
  current = 'regression';
  const model = await apiRead('/api/v1/learner-model');
  const speech = model.skills.find(skill => skill.skill_id === 'speaking');
  assert.equal(speech.assessment_status, 'not_assessed');
  assert.deepEqual(speech.evidence_levels, []);
  assert.equal(speech.evidence.length, 4);
  assert.ok(speech.evidence.every(item => item.evidence_type === 'assessment_activity' && item.evidence_validity === 'not_evaluated'));
  for (const metadata of Object.values(report.registrations)) {
    assert.deepEqual(Object.keys(metadata).sort(), ['learner_id', 'recording_id', 'task_version', 'prompt_id', 'prompt_version', 'duration_seconds', 'recorded_at', 'idempotency_key'].sort());
  }
  for (const client of [c, fresh]) {
    assert.equal(await client.ev(`(/IELTS\s*Band\s*[:：]?\s*\d|(?:流利度|发音|准确度)\s*[:：]?\s*\d/).test(${panel}.innerText)`), false);
  }
  report.checks.learner_model = speech;
  report.status = 'pass';
} catch (error) {
  report.status = 'fail';
  report.failed_stage = current;
  report.error = String(error);
  if (current in report.risks) report.risks[current] = 'fail';
  report.mobile_progress = clients[1] ? { taps: clients[1].taps, gestures: clients[1].scrolls } : null;
  await clients[current === 'mobile' ? 1 : 0]?.shot('failure.png').catch(() => {});
  throw error;
} finally {
  for (const client of clients) client.socket.close();
  await writeFile(join(out, 'browser.json'), JSON.stringify(report, null, 2) + '\n');
}
