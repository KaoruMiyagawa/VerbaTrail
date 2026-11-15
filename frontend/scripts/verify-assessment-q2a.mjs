import assert from 'node:assert/strict';
import {mkdir,writeFile} from 'node:fs/promises';
import {join} from 'node:path';
const {SMOKE_UI_URL:ui,SMOKE_CDP_URL:cdp,SMOKE_OUTPUT_DIR:out}=process.env;
const target=(await (await fetch(`${cdp}/json/list`)).json()).find(x=>x.type==='page');
const ws=new WebSocket(target.webSocketDebuggerUrl);await new Promise((r,j)=>{ws.onopen=r;ws.onerror=j;});let seq=0;const pending=new Map(),requests=[],passed=[];
ws.onmessage=({data})=>{const m=JSON.parse(data);if(m.id){pending.get(m.id)?.(m);pending.delete(m.id);}else if(m.method==='Network.requestWillBeSent'&&m.params.request.url.includes('/api/v1/assessment-runs'))requests.push({method:m.params.request.method,url:m.params.request.url});};
const send=(method,params={})=>new Promise((resolve,reject)=>{const id=++seq;const timer=setTimeout(()=>reject(Error(method)),20000);pending.set(id,m=>{clearTimeout(timer);m.error?reject(Error(JSON.stringify(m.error))):resolve(m.result);});ws.send(JSON.stringify({id,method,params}));});
const ev=async expression=>{const r=await send('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true});if(r.exceptionDetails)throw Error(JSON.stringify(r.exceptionDetails));return r.result.value;};
const wait=async expression=>{for(let i=0;i<200;i++){if(await ev(expression))return;await new Promise(r=>setTimeout(r,100));}throw Error(`Timeout ${expression}`);};
const click=async text=>{await wait(`Array.from(document.querySelectorAll('button')).some(x=>x.textContent.includes(${JSON.stringify(text)})&&!x.disabled)`);await ev(`Array.from(document.querySelectorAll('button')).find(x=>x.textContent.includes(${JSON.stringify(text)})&&!x.disabled).click()`);};
const burst=async text=>{await wait(`Array.from(document.querySelectorAll('button')).some(x=>x.textContent.includes(${JSON.stringify(text)})&&!x.disabled)`);await ev(`{const b=Array.from(document.querySelectorAll('button')).find(x=>x.textContent.includes(${JSON.stringify(text)})&&!x.disabled); b.click();b.click();b.click();}`);};
const key=async (name,code,virtual)=>{await send('Input.dispatchKeyEvent',{type:'keyDown',key:name,code,windowsVirtualKeyCode:virtual,...(name==='Enter'?{text:'\r',unmodifiedText:'\r'}:{})});await send('Input.dispatchKeyEvent',{type:'keyUp',key:name,code,windowsVirtualKeyCode:virtual});};
const focusByTab=async expression=>{for(let i=0;i<120;i++){if(await ev(expression))return;await key('Tab','Tab',9);}throw Error('Unable to focus '+expression);};
const shot=async name=>{const {data}=await send('Page.captureScreenshot',{format:'png'});await writeFile(join(out,name),Buffer.from(data,'base64'));};
const touch=async selector=>{await ev(`document.querySelector(${JSON.stringify(selector)}).scrollIntoView({block:'center',behavior:'instant'})`);await new Promise(r=>setTimeout(r,400));const pt=await ev(`(()=>{const r=document.querySelector(${JSON.stringify(selector)}).getBoundingClientRect();return {x:r.x+r.width/2,y:r.y+r.height/2}})()`);await send('Input.dispatchTouchEvent',{type:'touchStart',touchPoints:[pt]});await new Promise(r=>setTimeout(r,80));await send('Input.dispatchTouchEvent',{type:'touchEnd',touchPoints:[]});};
try{
 await mkdir(out,{recursive:true});await send('Runtime.enable');await send('Page.enable');await send('Network.enable');await send('Emulation.setDeviceMetricsOverride',{width:1440,height:1000,deviceScaleFactor:1,mobile:false});
 await send('Page.navigate',{url:ui});await wait("document.body.innerText.includes('初始能力评估')");await click('进入初始能力评估');await burst('开始新一轮评估');await wait("document.body.innerText.includes('开始 Reading')");
 const run=await ev("localStorage.getItem('assessment:selected:default_learner')");
 await click('返回 Learn');await ev('history.back()');await wait("location.pathname==='/learn/assessment' && document.body.innerText.includes('开始 Reading')");await ev('history.forward()');await wait("location.pathname==='/'");await ev('history.back()');await wait("document.body.innerText.includes('开始 Reading')");passed.push('unstarted navigation');
 await burst('开始 Reading');await wait("document.querySelectorAll('fieldset').length===4");
 await focusByTab("document.activeElement?.matches('fieldset input')");await key(' ','Space',32);await key('ArrowRight','ArrowRight',39);
 for(let i=1;i<4;i++){await focusByTab(`document.activeElement?.name===document.querySelectorAll('fieldset')[${i}].querySelector('input').name`);await key(' ','Space',32);}
 assert.equal(await ev("document.querySelectorAll('fieldset input:checked').length"),4);await shot('keyboard-selection.png');
 await click('返回 Learn');await ev('history.back()');await wait("document.querySelectorAll('fieldset input:checked').length===4");assert.equal(await ev("localStorage.getItem('assessment:selected:default_learner')"),run);passed.push('in-progress draft restored');
 await focusByTab("document.activeElement?.textContent?.includes('提交作答') && document.activeElement.tagName==='BUTTON'");await key('Enter','Enter',13);await key('Enter','Enter',13);await key('Enter','Enter',13);await wait("document.body.innerText.includes('本次答对')");await shot('keyboard-result.png');passed.push('keyboard answer and repeated submit');
 await click('返回 Learn');await ev('history.back()');await wait("document.body.innerText.includes('本次答对')");await ev('history.forward()');await wait("location.pathname==='/'");await ev('history.back()');await wait("document.body.innerText.includes('本次答对')");await send('Page.reload');await wait("document.body.innerText.includes('本次答对')");passed.push('completed navigation and reload');
 await click('开始新一轮评估');await wait("document.body.innerText.includes('开始 Reading')");await burst('开始 Reading');await wait("document.querySelectorAll('fieldset').length===4");assert.equal(await ev("document.querySelectorAll('fieldset input:checked').length"),0);passed.push('draft isolated by run');
 await send('Emulation.setDeviceMetricsOverride',{width:390,height:844,deviceScaleFactor:1,mobile:true});await send('Emulation.setTouchEmulationEnabled',{enabled:true,maxTouchPoints:1});assert.ok(await ev('document.documentElement.scrollWidth<=390'));
 await wait("Array.from(document.querySelectorAll('fieldset')).every(x=>!x.disabled)");
 for(let i=1;i<=4;i++){await touch(`fieldset:nth-of-type(${i}) label`);await wait(`document.querySelectorAll('fieldset input:checked').length===${i}`);}
 await wait("document.querySelectorAll('fieldset input:checked').length===4");await shot('touch-answers.png');
 await ev("Array.from(document.querySelectorAll('button')).find(x=>x.textContent.includes('提交作答')).dataset.q2submit='yes'");await touch('button[data-q2submit=yes]');
 await wait("document.body.innerText.includes('本次答对')");await shot('touch-result.png');passed.push('mobile touch answer and submit');
 await touch('.focus-theme-toggle');await wait("document.documentElement.dataset.theme==='noir'");assert.ok(await ev('document.documentElement.scrollWidth<=390'));passed.push('mobile touch theme');
 await ev("Array.from(document.querySelectorAll('button')).find(x=>x.textContent.includes('返回 Learn')).dataset.q2back='yes'");await touch('button[data-q2back=yes]');await wait("location.pathname==='/'");passed.push('mobile touch return');
 await writeFile(join(out,'browser-report.json'),JSON.stringify({passed,requests,button_bursts:{create:3,start:6,keyboard_submit:3},failed:[],skipped:['focus visibility visual audit','cross-version draft isolation','physical touch scroll gesture'],retested:['First run Enter lacked character event; corrected CDP input. Subsequent touch events initially missed during animated scrolling; switched to instant scroll and repeated.']},null,2));
}finally{ws.close();}
