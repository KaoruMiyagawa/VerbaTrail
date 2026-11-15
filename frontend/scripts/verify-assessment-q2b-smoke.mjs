import assert from 'node:assert/strict';
import { mkdir, writeFile } from 'node:fs/promises';
import { join } from 'node:path';

const { SMOKE_UI_URL: ui, SMOKE_API_URL: api, SMOKE_CDP_URL: cdp, SMOKE_OUTPUT_DIR: out } = process.env;
assert.ok(ui && api && cdp && out, 'Use verify_assessment_q2b_smoke.py to start isolated services.');
await mkdir(out, { recursive: true });
const target = (await (await fetch(`${cdp}/json/list`)).json()).find(page => page.type === 'page');
const socket = new WebSocket(target.webSocketDebuggerUrl);
await new Promise((resolve, reject) => { socket.onopen = resolve; socket.onerror = reject; });
let sequence = 0;
const pending = new Map(), requests = new Map(), errors = [], screenshots = [], steps = [];
const report = {
  status: 'running', browser: 'Headless Chrome, real Uvicorn + Vite, isolated SQLite',
  skills: { reading: { status: 'not_run' }, listening: { status: 'not_run' }, speaking: { status: 'not_run' }, writing: { status: 'not_run' } },
  navigation: { status: 'not_run', routes: [], themes: [] }, requests: [], screenshots, steps, failures: [],
};
let currentSkill = null;
const send = (method, params = {}) => new Promise((resolve, reject) => {
  const id = ++sequence, timer = setTimeout(() => { pending.delete(id); reject(Error(`CDP timeout: ${method}`)); }, 20000);
  pending.set(id, message => { clearTimeout(timer); message.error ? reject(Error(JSON.stringify(message.error))) : resolve(message.result); });
  socket.send(JSON.stringify({ id, method, params }));
});
socket.onmessage = ({ data }) => {
  const message = JSON.parse(data);
  if (message.id) { pending.get(message.id)?.(message); pending.delete(message.id); return; }
  if (message.method === 'Runtime.exceptionThrown') errors.push(message.params.exceptionDetails.text);
  if (message.method === 'Runtime.consoleAPICalled' && message.params.type === 'error') errors.push(message.params.args.map(x => x.description ?? x.value).join(' '));
  if (message.method === 'Network.requestWillBeSent' && message.params.request.url.startsWith(`${api}/api/v1/`)) {
    const request = message.params.request;
    requests.set(message.params.requestId, { method: request.method, url: request.url, body: request.postData || null, status: null });
  }
  if (message.method === 'Network.responseReceived') {
    const request = requests.get(message.params.requestId);
    if (request) request.status = message.params.response.status;
  }
  if (message.method === 'Network.loadingFailed') {
    const request = requests.get(message.params.requestId);
    if (request) request.failure = message.params.errorText;
  }
};
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
const ev = async expression => {
  const result = await send('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true });
  if (result.exceptionDetails) throw Error(JSON.stringify(result.exceptionDetails));
  return result.result.value;
};
const wait = async (expression, label = expression, timeout = 25000) => {
  const end = Date.now() + timeout;
  while (Date.now() < end) { const value = await ev(expression); if (value) return value; await pause(100); }
  throw Error(`Timed out: ${label}; page=${(await ev('document.body.innerText')).slice(-1200)}`);
};
const screenshot = async name => {
  const { data } = await send('Page.captureScreenshot', { format: 'png', captureBeyondViewport: false });
  await writeFile(join(out, name), Buffer.from(data, 'base64')); screenshots.push(name);
};
const record = async (name, details = {}) => { steps.push({ name, ...details, status: 'pass' }); await persist(); };
const persist = async () => writeFile(join(out, 'browser-report.json'), JSON.stringify({ ...report, requests: [...requests.values()], runtime_errors: errors }, null, 2), 'utf8');
const viewportCheck = async () => {
  const value = await ev('({width:innerWidth,height:innerHeight,scrollWidth:document.documentElement.scrollWidth,theme:document.documentElement.dataset.theme})');
  assert.ok(value.scrollWidth <= value.width, `horizontal overflow ${JSON.stringify(value)}`);
  return value;
};
const waitRoute = async path => { await wait(`location.pathname===${JSON.stringify(path)}`, `route ${path}`); report.navigation.routes.push(path); await viewportCheck(); };
const clickAt = async (rect, name) => {
  const x = Math.round(rect.x + rect.width / 2), y = Math.round(rect.y + rect.height / 2);
  assert.ok(rect.width > 0 && rect.height > 0 && x >= 0 && x < 1440 && y >= 0 && y < 1000, `${name} outside desktop viewport: ${JSON.stringify(rect)}`);
  await send('Input.dispatchMouseEvent', { type: 'mouseMoved', x, y });
  await send('Input.dispatchMouseEvent', { type: 'mousePressed', x, y, button: 'left', clickCount: 1 });
  await send('Input.dispatchMouseEvent', { type: 'mouseReleased', x, y, button: 'left', clickCount: 1 });
};
const clickSelector = async (selector, name = selector, root = 'document') => {
  const rect = await ev(`(()=>{const root=${root};const e=root.querySelector(${JSON.stringify(selector)});if(!e)return null;e.scrollIntoView({block:'center',behavior:'instant'});const r=e.getBoundingClientRect();return {x:r.x,y:r.y,width:r.width,height:r.height,disabled:!!e.disabled}})()`);
  assert.ok(rect && !rect.disabled, `missing/disabled control ${name}; path=${await ev('location.pathname')}; theme=${await ev('document.documentElement.dataset.theme')}; selector_present=${await ev(`Boolean(document.querySelector(${JSON.stringify(selector)}))`)}`);
  await clickAt(rect, name); await pause(150);
};
const clickText = async (text, name = text, tag = 'button') => {
  const rect = await ev(`(()=>{const e=[...document.querySelectorAll(${JSON.stringify(tag)})].find(x=>x.textContent.includes(${JSON.stringify(text)})&&!x.disabled);if(!e)return null;e.scrollIntoView({block:'center',behavior:'instant'});const r=e.getBoundingClientRect();return {x:r.x,y:r.y,width:r.width,height:r.height}})()`);
  assert.ok(rect, `missing enabled ${tag}: ${text}`);
  await clickAt(rect, name); await pause(150);
};
const setTheme = async theme => {
  if (await ev('document.documentElement.dataset.theme') !== theme) await clickSelector('.focus-theme-toggle', `theme-${theme}`);
  await wait(`document.documentElement.dataset.theme===${JSON.stringify(theme)}`, `theme ${theme}`);
  const geometry = await viewportCheck(); report.navigation.themes.push({ theme, ...geometry });
  return geometry;
};
const goHome = async () => { await clickText('Learn', 'Learn navigation'); await waitRoute('/'); await wait("Boolean(document.querySelector('.learn-page'))"); };
const enterSkill = async (href, marker) => {
  await goHome(); await clickSelector(`a[href=${JSON.stringify(href)}]`, `Learn entry ${href}`);
  await waitRoute(href); await wait("Boolean(document.querySelector('h1'))", `skill page ${marker}`);
};
const enterNoirAndPremium = async marker => {
  for (const theme of ['premium', 'noir']) {
    await setTheme(theme);
    assert.ok(await ev(`document.body.innerText.includes(${JSON.stringify(marker)})`), `${marker} disappeared under ${theme}`);
    await screenshot(`${currentSkill}-${theme}.png`);
  }
};
const mouseInputText = async (selector, text, replace = false) => {
  const rect = await ev(`(()=>{const e=document.querySelector(${JSON.stringify(selector)});if(!e)return null;e.scrollIntoView({block:'center',behavior:'instant'});const r=e.getBoundingClientRect();return {x:r.x,y:r.y,width:r.width,height:r.height}})()`);
  assert.ok(rect, `missing text field ${selector}`); await clickAt(rect, `focus ${selector}`);
  if (replace) {
    await send('Input.dispatchKeyEvent', { type: 'keyDown', key: 'a', code: 'KeyA', windowsVirtualKeyCode: 65, modifiers: 2 });
    await send('Input.dispatchKeyEvent', { type: 'keyUp', key: 'a', code: 'KeyA', windowsVirtualKeyCode: 65, modifiers: 2 });
  }
  await send('Input.insertText', { text });
};

try {
  await send('Runtime.enable'); await send('Page.enable'); await send('Network.enable'); await send('Log.enable');
  await send('Emulation.setDeviceMetricsOverride', { width: 1440, height: 1000, deviceScaleFactor: 1, mobile: false });
  await send('Page.navigate', { url: ui });
  await wait("document.body.innerText.includes('初始能力评估')", 'Learn home loaded');
  await wait("document.querySelectorAll('.learn-skill-card[href]').length>=4", 'four Learn skill entries');
  assert.equal(await ev(`document.querySelector('.learn-skill-card[href="/learning"]')!==null`), true);
  await screenshot('learn-home-entry.png');
  await record('learn-home-four-skills-and-assessment-entry', { routes: [...documentRoutes()] });

  // Reading: choose the ordinary IELTS Academic topic, create a normal session and submit its live question.
  currentSkill = 'reading'; report.skills.reading.status = 'running';
  await enterSkill('/learning', '选择阅读任务');
  await wait("[...document.querySelectorAll('.wb-topic-option')].some(x=>x.innerText.includes('Academic Reading'))", 'ordinary Reading catalog');
  await clickText('Academic Reading', 'select ordinary Reading topic', 'button.wb-topic-option');
  await wait("[...document.querySelectorAll('button')].some(x=>x.textContent.includes('开始阅读')&&!x.disabled)");
  await clickText('开始阅读');
  await wait("document.querySelector('#real-answer') && document.querySelector('.wb-material h3')?.textContent.includes('Urban Wetlands')", 'ordinary Reading material and question');
  assert.equal(await ev("document.querySelector('.focus-question-prompt')?.textContent.includes('What conditions did the wetland study identify?')"), true);
  const readingId = await ev("sessionStorage.getItem('unlearnloop.real.active-session.v1')"); assert.ok(readingId);
  await screenshot('reading-normal-session.png');
  await mouseInputText('#real-answer', 'B');
  await clickText('提交作答');
  await wait("document.body.innerText.includes('最近一次作答评价')", 'real ordinary Reading result');
  assert.ok(await ev("document.body.innerText.includes('作答结果已保存') || document.body.innerText.includes('正确') || document.body.innerText.includes('未通过')"));
  await screenshot('reading-normal-result.png');
  await enterNoirAndPremium('最近一次作答评价');
  const readingOrdinary = { session_id: readingId, version: 'ielts-reading content pack (seeded); assessment_run_created: false', result_visible: true, evaluation: await ev("[...document.querySelectorAll('.wb-card')].find(card=>card.innerText.includes('最近一次作答评价'))?.innerText.slice(0,360)||''") };
  report.skills.reading.status = 'pass'; report.skills.reading.details = readingOrdinary; await record('ordinary-reading-session-submitted', readingOrdinary);

  // Listening: browser playback plus server-scored submission, and a wrong-learner ownership probe.
  currentSkill = 'listening'; report.skills.listening.status = 'running';
  await goHome(); await clickSelector("a[href='/learn/listening']", 'Learn entry /learn/listening'); await waitRoute('/learn/listening');
  await wait("document.body.innerText.includes('生活场景') && [...document.querySelectorAll('button')].some(x=>x.textContent.includes('开始练习'))", 'Listening intro');
  await screenshot('listening-intro.png'); await clickText('开始练习');
  await wait("document.querySelector('audio') && document.querySelectorAll('fieldset.listening-question').length===5", 'Listening lesson loaded');
  const media = await wait("(()=>{const a=document.querySelector('audio');return a.readyState>=2&&a.duration>30?{duration:a.duration,readyState:a.readyState,src:a.currentSrc}:false})()", 'playable Listening audio metadata');
  const playback = await ev("(async()=>{const a=document.querySelector('audio');await a.play();await new Promise(r=>setTimeout(r,700));const result={playing:!a.paused,currentTime:a.currentTime,duration:a.duration};a.pause();return result})()");
  assert.ok(playback.playing && playback.currentTime>0, JSON.stringify(playback));
  const listeningSessionId = await ev("localStorage.getItem('verbatrail.listening.active.v1')"); assert.ok(listeningSessionId);
  const ownerProbe = await ev(`(async()=>{const id=${JSON.stringify(listeningSessionId)};const answers=Object.fromEntries([...document.querySelectorAll('fieldset.listening-question')].map(f=>[f.querySelector('input').name,f.querySelector('input').value]));const get=await fetch(${JSON.stringify(`${api}/api/v1/listening/sessions/`)}+id+'?learner_id=q2b_wrong_learner');const submit=await fetch(${JSON.stringify(`${api}/api/v1/listening/sessions/`)}+id+'/submit?learner_id=q2b_wrong_learner',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({answers,idempotency_key:'q2b-owner-check'})});return {get:get.status,submit:submit.status}})()`);
  assert.deepEqual(ownerProbe, { get: 404, submit: 404 });
  for (let i = 0; i < 5; i += 1) {
    await ev(`document.querySelectorAll('fieldset.listening-question')[${i}].scrollIntoView({block:'center',behavior:'instant'})`);
    const first = await ev(`(()=>{const input=document.querySelectorAll('fieldset.listening-question')[${i}].querySelector('input');const r=input.getBoundingClientRect();return {x:r.x+r.width/2,y:r.y+r.height/2}})()`);
    await clickAt({ ...first, width: 1, height: 1 }, `answer Listening question ${i+1}`);
  }
  const answersCount = await ev("document.querySelectorAll('fieldset.listening-question input:checked').length"); assert.equal(answersCount, 5);
  await clickText('提交并查看结果'); await wait("document.body.innerText.includes('本次结果：') && document.body.innerText.includes('评价来源：服务端规则校验')", 'Listening server score');
  const listeningScore = await ev("document.querySelector('.listening-score h2')?.textContent");
  await screenshot('listening-server-result.png');
  await send('Page.reload'); await wait("document.body.innerText.includes('本次结果：')", 'Listening result restore after reload');
  await enterNoirAndPremium('本次结果：');
  report.skills.listening.status = 'pass'; report.skills.listening.details = { session_id: listeningSessionId, playable_audio: media, playback, submitted_answers: answersCount, server_score: listeningScore, other_learner_get_status: ownerProbe.get, other_learner_submit_status: ownerProbe.submit, restored_after_reload: true };
  await record('listening-real-audio-scoring-and-ownership', report.skills.listening.details);

  // Speaking: Chrome's fake media input drives the real MediaRecorder; audio remains in IndexedDB.
  currentSkill = 'speaking'; report.skills.speaking.status = 'running';
  await goHome(); await clickSelector("a[href='/learn/speaking']", 'Learn entry /learn/speaking'); await waitRoute('/learn/speaking');
  await wait("Boolean(document.querySelector('#speaking-prompt') && document.querySelector('.speaking-card h2[lang=en]'))", 'Speaking Part 1 prompt');
  const prompt = await ev("document.querySelector('.speaking-card h2[lang=en]').textContent");
  await screenshot('speaking-prompt.png');
  await wait("[...document.querySelectorAll('button')].some(x=>x.textContent.includes('开始录音')&&!x.disabled)");
  await clickText('开始录音'); await wait("document.body.innerText.includes('正在录音')", 'recording started');
  await pause(1600); await clickText('停止录音'); await wait("document.body.innerText.includes('录音已保存到此浏览器')", 'local recording stored', 25000);
  await wait("Boolean(document.querySelector('.speaking-recording audio'))", 'local recording player');
  const localRecording = await ev("(()=>{const a=document.querySelector('.speaking-recording audio');return {src:a.src.slice(0,32),readyState:a.readyState,recordings:document.querySelectorAll('.speaking-recording').length}})()");
  const localPlayback = await ev("(async()=>{const a=document.querySelector('.speaking-recording audio');await a.play();await new Promise(r=>setTimeout(r,250));const out={playing:!a.paused,currentTime:a.currentTime};a.pause();return out})()");
  assert.ok(localRecording.src.startsWith('blob:') && localPlayback.currentTime>0, JSON.stringify({localRecording,localPlayback}));
  const checkRect = await ev("(()=>{const e=document.querySelector('.speaking-check input'),r=e.getBoundingClientRect();return {x:r.x+r.width/2,y:r.y+r.height/2}})()");
  await clickAt({ ...checkRect, width: 1, height: 1 }, 'speaking self-check');
  await clickText('完成练习'); await wait("document.body.innerText.includes('练习元数据已保存到学习历史')", 'Speaking metadata saved');
  await send('Page.reload'); await wait("Boolean(document.querySelector('.speaking-recording audio')) && document.body.innerText.includes('练习已记录')", 'Speaking local recording and server metadata restored');
  assert.ok(await ev("document.body.innerText.includes('不评定口语水平') && !document.body.innerText.includes('IELTS Band 9')"));
  await enterNoirAndPremium('不评定口语水平');
  report.skills.speaking.status = 'pass'; report.skills.speaking.details = { prompt, microphone: 'Chrome fake-device-for-media-stream; virtual device only, no physical microphone tested', localRecording, localPlayback, local_blob_restored_after_reload: true, server_metadata_saved: true, evaluation_status: 'not_assessed', band_claim: false };
  await record('speaking-virtual-microphone-local-recording-and-self-check', report.skills.speaking.details);

  // Writing: server-saved draft, two immutable revisions and refresh restoration.
  currentSkill = 'writing'; report.skills.writing.status = 'running';
  await goHome(); await clickSelector("a[href='/learn/writing']", 'Learn entry /learn/writing'); await waitRoute('/learn/writing');
  await wait("document.querySelector('#writing-essay') && document.body.innerText.includes('Shared spaces for neighbourhoods')", 'Writing Task 2 loaded');
  const writingSessionId = await ev(`(async()=>{const response=await fetch(${JSON.stringify(`${api}/api/v1/writing/sessions`)},{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({learner_id:'default_learner'})});if(!response.ok)throw Error('Writing session reopen '+response.status);return (await response.json()).session_id})()`);
  await screenshot('writing-task2-prompt.png');
  const essay1 = 'Local councils should consider the needs of the whole neighbourhood before deciding how to use public land. A community garden can bring neighbours together and give residents a practical way to grow food. It may also provide a calm place for older people and families. However, an outdoor sports facility can support exercise and give young people a safe place to meet. In my view, the best choice depends on what the area already has. If there is no nearby place for exercise, a small court may be more useful. Where sports facilities are already available, a garden could serve a different need. Councils should consult residents, review available space, and choose a plan that can be maintained over time.';
  await mouseInputText('#writing-essay', essay1);
  await wait("document.body.innerText.includes('服务器草稿已保存')", 'Writing autosaved draft');
  const draftSaved = await ev(`document.querySelector('#writing-essay').value===${JSON.stringify(essay1)}`); assert.ok(draftSaved);
  await send('Page.reload'); await wait(`document.querySelector('#writing-essay')?.value===${JSON.stringify(essay1)}`, 'Writing draft restored');
  await clickText('提交作文'); await wait("document.body.innerText.includes('第 1 版') && document.body.innerText.includes('自动文本检测')", 'Writing revision one');
  const essay2 = `${essay1} The local council could also arrange a trial and review attendance before making a permanent decision.`;
  await mouseInputText('#writing-essay', essay2, true);
  await wait("document.body.innerText.includes('服务器草稿已保存') && document.querySelector('#writing-essay').value.includes('arrange a trial')", 'Writing second draft saved');
  await clickText('提交修订版'); await wait("document.body.innerText.includes('第 2 版') && document.body.innerText.includes('尚未评估')", 'Writing revision two and evaluation boundary');
  assert.ok(await ev("document.body.innerText.includes('论证、内容质量、词汇、语法准确性和 IELTS Band')"));
  await screenshot('writing-revision-two.png');
  await send('Page.reload'); await wait("Boolean(document.body && document.body.innerText.includes('第 2 版') && document.querySelector('#writing-essay')?.value.includes('arrange a trial'))", 'Writing latest revision restored');
  await enterNoirAndPremium('尚未评估');
  report.skills.writing.status = 'pass'; report.skills.writing.details = { draft_restored: true, revision_count_visible: 2, latest_draft_restored: true, session: writingSessionId, semantic_quality: 'not_assessed', word_count_is_not_quality_score: true };
  await record('writing-draft-revision-and-refresh-restore', report.skills.writing.details);

  // Verify the assessment route remains distinct and does not create a run just by reading it.
  currentSkill = 'navigation';
  await goHome(); await clickText('进入初始能力评估'); await waitRoute('/learn/assessment');
  await wait("document.body.innerText.includes('初始能力评估') && [...document.querySelectorAll('button')].some(button=>button.textContent.includes('开始新一轮评估'))", 'assessment route stays available');
  assert.ok(await ev("document.body.innerText.includes('暂无评估记录')"));
  await screenshot('assessment-entry-remains-distinct.png');
  const assessmentRuns = await ev(`(async()=>await (await fetch(${JSON.stringify(`${api}/api/v1/assessment-runs?learner_id=default_learner`)})).json())()`);
  assert.deepEqual(assessmentRuns, []);
  report.navigation.status = 'pass'; report.navigation.assessment_runs_after_visit = assessmentRuns.length;
  await record('assessment-route-distinct-and-read-only-on-visit', { assessment_run_count: assessmentRuns.length });
  assert.deepEqual(errors, [], `browser errors: ${errors.join('; ')}`);
  report.status = 'pass';
  await persist();
} catch (error) {
  report.status = 'fail';
  if (currentSkill && report.skills[currentSkill]?.status === 'running') report.skills[currentSkill].status = 'fail';
  report.failures.push({ skill: currentSkill, message: error.message, stack: error.stack });
  try { await screenshot(`failure-${currentSkill}.png`); } catch { /* Keep the original failure if Chrome already exited. */ }
  await persist();
  throw error;
} finally { socket.close(); }

function documentRoutes() { return ['/learning', '/learn/listening', '/learn/speaking', '/learn/writing']; }
