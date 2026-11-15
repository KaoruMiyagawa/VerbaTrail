import assert from 'node:assert/strict';
import { mkdir, writeFile } from 'node:fs/promises';
import { join } from 'node:path';

const { SMOKE_UI_URL: ui, SMOKE_CDP_URL: cdp, SMOKE_OUTPUT_DIR: out } = process.env;
if (!ui || !cdp || !out) throw new Error('SMOKE_UI_URL, SMOKE_CDP_URL and SMOKE_OUTPUT_DIR are required');
const target = (await (await fetch(`${cdp}/json/list`)).json()).find(item => item.type === 'page');
const ws = new WebSocket(target.webSocketDebuggerUrl);
await new Promise((resolve, reject) => { ws.onopen = resolve; ws.onerror = reject; });
let sequence = 0;
const pending = new Map();
const traversal = [];
ws.onmessage = ({ data }) => {
  const message = JSON.parse(data);
  if (message.id) { pending.get(message.id)?.(message); pending.delete(message.id); }
};
const send = (method, params = {}) => new Promise((resolve, reject) => {
  const id = ++sequence;
  const timeout = setTimeout(() => { pending.delete(id); reject(new Error(`CDP timeout: ${method}`)); }, 20000);
  pending.set(id, message => { clearTimeout(timeout); message.error ? reject(Error(JSON.stringify(message.error))) : resolve(message.result); });
  ws.send(JSON.stringify({ id, method, params }));
});
const evaluate = async expression => {
  const result = await send('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true });
  if (result.exceptionDetails) throw new Error(JSON.stringify(result.exceptionDetails));
  return result.result.value;
};
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
const waitFor = async expression => {
  for (let i = 0; i < 300; i += 1) { if (await evaluate(expression)) return; await pause(100); }
  throw new Error(`Timed out waiting for: ${expression}`);
};
const key = async (name, code, keyCode, modifiers = 0) => {
  await send('Input.dispatchKeyEvent', { type: 'keyDown', key: name, code, windowsVirtualKeyCode: keyCode, modifiers, ...(name === 'Enter' ? { text: '\r', unmodifiedText: '\r' } : {}) });
  await send('Input.dispatchKeyEvent', { type: 'keyUp', key: name, code, windowsVirtualKeyCode: keyCode, modifiers });
  await pause(120);
};
const tab = async (backward = false) => {
  await key('Tab', 'Tab', 9, backward ? 8 : 0);
  await settleFocus();
  const item = await evaluate(`(() => {
    const e=document.activeElement,s=getComputedStyle(e),r=e.getBoundingClientRect();
    const focusable=e.matches('button,a,input,select,textarea,summary,[tabindex]:not([tabindex="-1"])');
    const hidden=!!e.closest('[hidden],[aria-hidden="true"]')||s.display==='none'||s.visibility==='hidden'||parseFloat(s.opacity)===0;
    const visible=focusable&&!hidden&&r.width>0&&r.height>0&&r.right>0&&r.left<innerWidth&&r.bottom>0&&r.top<innerHeight;
    return {tag:e.tagName,text:(e.innerText||e.labels?.[0]?.innerText||e.getAttribute('aria-label')||e.textContent||'').trim().replace(/\\s+/g,' ').slice(0,90),focusable,hidden,visible,rect:{top:r.top,right:r.right,bottom:r.bottom,left:r.left}};
  })()`);
  if (item.focusable) traversal.push(item);
  assert.ok(!item.focusable || item.visible, `Tab order reached hidden/off-screen control: ${JSON.stringify(item)}`);
};
const keyTo = async (expression, label) => {
  for (let i = 0; i < 100; i += 1) {
    if (await evaluate(expression)) return;
    await tab();
  }
  throw new Error(`Keyboard traversal could not reach ${label}`);
};
const settleFocus = async () => {
  let prior = null;
  for (let i = 0; i < 10; i += 1) {
    await pause(80);
    const current = await evaluate(`(() => {const r=document.activeElement.getBoundingClientRect();return [r.x,r.y,r.width,r.height].map(Math.round).join(',')})()`);
    if (current === prior) return;
    prior = current;
  }
};
const capture = async name => {
  const { data } = await send('Page.captureScreenshot', { format: 'png', captureBeyondViewport: false });
  await writeFile(join(out, `${name}.png`), Buffer.from(data, 'base64'));
};
const describeFocus = async () => evaluate(`(() => {
  const e=document.activeElement, s=getComputedStyle(e), r=e.getBoundingClientRect();
  const nav=document.querySelector('.focus-top-nav'), n=nav?.getBoundingClientRect(), ns=nav&&getComputedStyle(nav);
  const outlineVisible=s.outlineStyle!=='none' && parseFloat(s.outlineWidth)>=2 && s.outlineColor!=='transparent';
  const boxShadowVisible=s.boxShadow!=='none';
  const visualIndicator=outlineVisible||boxShadowVisible;
  const navOverlap=!!(n&&ns&&['fixed','sticky'].includes(ns.position)&&r.left<n.right&&r.right>n.left&&r.top<n.bottom&&r.bottom>n.top);
  const visible=r.width>0&&r.height>0&&r.right>0&&r.left<innerWidth&&r.bottom>0&&r.top<innerHeight;
  return {tag:e.tagName,type:e.type||null,text:(e.innerText||e.labels?.[0]?.innerText||e.getAttribute('aria-label')||e.textContent||'').trim().replace(/\\s+/g,' ').slice(0,100),name:e.getAttribute('name'),focusVisible:e.matches(':focus-visible'),outline:{style:s.outlineStyle,width:s.outlineWidth,color:s.outlineColor,offset:s.outlineOffset},boxShadow:s.boxShadow,rect:{x:r.x,y:r.y,width:r.width,height:r.height,top:r.top,right:r.right,bottom:r.bottom,left:r.left},viewportVisible:visible,nav:{position:ns?.position||null,rect:n?{top:n.top,right:n.right,bottom:n.bottom,left:n.left}:null,overlap:navOverlap},visualIndicator,pass:visible&&visualIndicator&&!navOverlap};
})()`);
const recordFocus = async (theme, id, expected, report) => {
  const focus = await describeFocus();
  const pass = expected(focus);
  const file = `focus-${theme}-${id}`;
  await capture(file);
  report.push({ theme, control: id, expected: focus.text, ...focus, screenshot: `${file}.png`, status: pass ? 'pass' : 'fail' });
  assert.ok(pass, `${theme}/${id} failed visible keyboard focus: ${JSON.stringify(focus)}`);
};
const focusButton = (text) => `document.activeElement.matches('button') && document.activeElement.textContent.includes(${JSON.stringify(text)})`;
const focusRadioForQuestion = index => `document.activeElement.matches('input[type="radio"]') && document.activeElement.name === document.querySelectorAll('fieldset')[${index}].querySelector('input[type="radio"]').name`;
const radioEnabled = focus => focus.tag === 'INPUT' && focus.type === 'radio' && focus.name;

const results = [];
try {
  await mkdir(out, { recursive: true });
  await send('Runtime.enable'); await send('Page.enable');
  await send('Emulation.setDeviceMetricsOverride', { width: 1440, height: 1000, deviceScaleFactor: 1, mobile: false });
  await send('Page.navigate', { url: ui });
  await waitFor("document.body.innerText.includes('进入初始能力评估')");

  for (const [theme, passIndex] of [['premium', 0], ['noir', 1]]) {
    if (theme === 'noir') {
      await keyTo("document.activeElement.matches('.focus-theme-toggle')", 'theme toggle');
      await key(' ', 'Space', 32);
      await waitFor("document.documentElement.dataset.theme==='noir'");
    }
    assert.equal(await evaluate('document.documentElement.dataset.theme'), theme);

    await keyTo("document.activeElement.matches('button') && document.activeElement.textContent.includes('进入初始能力评估')", 'Learn assessment entry');
    await recordFocus(theme, 'learn-entry', focus => focus.tag === 'BUTTON' && focus.visualIndicator && focus.viewportVisible && !focus.nav.overlap, results);
    await key('Enter', 'Enter', 13);
    await waitFor("document.querySelector('.assessment-page') && document.body.innerText.includes('开始新一轮评估')");

    await keyTo(focusButton('开始新一轮评估'), 'create assessment');
    await recordFocus(theme, 'create-run', focus => focusButtonByText(focus, '开始新一轮评估') && focus.visualIndicator && focus.viewportVisible && !focus.nav.overlap, results);
    await key('Enter', 'Enter', 13);
    await waitFor("document.body.innerText.includes('开始 Reading')");

    await keyTo(focusButton('开始 Reading'), 'start Reading');
    await recordFocus(theme, 'start-reading', focus => focusButtonByText(focus, '开始 Reading') && focus.visualIndicator && focus.viewportVisible && !focus.nav.overlap, results);
    await key('Enter', 'Enter', 13);
    await waitFor('document.querySelectorAll("fieldset input[type=radio]").length>=8');

    for (let question = 0; question < 4; question += 1) {
      await keyTo(focusRadioForQuestion(question), `question ${question + 1} radio`);
      await recordFocus(theme, `question-${question + 1}-radio`, focus => radioEnabled(focus) && focus.focusVisible && focus.visualIndicator && focus.viewportVisible && !focus.nav.overlap, results);
      await key(' ', 'Space', 32);
      assert.equal(await evaluate(`document.querySelectorAll('fieldset')[${question}].querySelector('input:checked') !== null`), true, `Space must select question ${question + 1}`);
    }

    await keyTo(focusButton('提交作答'), 'submit assessment');
    await recordFocus(theme, 'submit', focus => focusButtonByText(focus, '提交作答') && focus.visualIndicator && focus.viewportVisible && !focus.nav.overlap, results);
    await key('Enter', 'Enter', 13);
    await waitFor("document.body.innerText.includes('本次答对')");

    await keyTo("document.activeElement.matches('section button') && document.activeElement.textContent.includes('initial-v2') && document.activeElement.textContent.includes('completed')", 'history run selector');
    await recordFocus(theme, 'history-select', focus => focus.tag === 'BUTTON' && focus.text.includes('initial-v2') && focus.visualIndicator && focus.viewportVisible && !focus.nav.overlap, results);
    await tab(true);
    assert.equal(await evaluate("document.activeElement.matches('button') && document.activeElement.textContent.includes('开始新一轮评估')"), true, 'Shift+Tab from history must focus create-run control');
    await tab();
    assert.equal(await evaluate("document.activeElement.matches('section button') && document.activeElement.textContent.includes('initial-v2')"), true, 'Tab must return from create-run to history selector');
    await key('Enter', 'Enter', 13);
    await waitFor("document.body.innerText.includes('本次答对')");

    await keyTo(focusButton('返回 Learn'), 'return Learn');
    await recordFocus(theme, 'return-learn', focus => focusButtonByText(focus, '返回 Learn') && focus.visualIndicator && focus.viewportVisible && !focus.nav.overlap, results);
    await key('Enter', 'Enter', 13);
    await waitFor("document.body.innerText.includes('进入初始能力评估')");
    assert.equal(await evaluate('document.documentElement.dataset.theme'), theme);
  }

  const grouped = Object.fromEntries(['premium', 'noir'].map(theme => [theme, results.filter(x => x.theme === theme)]));
  const report = {
    status: results.length === 20 && results.every(x => x.status === 'pass') ? 'pass' : 'fail',
    viewport: { width: 1440, height: 1000 },
    input: 'Chrome DevTools Protocol Input.dispatchKeyEvent Tab, Shift+Tab, Enter and Space',
    themes: { premium: 'Premium Academic', noir: 'Noir & Champagne' },
    matrix: grouped,
    traversal,
    checks: { keyboardReachability: true, visibleIndicator: true, viewportVisibility: true, fixedNavigationOcclusion: false, hiddenFocusStealingObserved: false, shiftTabReverseOrder: true, spaceSelectionForAllQuestions: true, enterActivationForActions: true },
    failures: [],
  };
  await writeFile(join(out, 'focus-matrix.json'), JSON.stringify(report, null, 2), 'utf8');
  assert.equal(report.status, 'pass');
} catch (error) {
  const grouped = Object.fromEntries(['premium', 'noir'].map(theme => [theme, results.filter(x => x.theme === theme)]));
  await writeFile(join(out, 'focus-matrix.json'), JSON.stringify({ status: 'fail', viewport: { width: 1440, height: 1000 }, input: 'Chrome DevTools Protocol keyboard events', matrix: grouped, completed: results, failures: [{ message: error.message, stack: error.stack }] }, null, 2), 'utf8');
  throw error;
} finally { ws.close(); }

function focusButtonByText(focus, text) { return focus.tag === 'BUTTON' && focus.text.includes(text); }
