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
const pending = new Map();
ws.onmessage = ({ data }) => {
  const message = JSON.parse(data);
  if (message.id) { pending.get(message.id)?.(message); pending.delete(message.id); }
  else if (message.method === 'Network.requestWillBeSent') {
    const request = message.params.request;
    if (request.url.includes('/api/v1/assessment-runs')) output.browserRequests.push({ method: request.method, url: request.url, body: request.postData || null });
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
      await screenshot(id);
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
  output.themeRuns.push({ theme: current.theme, scenario: 'full-reading-assessment', status: 'running' });
  await screenshot('assessment-entry-mobile.png');

  await waitFor("[...document.querySelectorAll('button')].some(button=>button.textContent.includes('开始新一轮评估')&&!button.disabled)");
  assert.equal(await markButton('开始新一轮评估', 'touch-create-run'), true);
  await touchElement('#touch-create-run', 'create-assessment');
  await waitFor("document.body.innerText.includes('开始 Reading')");
  output.runId = await evaluate("localStorage.getItem('assessment:selected:default_learner')");
  assert.ok(output.runId);
  await record('assessment-created', { run_id: output.runId, theme: current.theme });
  assert.equal(await markButton('开始 Reading', 'touch-start-reading'), true);
  await touchElement('#touch-start-reading', 'start-reading');
  await waitFor('document.querySelectorAll("fieldset input[type=radio]").length>=8');
  output.sessionId = await evaluate("document.body.innerText.match(/session_[A-Za-z0-9_-]+/)?.[0]||null");
  await record('reading-started', { run_id: output.runId, session_id: output.sessionId, version: await evaluate("document.body.innerText.match(/reading-initial-community-garden-v[12]/)?.[0]||null") });

  await gesture('down', 205);
  await scrollUntilReachable('article[lang="en"]', 'passage-start', { fully: false });
  await screenshot('passage-visible-before-scroll.png');
  const beforeMaterialSwipe = await state();
  await gesture('up', 210);
  const afterMaterialSwipe = await state();
  assert.ok(afterMaterialSwipe.scrollY > beforeMaterialSwipe.scrollY, 'upward finger swipe must move content downward while reading');
  await screenshot('passage-after-touch-scroll.png');
  await record('passage-gesture-scroll', { before: beforeMaterialSwipe.scrollY, after: afterMaterialSwipe.scrollY, direction: 'finger-up/content-down' });

  for (let q = 0; q < 4; q += 1) {
    const fieldsetSelector = `fieldset:nth-of-type(${q + 1})`;
    const fieldsetExists = await evaluate(`document.querySelectorAll('fieldset').length>${q}`);
    assert.ok(fieldsetExists, `Question ${q + 1} fieldset not rendered`);
    await scrollUntilReachable(`fieldset:nth-of-type(${q + 1})`, `question-${q + 1}-reachable`, { fully: true });
    const optionSelector = `fieldset:nth-of-type(${q + 1}) input[type="radio"]`;
    const input = await evaluate(`(()=>{const e=document.querySelector(${JSON.stringify(optionSelector)});const r=e.getBoundingClientRect();return {x:r.x+r.width/2,y:r.y+r.height/2,checked:e.checked,disabled:e.disabled}})()`);
    assert.ok(input.y > 0 && input.y < 844, `Question ${q + 1} radio is outside viewport`);
    await tapAt(Math.round(input.x), Math.round(input.y), `select-question-${q + 1}`);
    await waitFor(`document.querySelectorAll('fieldset')[${q}].querySelector('input:checked')!==null`);
    await screenshot(`question-${q + 1}-selected.png`);
    await record(`question-${q + 1}-selected`, { input, selected: await evaluate(`document.querySelectorAll('fieldset')[${q}].querySelector('input:checked')?.value||null`) });
  }

  assert.equal(await markButton('提交作答', 'touch-submit-reading'), true);
  await scrollUntilReachable('#touch-submit-reading', 'submit-reachable', { fully: true });
  const beforeSubmit = await state();
  await touchElement('#touch-submit-reading', 'submit-reading');
  await waitFor("document.body.innerText.includes('本次答对')");
  const score = await evaluate("document.body.innerText.match(/本次答对\\s*\\d+\\s*\\/\\s*4\\s*题/)?.[0]||null");
  assert.ok(score, 'server-scored 4-question result not shown');
  const doneState = await state();
  output.score = score;
  output.runId = await evaluate("localStorage.getItem('assessment:selected:default_learner')");
  await screenshot('server-result-first-view.png');
  await record('server-submission-result', { run_id: output.runId, score, submit_scroll_y: beforeSubmit.scrollY, result_scroll_y: doneState.scrollY });

  const explanationIds = [];
  for (let q = 0; q < 4; q += 1) {
    const explanationSelector = `fieldset:nth-of-type(${q + 1}) p:nth-of-type(3)`;
    const explanation = await evaluate(`document.querySelector(${JSON.stringify(explanationSelector)})?.innerText||null`);
    assert.ok(explanation, `Question ${q + 1} has no rendered server explanation`);
    await scrollUntilReachable(explanationSelector, `result-explanation-${q + 1}`, { fully: true });
    const visible = await evaluate(`(()=>{const e=document.querySelector(${JSON.stringify(explanationSelector)}),r=e.getBoundingClientRect(),n=document.querySelector('.focus-top-nav')?.getBoundingClientRect();return {text:e.innerText,top:r.top,bottom:r.bottom,navBottom:n?.bottom||0,fullyVisible:r.top>=(n?.bottom||0)&&r.bottom<=innerHeight}})()`);
    assert.ok(visible.fullyVisible, `Explanation ${q + 1} not fully visible: ${JSON.stringify(visible)}`);
    await screenshot(`result-explanation-${q + 1}-visible.png`);
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
  const restored = await evaluate("({runId:localStorage.getItem('assessment:selected:default_learner'),score:document.body.innerText.match(/本次答对\\s*\\d+\\s*\\/\\s*4\\s*题/)?.[0]||null,theme:document.documentElement.dataset.theme})");
  assert.equal(restored.runId, output.runId);
  assert.equal(restored.score, score);
  await screenshot('refreshed-result-restored.png');
  await record('refresh-restores-server-result', { ...restored, same_run: restored.runId === output.runId, same_score: restored.score === score });
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
