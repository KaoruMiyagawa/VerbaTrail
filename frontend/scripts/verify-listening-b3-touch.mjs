import assert from 'node:assert/strict';
import { mkdir, writeFile } from 'node:fs/promises';
import { join } from 'node:path';

const { SMOKE_UI_URL: ui, SMOKE_CDP_URL: cdp, SMOKE_OUTPUT_DIR: out } = process.env;
const output = { status: 'running', viewport: { width: 390, height: 844 }, input: 'Chrome CDP Input.dispatchTouchEvent touchStart/touchMove/touchEnd; no programmatic scrolling', themeRuns: [], gestures: [], screenshots: [], browserRequests: [], checks: [], failures: [] };
await mkdir(out, { recursive: true });
const target = (await (await fetch(`${cdp}/json/list`)).json()).find(page => page.type === 'page');
const ws = new WebSocket(target.webSocketDebuggerUrl);
await new Promise((resolve, reject) => { ws.onopen = resolve; ws.onerror = reject; });
let seq = 0;
let failAudio=false;const audioFaults=[];
const pending = new Map();
ws.onmessage = ({ data }) => {
  const message = JSON.parse(data);
  if (message.id) { pending.get(message.id)?.(message); pending.delete(message.id); }
  else if (message.method === 'Fetch.requestPaused') { const p=message.params;if(failAudio&&p.request.url.includes('/audio/listening/initial-community-arts-v1.wav')) {audioFaults.push({url:p.request.url,action:'ConnectionReset',at:Date.now()});void send('Fetch.failRequest',{requestId:p.requestId,errorReason:'ConnectionReset'});}else void send('Fetch.continueRequest',{requestId:p.requestId}); }
  else if (message.method === 'Network.requestWillBeSent') {
    const request = message.params.request;
    if (request.url.includes('/api/v1/assessment-runs') || request.url.includes('/audio/listening/')) output.browserRequests.push({ method: request.method, url: request.url, body: request.postData || null });
  }
};
const send = (method, params = {}) => new Promise((resolve, reject) => {
  const id = ++seq;
  const timer = setTimeout(() => { pending.delete(id); reject(Error(`CDP timeout: ${method}`)); }, 20000);
  pending.set(id, message => { clearTimeout(timer); message.error ? reject(Error(JSON.stringify(message.error))) : resolve(message.result); });
  ws.send(JSON.stringify({ id, method, params }));
});
const evaluate = async expression => {
  const result = await send('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true });
  if (result.exceptionDetails) throw Error(JSON.stringify(result.exceptionDetails));
  return result.result.value;
};
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
const flush = async () => writeFile(join(out, 'touch-report.json'), JSON.stringify(output, null, 2), 'utf8');
const waitFor = async expression => {
  for (let i = 0; i < 300; i += 1) { if (await evaluate(expression)) return; await pause(100); }
  throw Error(`Timed out: ${expression}`);
};
const state = () => evaluate(`({scrollY,scrollHeight:document.documentElement.scrollHeight,scrollWidth:document.documentElement.scrollWidth,clientWidth:document.documentElement.clientWidth,theme:document.documentElement.dataset.theme,viewportHeight:innerHeight})`);
const screenshot = async name => {
  const file = name.endsWith('.png') ? name : `${name}.png`;
  const { data } = await send('Page.captureScreenshot', { format: 'png', captureBeyondViewport: false });
  await writeFile(join(out, file), Buffer.from(data, 'base64'));
  output.screenshots.push(file);
};
const waitForScrollStop = async () => {
  let stable = 0, previous = await evaluate('scrollY');
  for (let i = 0; i < 20 && stable < 3; i += 1) {
    await pause(100);
    const current = await evaluate('scrollY');
    if (Math.abs(current - previous) < 1) stable += 1; else stable = 0;
    previous = current;
  }
};
const gesture = async (direction, distance = 190) => {
  const up = direction === 'up';
  const startY = up ? 745 : 335;
  const endY = startY + (up ? -distance : distance);
  const before = await state();
  const steps = Math.max(5, Math.ceil(distance / 24));
  const points = [];
  for (let step = 0; step <= steps; step += 1) {
    const y = Math.round(startY + (endY - startY) * step / steps);
    points.push({ x: 195, y });
    const type = step === 0 ? 'touchStart' : step === steps ? 'touchEnd' : 'touchMove';
    await send('Input.dispatchTouchEvent', { type, touchPoints: type === 'touchEnd' ? [] : [{ id: 1, x: 195, y, radiusX: 9, radiusY: 9, force: 1 }] });
    await pause(30);
  }
  await waitForScrollStop();
  const after = await state();
  const entry = { direction, events: ['touchStart', ...Array(Math.max(0, steps - 1)).fill('touchMove'), 'touchEnd'], points, before: before.scrollY, after: after.scrollY, delta: after.scrollY - before.scrollY, theme: after.theme, scrollWidth: after.scrollWidth, clientWidth: after.clientWidth };
  output.gestures.push(entry);
  assert.ok(after.scrollWidth <= after.clientWidth, `horizontal overflow: ${JSON.stringify(after)}`);
  await flush();
  return entry;
};
const tapAt = async (x, y, name) => {
  assert.ok(x >= 0 && x < 390 && y >= 0 && y < 844, `Touch target outside viewport for ${name}: ${x},${y}`);
  const hit = await evaluate(`(()=>{const e=document.elementFromPoint(${x},${y});return {tag:e?.tagName,text:(e?.innerText||e?.textContent||'').trim().slice(0,80),selector:e?.closest('button,label,input')?.outerHTML.slice(0,180)||null}})()`);
  await send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [{ id: 1, x, y, radiusX: 10, radiusY: 10, force: 1 }] });
  await pause(90);
  await send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] });
  output.checks.push({ id: name, type: 'touchTap', x, y, hit, status: 'input-sent' });
  await pause(250);
  await flush();
};
const touchElement = async (selector, name) => {
  const box = await evaluate(`(()=>{const e=document.querySelector(${JSON.stringify(selector)});if(!e)return null;const r=e.getBoundingClientRect();return {x:r.x,y:r.y,width:r.width,height:r.height,top:r.top,bottom:r.bottom,centerX:r.x+r.width/2,centerY:r.y+r.height/2,visible:r.width>0&&r.height>0&&r.bottom>0&&r.top<innerHeight}})()`);
  if (!box?.visible) throw Error(`${name} is not currently touch reachable: ${JSON.stringify(box)}`);
  await tapAt(Math.round(box.centerX), Math.round(box.centerY), name);
  return box;
};
const markButton = async (text, id) => evaluate(`(()=>{const e=[...document.querySelectorAll('button')].find(button=>button.textContent.includes(${JSON.stringify(text)})&&!button.disabled);if(!e)return false;e.id=${JSON.stringify(id)};return true})()`);
const selectorBox = selector => evaluate(`(()=>{const e=document.querySelector(${JSON.stringify(selector)});if(!e)return null;const r=e.getBoundingClientRect();return {top:r.top,bottom:r.bottom,left:r.left,right:r.right,width:r.width,height:r.height,visible:r.width>0&&r.height>0&&r.bottom>0&&r.top<innerHeight}})()`);
const scrollUntilReachable = async (selector, id, { fully = true, allowNav = false, max = 28 } = {}) => {
  let box = await selectorBox(selector);
  for (let i = 0; i < max; i += 1) {
    const navBottom = await evaluate("document.querySelector('.focus-top-nav')?.getBoundingClientRect().bottom||0");
    const reached = box?.visible && (allowNav || box.top >= navBottom + 6) && (!fully || box.bottom <= 844 - 8);
    if (reached) {
      const current = await state();
      output.checks.push({ id, type: 'touch-reach', rect: box, scrollY: current.scrollY, theme: current.theme, status: 'pass' });
      // Reachability is recorded with coordinates; save representative screenshots separately.
      await flush();
      return box;
    }
    if (!box) throw Error(`Target missing while touch scrolling: ${id}`);
    let direction;
    if (box.top < navBottom + 6) direction = 'down';
    else direction = 'up';
    const distance = Math.max(105, Math.min(205, Math.round(direction === 'up' ? box.bottom - (844 - 12) : navBottom + 12 - box.top)));
    await gesture(direction, distance);
    box = await selectorBox(selector);
  }
  throw Error(`Touch gestures could not bring ${id} into usable viewport: ${JSON.stringify(box)}`);
};
const record = (id, data = {}) => { output.checks.push({ id, ...data, status: 'pass' }); return flush(); };
const bodyHas = text => evaluate(`document.body.innerText.includes(${JSON.stringify(text)})`);

try {
  await send('Runtime.enable'); await send('Page.enable'); await send('Network.enable');
  await send('Emulation.setDeviceMetricsOverride', { width: 390, height: 844, deviceScaleFactor: 1, mobile: true, screenWidth: 390, screenHeight: 844 });
  await send('Emulation.setTouchEmulationEnabled', { enabled: true, maxTouchPoints: 1 });
  await send('Page.navigate', { url: `${ui}/learn/assessment` });
  await waitFor("document.body.innerText.includes('开始新一轮评估')");
  let current = await state();
  assert.equal(current.clientWidth, 390);
  assert.ok(current.scrollWidth <= current.clientWidth, `horizontal overflow at entry: ${JSON.stringify(current)}`);
  output.themeRuns.push({ theme: current.theme, scenario: 'full-listening-assessment', status: 'running' });
  await screenshot('assessment-entry-mobile.png');

  await waitFor("[...document.querySelectorAll('button')].some(button=>button.textContent.includes('开始新一轮评估')&&!button.disabled)");
  assert.equal(await markButton('开始新一轮评估', 'touch-create-run'), true);
  await touchElement('#touch-create-run', 'create-assessment');
  await waitFor("document.body.innerText.includes('Listening：not_started')");
  assert.equal(await markButton('Listening：', 'touch-select-listening'),true);await scrollUntilReachable('#touch-select-listening','listening-selector');await touchElement('#touch-select-listening','select-listening');
  await waitFor("document.body.innerText.includes('开始 Listening')");
  output.runId = await evaluate("localStorage.getItem('assessment:selected:default_learner')");
  assert.ok(output.runId);
  await record('assessment-created', { run_id: output.runId, theme: current.theme });
  assert.equal(await markButton('开始 Listening', 'touch-start-listening'), true);
  await scrollUntilReachable('#touch-start-listening','start-listening-reachable');await touchElement('#touch-start-listening', 'start-listening');
  await waitFor('document.querySelectorAll("[data-assessment-skill=listening] fieldset input[type=radio]").length>=24');
  output.sessionId = await evaluate("document.body.innerText.match(/session_[A-Za-z0-9_-]+/)?.[0]||null");
  await record('listening-started', { run_id: output.runId, session_id: output.sessionId, version: await evaluate("document.body.innerText.match(/listening-initial-community-arts-v1/)?.[0]||null") });

  await scrollUntilReachable('audio[aria-label="Listening 初始评估音频"]','audio-reachable');
  const abox=await selectorBox('audio[aria-label="Listening 初始评估音频"]');
  await waitFor("document.querySelector('audio').readyState>=2");
  await tapAt(Math.round(abox.left+23),Math.round((abox.top+abox.bottom)/2),'play-audio');
  await waitFor("document.querySelector('audio').currentTime>0.2&&!document.querySelector('audio').paused");
  await screenshot('audio-playing.png');await record('audio-played-by-touch',{time:await evaluate("document.querySelector('audio').currentTime")});
  for (let q = 0; q < 6; q += 1) {
    const fieldsetSelector = `[data-assessment-skill=listening] fieldset:nth-of-type(${q + 1})`;
    const fieldsetExists = await evaluate(`document.querySelectorAll('[data-assessment-skill=listening] fieldset').length>${q}`);
    assert.ok(fieldsetExists, `Question ${q + 1} fieldset not rendered`);
    await scrollUntilReachable(`[data-assessment-skill=listening] fieldset:nth-of-type(${q + 1})`, `question-${q + 1}-reachable`, { fully: true });
    const optionSelector = `[data-assessment-skill=listening] fieldset:nth-of-type(${q + 1}) input[type="radio"]`;
    const input = await evaluate(`(()=>{const e=document.querySelector(${JSON.stringify(optionSelector)});const r=e.getBoundingClientRect();return {x:r.x+r.width/2,y:r.y+r.height/2,checked:e.checked,disabled:e.disabled}})()`);
    assert.ok(input.y > 0 && input.y < 844, `Question ${q + 1} radio is outside viewport`);
    await tapAt(Math.round(input.x), Math.round(input.y), `select-question-${q + 1}`);
    await waitFor(`document.querySelectorAll('[data-assessment-skill=listening] fieldset')[${q}].querySelector('input:checked')!==null`);

    await record(`question-${q + 1}-selected`, { input, selected: await evaluate(`document.querySelectorAll('[data-assessment-skill=listening] fieldset')[${q}].querySelector('input:checked')?.value||null`) });
  }

  // Tab from the final selected radio to the submit button in Premium theme.
  let submitFocus=null;
  for(let i=0;i<35;i++){
    await send('Input.dispatchKeyEvent',{type:'keyDown',key:'Tab',code:'Tab',windowsVirtualKeyCode:9});
    await send('Input.dispatchKeyEvent',{type:'keyUp',key:'Tab',code:'Tab',windowsVirtualKeyCode:9});
    const target=await evaluate("document.activeElement?.textContent?.trim()||''");
    if(target==='提交作答') {await waitForScrollStop();await pause(300);submitFocus=await evaluate(`(()=>{let e=document.activeElement,s=getComputedStyle(e),r=e.getBoundingClientRect();return {text:e.textContent,outline:s.outline,top:r.top,bottom:r.bottom,navBottom:document.querySelector('.focus-top-nav')?.getBoundingClientRect().bottom||0}})()`);break;}
  }
  assert.ok(submitFocus && submitFocus.outline.includes('solid') && submitFocus.top>=submitFocus.navBottom && submitFocus.bottom<=844,JSON.stringify(submitFocus));
  output.premiumSubmitFocus=submitFocus;await screenshot('premium-submit-focus.png');
  assert.equal(await markButton('提交作答', 'touch-submit-listening'), true);
  await scrollUntilReachable('#touch-submit-listening', 'submit-reachable', { fully: true });
  const beforeSubmit = await state();
  await touchElement('#touch-submit-listening', 'submit-listening');
  await waitFor("document.body.innerText.includes('本次答对')");
  const score = await evaluate("document.body.innerText.match(/本次答对\\s*\\d+\\s*\\/\\s*6\\s*题/)?.[0]||null");
  assert.ok(score, 'server-scored 6-question result not shown');
  const doneState = await state();
  output.score = score;
  output.runId = await evaluate("localStorage.getItem('assessment:selected:default_learner')");
  await screenshot('server-result-first-view.png');
  await record('server-submission-result', { run_id: output.runId, score, submit_scroll_y: beforeSubmit.scrollY, result_scroll_y: doneState.scrollY });

  const explanationIds = [];
  for (let q = 0; q < 6; q += 1) {
    const explanationSelector = `[data-assessment-skill=listening] fieldset:nth-of-type(${q + 1}) p:nth-of-type(3)`;
    const explanation = await evaluate(`document.querySelector(${JSON.stringify(explanationSelector)})?.innerText||null`);
    assert.ok(explanation, `Question ${q + 1} has no rendered server explanation`);
    await scrollUntilReachable(explanationSelector, `result-explanation-${q + 1}`, { fully: true });
    const visible = await evaluate(`(()=>{const e=document.querySelector(${JSON.stringify(explanationSelector)}),r=e.getBoundingClientRect(),n=document.querySelector('.focus-top-nav')?.getBoundingClientRect();return {text:e.innerText,top:r.top,bottom:r.bottom,navBottom:n?.bottom||0,fullyVisible:r.top>=(n?.bottom||0)&&r.bottom<=innerHeight}})()`);
    assert.ok(visible.fullyVisible, `Explanation ${q + 1} not fully visible: ${JSON.stringify(visible)}`);

    explanationIds.push({ question: q + 1, text: explanation, ...visible });
  }
  await record('all-result-explanations-touch-read', { explanations: explanationIds });

  const beforeReverse = await state();
  for (let i = 0; i < 14; i += 1) {
    const now = await state();
    if (now.scrollY <= 2) break;
    await gesture('down', 205);
  }
  const afterReverse = await state();
  assert.ok(afterReverse.scrollY < beforeReverse.scrollY, 'downward finger swipe must move content upward');
  await screenshot('result-returned-up-by-touch.png');
  await record('reverse-touch-scroll', { before: beforeReverse.scrollY, after: afterReverse.scrollY, direction: 'finger-down/content-up' });

  await scrollUntilReachable('.focus-theme-toggle', 'theme-toggle-reachable', { fully: true, allowNav: true });
  const themeBefore = await evaluate('document.documentElement.dataset.theme');
  await touchElement('.focus-theme-toggle', 'switch-theme');
  const nextTheme = await evaluate('document.documentElement.dataset.theme');
  assert.notEqual(nextTheme, themeBefore, 'touch theme toggle did not change theme');
  const alternateThemeRun = { theme: nextTheme, scenario: 'post-switch-touch-scroll', status: 'running' };
  output.themeRuns.push(alternateThemeRun);
  const beforeThemeSwipe = await state();
  await gesture('up', 185);
  const afterThemeSwipe = await state();
  assert.ok(afterThemeSwipe.scrollY > beforeThemeSwipe.scrollY, 'scroll did not work after theme switch');
  await screenshot('alternate-theme-touch-scroll.png');
  await record('alternate-theme-scroll', { before_theme: themeBefore, after_theme: nextTheme, before: beforeThemeSwipe.scrollY, after: afterThemeSwipe.scrollY });
  alternateThemeRun.status = 'pass';
  alternateThemeRun.scroll_before = beforeThemeSwipe.scrollY;
  alternateThemeRun.scroll_after = afterThemeSwipe.scrollY;
  await gesture('down', 185);

  await send('Page.reload');
  await waitFor("document.body.innerText.includes('本次答对')");
  const restored = await evaluate("({runId:localStorage.getItem('assessment:selected:default_learner'),score:document.body.innerText.match(/本次答对\\s*\\d+\\s*\\/\\s*6\\s*题/)?.[0]||null,theme:document.documentElement.dataset.theme})");
  assert.equal(restored.runId, output.runId);
  assert.equal(restored.score, score);
  await screenshot('refreshed-result-restored.png');
  await record('refresh-restores-server-result', { ...restored, same_run: restored.runId === output.runId, same_score: restored.score === score });
  // New run; save a partial draft before causing a real audio transport failure.
  await scrollUntilReachable('.focus-theme-toggle','audio-failure-top',{fully:true,allowNav:true});
  assert.equal(await markButton('开始新一轮评估','touch-create-second'),true);
  await scrollUntilReachable('#touch-create-second','new-run-reachable');await touchElement('#touch-create-second','create-second-run');
  await waitFor("document.body.innerText.includes('Listening：not_started')");
  output.failureRunId=await evaluate("localStorage.getItem('assessment:selected:default_learner')");
  assert.notEqual(output.failureRunId,output.runId);
  assert.equal(await markButton('开始 Listening','touch-start-second'),true);await scrollUntilReachable('#touch-start-second','start-second-reachable');await touchElement('#touch-start-second','start-second');
  await waitFor("document.querySelectorAll('[data-assessment-skill=listening] fieldset').length===6");
  const option='[data-assessment-skill=listening] fieldset:nth-of-type(1) input[type=radio]';await scrollUntilReachable(option,'failure-draft-option');await touchElement(option,'failure-draft-select');
  await waitFor("document.querySelectorAll('[data-assessment-skill=listening] input:checked').length===1");
  failAudio=true;await send('Fetch.enable',{patterns:[{urlPattern:'*/audio/listening/initial-community-arts-v1.wav*',requestStage:'Request'}]});
  await send('Network.setCacheDisabled',{cacheDisabled:true});await send('Page.reload');
  await waitFor("document.querySelector('[data-assessment-skill=listening] [role=alert]')?.innerText.includes('音频无法加载')");
  assert.ok(audioFaults.length>0);assert.equal(await evaluate("document.querySelectorAll('[data-assessment-skill=listening] input:checked').length"),1);
  assert.equal(await evaluate("document.querySelector('[data-assessment-skill=listening]').innerText.includes('本次答对')"),false);
  await scrollUntilReachable('[data-assessment-skill=listening] [role=alert]','audio-error');await screenshot('audio-error.png');
  failAudio=false;await send('Fetch.disable');assert.equal(await markButton('重新加载音频','audio-retry'),true);await touchElement('#audio-retry','audio-retry');await waitFor("document.querySelector('audio').readyState>=2");
  await scrollUntilReachable('audio[aria-label="Listening 初始评估音频"]','audio-recovered');const retryBox=await selectorBox('audio[aria-label="Listening 初始评估音频"]');await tapAt(Math.round(retryBox.left+23),Math.round((retryBox.top+retryBox.bottom)/2),'replay-after-error');await waitFor("document.querySelector('audio').currentTime>0.2&&!document.querySelector('audio').paused");
  await send('Page.reload');await waitFor("document.querySelectorAll('[data-assessment-skill=listening] input:checked').length===1");assert.equal(await evaluate("localStorage.getItem('assessment:selected:default_learner')"),output.failureRunId);await record('audio-failure-recovery',{faults:audioFaults,draft_count:1,run_id:output.failureRunId});
  // Real keyboard Tab focus, computed styles and hit testing.
  const focus=[];for(let i=0;i<50;i++){await send('Input.dispatchKeyEvent',{type:'keyDown',key:'Tab',code:'Tab',windowsVirtualKeyCode:9});await send('Input.dispatchKeyEvent',{type:'keyUp',key:'Tab',code:'Tab',windowsVirtualKeyCode:9});const f=await evaluate(`(()=>{let e=document.activeElement,s=getComputedStyle(e),r=e.getBoundingClientRect();return {tag:e.tagName,text:(e.innerText||e.getAttribute('aria-label')||'').slice(0,80),outline:s.outline,boxShadow:s.boxShadow,top:r.top,bottom:r.bottom,visible:r.width>0&&r.height>0}})()`);if(f.visible)focus.push(f);if(f.tag==='INPUT'){await waitForScrollStop();await pause(300);const final=await evaluate(`(()=>{let e=document.activeElement,s=getComputedStyle(e),r=e.getBoundingClientRect();return {tag:e.tagName,outline:s.outline,top:r.top,bottom:r.bottom,navBottom:document.querySelector('.focus-top-nav')?.getBoundingClientRect().bottom||0}})()`);focus.push(final);assert.ok(final.top>=final.navBottom && final.bottom<=844 && final.outline.includes('solid'));break;}}assert.ok(focus.some(x=>x.tag==='INPUT'));output.keyboardFocus=focus;await screenshot('keyboard-focus.png');
  output.themeRuns[0].status = 'pass';
  output.themeRuns[0].run_id = output.runId;
  output.themeRuns[0].score = score;
  output.status = 'pass';
  await flush();
} catch (error) {
  output.status = 'fail';
  output.failures.push({ message: error.message, stack: error.stack });
  await flush();
  throw error;
} finally {
  ws.close();
}
