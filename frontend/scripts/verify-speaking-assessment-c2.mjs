import assert from 'node:assert/strict';
import { writeFile } from 'node:fs/promises';
import { join } from 'node:path';

const { SMOKE_UI_URL: ui, SMOKE_CDP_URL: cdp, SMOKE_OUTPUT_DIR: out } = process.env;
const target = (await (await fetch(cdp + '/json/list')).json()).find(item => item.type === 'page');
const socket = new WebSocket(target.webSocketDebuggerUrl);
await new Promise((resolve, reject) => { socket.onopen = resolve; socket.onerror = reject; });
const pending = new Map(), requests = new Map(), checks = [], screenshots = [];
let seq = 0;
const send = (method, params = {}) => new Promise((resolve, reject) => {
  const id = ++seq;
  const timer = setTimeout(() => { pending.delete(id); reject(Error(`CDP timed out: ${method}`)); }, 20000);
  pending.set(id, message => {
    clearTimeout(timer);
    if (message.error) reject(Error(JSON.stringify(message.error)));
    else resolve(message.result);
  });
  socket.send(JSON.stringify({ id, method, params }));
});
socket.onmessage = ({ data }) => {
  const message = JSON.parse(data);
  if (message.id) { pending.get(message.id)?.(message); pending.delete(message.id); }
  if (message.method === 'Network.requestWillBeSent') {
    const { requestId, request } = message.params;
    const path = new URL(request.url).pathname;
    if (path.startsWith('/api/v1/')) requests.set(requestId, { method: request.method, path, body: request.postData });
  } else if (message.method === 'Network.responseReceived') {
    const { requestId, response } = message.params;
    if (requests.has(requestId)) requests.get(requestId).status = response.status;
  }
};

async function ev(expression) {
  const response = await send('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true });
  if (response.exceptionDetails) throw Error(response.result.description ?? 'Browser evaluation failed');
  return response.result.value;
}
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
async function wait(expression) {
  for (let i = 0; i < 160; i++) {
    try { if (await ev(expression)) return; }
    catch (error) { if (!/context|Cannot read properties/i.test(String(error))) throw error; }
    await sleep(100);
  }
  throw Error(`Timeout: ${expression}`);
}
async function pointer(selector, inset) {
  await wait(`!!(${selector})`);
  await ev(`(${selector}).scrollIntoView({block:'center',behavior:'instant'})`);
  await sleep(150);
  const point = await ev(`(()=>{const r=(${selector}).getBoundingClientRect();return {x:r.x+${inset ?? 'r.width/2'}, y:r.y+r.height/2};})()`);
  for (const type of ['mousePressed', 'mouseReleased']) {
    await send('Input.dispatchMouseEvent', { type, button: 'left', clickCount: 1, ...point });
  }
}
const button = label => pointer(`Array.from(document.querySelectorAll('button')).find(e=>e.textContent.includes(${JSON.stringify(label)})&&!e.disabled&&e.getClientRects().length)`);
const visible = "document.querySelector('[data-assessment-skill]:not([hidden])')";
const audio = "document.querySelector('audio[aria-label=\"播放初始评估本地录音\"]')";
const text = label => wait(`document.body.innerText.includes(${JSON.stringify(label)})`);
const viewport = (width, height, mobile) => send('Emulation.setDeviceMetricsOverride', { width, height, deviceScaleFactor: 1, mobile });
async function screenshot(name) {
  const { data } = await send('Page.captureScreenshot', { format: 'png' });
  await writeFile(join(out, name), Buffer.from(data, 'base64'));
  screenshots.push(name);
}
const localRows = `new Promise((resolve,reject)=>{
  const open=indexedDB.open('verbatrail-speaking-assessment-v1');
  open.onerror=()=>reject(open.error);
  open.onsuccess=()=>{
    const db=open.result, tx=db.transaction('recordings'), store=tx.objectStore('recordings'), req=store.getAll();
    const keyPath=store.keyPath;
    tx.oncomplete=()=>{db.close();resolve({key_path:keyPath,rows:req.result.map(({runId,blob,metadata,registration})=>({runId,bytes:blob.size,type:blob.type,metadata,registration}))});};
    tx.onerror=()=>{db.close();reject(tx.error);};
  };
})`;

async function play() {
  await wait(`${audio}?.readyState >= 2`);
  await pointer(audio, 22);
  await wait(`${audio}?.currentTime > 0.25`);
  return ev(`({time:${audio}.currentTime,local_url:${audio}.currentSrc.startsWith('blob:')})`);
}

try {
  await send('Runtime.enable');
  await send('Page.enable');
  await send('Network.enable', { maxPostDataSize: 16384 });
  await send('Page.addScriptToEvaluateOnNewDocument', { source: `
    window.mediaEvidence = { streams: [], chunks: [], tracks: [] };
    if (navigator.mediaDevices) {
      const getUserMedia = navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
      navigator.mediaDevices.getUserMedia = async constraints => {
        const stream = await getUserMedia(constraints);
        const tracks = stream.getAudioTracks();
        window.mediaEvidence.streams.push({ constraints, active: stream.active, audio_tracks: tracks.length });
        window.mediaEvidence.tracks.push(...tracks);
        return stream;
      };
      const Recorder = window.MediaRecorder;
      window.MediaRecorder = class extends Recorder {
        constructor(...args) {
          super(...args);
          this.addEventListener('dataavailable', event => window.mediaEvidence.chunks.push({ bytes: event.data.size, type: event.data.type }));
        }
      };
    }
  ` });
  await viewport(1440, 1000, false);
  await send('Page.navigate', { url: ui + '/learn' });
  await button('进入初始能力评估');
  await button('开始新一轮评估');
  await button('Speaking：not_started');
  await button('开始 Speaking');
  await text('Your neighborhood is starting a weekly skill exchange.');
  await text('当前浏览器尚无录音');
  const readOtherProgress = () => ev(`Array.from(document.querySelectorAll('button')).map(e=>e.textContent).filter(s=>/^(Reading|Listening)：/.test(s))`);
  const unchanged = await readOtherProgress();
  assert.deepEqual(unchanged, ['Listening：not_started', 'Reading：not_started']);

  await button('开始录音');
  await text('正在录音');
  await sleep(1600);
  await button('停止录音');
  await text('本地录音已保存，尚未登记');
  const captured = await ev(`({streams:mediaEvidence.streams,chunks:mediaEvidence.chunks,tracks:mediaEvidence.tracks.map(t=>t.readyState)})`);
  assert.deepEqual(captured.streams, [{ constraints: { audio: true }, active: true, audio_tracks: 1 }]);
  assert.deepEqual(captured.tracks, ['ended']);
  assert.ok(captured.chunks.some(chunk => chunk.bytes > 0));
  const saved = await ev(localRows);
  assert.deepEqual(saved.key_path, ['metadata.learner_id', 'runId', 'metadata.task_version', 'metadata.prompt_id', 'metadata.prompt_version', 'metadata.recording_id']);
  assert.equal(saved.rows.length, 1);
  assert.ok(saved.rows[0].bytes > 0);
  assert.equal(saved.rows[0].registration, 'local');
  const playback = await play();
  assert.ok(playback.local_url);
  await screenshot('desktop-local-recording.png');
  checks.push('real getUserMedia and MediaRecorder, released track, nonempty IndexedDB Blob, native playback');

  await button('登记本次录音任务');
  await wait(`${visible}?.querySelector('h3')?.textContent==='任务已登记 · 口语能力未评分'`);
  await wait(`(${localRows}).then(value=>value.rows[0]?.registration==='registered')`);
  const completions = [...requests].filter(([, request]) => request.method === 'POST' && request.path.endsWith('/speaking/complete'));
  assert.equal(completions.length, 1);
  const [requestId, post] = completions[0];
  const response = JSON.parse((await send('Network.getResponseBody', { requestId })).body);
  const body = JSON.parse(post.body);
  assert.equal(post.status, 200);
  assert.deepEqual(body, saved.rows[0].metadata);
  assert.deepEqual(Object.keys(body).sort(), ['learner_id', 'recording_id', 'task_version', 'prompt_id', 'prompt_version', 'duration_seconds', 'recorded_at', 'idempotency_key'].sort());
  assert.equal(response.task_status, 'completed');
  assert.equal(response.evaluation_status, 'not_assessed');
  assert.equal(response.local_audio_availability, 'unknown');
  assert.equal(response.audio_available_on_server, false);
  assert.deepEqual(await readOtherProgress(), unchanged);
  checks.push('server registers exactly one metadata-only POST, status not_assessed, other skills unchanged');

  await send('Page.reload');
  await wait(`${visible}?.querySelector('h3')?.textContent==='任务已登记 · 口语能力未评分'`);
  const restored = await ev(localRows);
  assert.equal(restored.rows[0].metadata.recording_id, body.recording_id);
  assert.equal(restored.rows[0].registration, 'registered');
  const playbackAfterReload = await play();
  assert.ok(playbackAfterReload.local_url);
  checks.push('reload restores registered state and the same playable local Blob');

  const layouts = [];
  async function layout(width, height, mobile, theme, file) {
    await viewport(width, height, mobile);
    await ev(`${visible}.querySelector('h2').scrollIntoView({block:'start',behavior:'instant'})`);
    const metrics = await ev(`({width:innerWidth,height:innerHeight,scroll_width:document.documentElement.scrollWidth,
      panel_width:${visible}.getBoundingClientRect().width,audio_width:${audio}.getBoundingClientRect().width,
      page_theme:document.documentElement.dataset.theme})`);
    assert.equal(metrics.width, width);
    assert.equal(metrics.height, height);
    assert.ok(metrics.scroll_width <= width && metrics.audio_width > 0 && metrics.audio_width <= metrics.panel_width);
    layouts.push({ theme, ...metrics });
    if (file) await screenshot(file);
  }
  await layout(1440, 1000, false, 'Premium Academic');
  await layout(390, 844, true, 'Premium Academic');
  await pointer("document.querySelector('.focus-theme-toggle')");
  await layout(390, 844, true, 'Noir & Champagne', 'mobile-restored-noir.png');
  await layout(1440, 1000, false, 'Noir & Champagne');
  assert.notEqual(layouts[0].page_theme, layouts[2].page_theme);
  checks.push('both themes fit the desktop and 390px mobile viewport');

  // Only remove the test profile's assessment audio, simulating a browser without the Blob.
  await ev(`new Promise((resolve,reject)=>{
    const req=indexedDB.deleteDatabase('verbatrail-speaking-assessment-v1');
    req.onsuccess=()=>resolve(true);req.onerror=()=>reject(req.error);req.onblocked=()=>reject(Error('blocked'));
  })`);
  await send('Page.reload');
  await text('录音任务已登记，但当前浏览器没有可播放的本地录音');
  assert.equal(await ev(`!!${audio}`), false);
  assert.equal(await ev(`${visible}.querySelector('h3').textContent`), '任务已登记 · 口语能力未评分');
  await ev(`${visible}.querySelector('h2').scrollIntoView({block:'start',behavior:'instant'})`);
  await screenshot('desktop-missing-local-audio.png');
  checks.push('missing local Blob keeps the server completion and explains playback availability');

  for (const [skill, options, score] of [['Reading', [0, 0, 0, 0], '1 / 4'], ['Listening', [1, 3, 0, 2, 1, 0], '5 / 6']]) {
    await button(`${skill}：not_started`);
    await button(`开始 ${skill}`);
    await wait(`${visible}?.querySelectorAll('fieldset').length===${options.length}`);
    if (skill === 'Listening') await wait(`${visible}?.querySelector('audio')?.readyState>=2`);
    for (const [i, option] of options.entries()) {
      await pointer(`${visible}.querySelectorAll('fieldset')[${i}].querySelectorAll('input')[${option}]`);
    }
    await button('提交作答');
    await wait(`${visible}.innerText.includes('本次答对 ${score}')`);
    checks.push(`${skill} finishes with server result ${score} in the same run`);
  }
  await button('返回 Learn');
  await pointer("Array.from(document.querySelectorAll('a')).find(e=>e.textContent.includes('进入口语'))");
  await text('Speaking · 口语');
  await wait("document.querySelector('#speaking-prompt')?.options.length>0");
  checks.push('ordinary Speaking workspace opens and loads its independent prompt catalogue');

  await writeFile(join(out, 'browser.json'), JSON.stringify({ status: 'pass', browser: await send('Browser.getVersion'),
    microphone: 'Chrome virtual microphone', checks, captured, saved, playback, restored, playbackAfterReload,
    registration: { path: post.path, status: post.status, body, response }, layouts, requests: [...requests.values()], screenshots }, null, 2) + '\n');
} catch (error) {
  await screenshot('failure.png').catch(() => {});
  await writeFile(join(out, 'failure.json'), JSON.stringify({ status: 'fail', error: String(error), checks, requests: [...requests.values()] }, null, 2));
  throw error;
} finally {
  socket.close();
}
