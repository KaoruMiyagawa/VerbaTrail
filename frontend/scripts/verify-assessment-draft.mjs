import assert from 'node:assert/strict';
import {mkdir,writeFile} from 'node:fs/promises';
import {join} from 'node:path';
const {SMOKE_UI_URL:ui,SMOKE_CDP_URL:cdp,SMOKE_OUTPUT_DIR:out,DRAFT_IDS,DRAFT_HISTORY}=process.env;
const ids=JSON.parse(DRAFT_IDS),history=JSON.parse(DRAFT_HISTORY),log=[];
const target=(await (await fetch(`${cdp}/json/list`)).json()).find(x=>x.type==='page');const ws=new WebSocket(target.webSocketDebuggerUrl);await new Promise((r,j)=>{ws.onopen=r;ws.onerror=j;});let seq=0;const pending=new Map();
ws.onmessage=({data})=>{const m=JSON.parse(data);if(m.id){pending.get(m.id)?.(m);pending.delete(m.id);}};
const send=(method,params={})=>new Promise((resolve,reject)=>{const id=++seq;const timer=setTimeout(()=>reject(Error(method)),20000);pending.set(id,m=>{clearTimeout(timer);m.error?reject(Error(JSON.stringify(m.error))):resolve(m.result);});ws.send(JSON.stringify({id,method,params}));});
const ev=async expression=>{const r=await send('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true});if(r.exceptionDetails)throw Error(JSON.stringify(r.exceptionDetails));return r.result.value;};
const wait=async expression=>{for(let i=0;i<200;i++){if(await ev(expression))return;await new Promise(r=>setTimeout(r,100));}throw Error(`Timeout ${expression}`);};
const select=async i=>{await wait("document.querySelectorAll('section ul button').length>=3");await ev(`document.querySelectorAll('section ul button')[${history.indexOf(ids[i])}].click()`);await wait(`localStorage.getItem('assessment:selected:default_learner')===${JSON.stringify(ids[i])} && document.querySelectorAll('fieldset').length===4`);await wait("Array.from(document.querySelectorAll('fieldset')).every(f=>!f.disabled)");log.push({step:'select',run_id:ids[i],version:await ev("document.body.innerText.match(/reading-initial-community-garden-v[12]/)?.[0]")});};
const checked=()=>ev("Array.from(document.querySelectorAll('fieldset')).map(f=>Array.from(f.querySelectorAll('input')).findIndex(x=>x.checked))");
const choose=async (question,option)=>ev(`document.querySelectorAll('fieldset')[${question}].querySelectorAll('input')[${option}].click()`);
const shot=async name=>{await ev("document.querySelector('fieldset').scrollIntoView({block:'start',behavior:'instant'})");const {data}=await send('Page.captureScreenshot',{format:'png'});await writeFile(join(out,name),Buffer.from(data,'base64'));};
try{
 await mkdir(out,{recursive:true});await send('Runtime.enable');await send('Page.enable');await send('Emulation.setDeviceMetricsOverride',{width:1440,height:1000,deviceScaleFactor:1,mobile:false});await send('Page.navigate',{url:ui+'/learn/assessment'});
 await select(0);assert.equal(log.at(-1).version,'reading-initial-community-garden-v1');await choose(0,1);await choose(1,2);await shot('v1-draft-before-refresh.png');
 await select(1);assert.equal(log.at(-1).version,'reading-initial-community-garden-v2');assert.deepEqual(await checked(),[-1,-1,-1,-1]);await choose(0,2);await choose(1,0);
 await select(0);assert.deepEqual(await checked(),[1,2,-1,-1]);await select(1);assert.deepEqual(await checked(),[2,0,-1,-1]);await shot('v2-draft-before-refresh.png');
 await select(2);assert.deepEqual(await checked(),[-1,-1,-1,-1]);await choose(0,3);await send('Page.reload');await wait(`localStorage.getItem('assessment:selected:default_learner')===${JSON.stringify(ids[2])} && document.querySelectorAll('fieldset').length===4`);await wait("document.querySelectorAll('fieldset input:checked').length===1");assert.deepEqual(await checked(),[3,-1,-1,-1]);await shot('v2-second-after-refresh.png');
 await select(0);assert.deepEqual(await checked(),[1,2,-1,-1]);await send('Page.reload');await wait("document.querySelectorAll('fieldset input:checked').length===2");assert.deepEqual(await checked(),[1,2,-1,-1]);await shot('v1-draft-after-refresh.png');
 await select(1);assert.deepEqual(await checked(),[2,0,-1,-1]);await send('Page.reload');await wait("document.querySelectorAll('fieldset input:checked').length===2");assert.deepEqual(await checked(),[2,0,-1,-1]);await shot('v2-draft-after-refresh.png');await choose(2,3);await choose(3,1);await ev("Array.from(document.querySelectorAll('button')).find(b=>b.textContent.includes('提交作答')).click()");await wait("document.body.innerText.includes('本次答对 4 / 4 题')");
 const key=`assessment:draft:default_learner:${ids[1]}:reading-initial-community-garden-v2`;
 await ev(`localStorage.setItem(${JSON.stringify(key)},JSON.stringify({'readgarden-v2-q1':'Vegetables for the café'}))`);await send('Page.reload');await wait("document.body.innerText.includes('本次答对 4 / 4 题')");assert.deepEqual(await checked(),[2,0,3,1]);await shot('server-result-after-refresh.png');
 await select(0);assert.deepEqual(await checked(),[1,2,-1,-1]);await select(2);assert.deepEqual(await checked(),[3,-1,-1,-1]);
 const keys=await ev("Object.keys(localStorage).filter(k=>k.startsWith('assessment:draft:')).sort()");assert.equal(keys.length,3);for(const i of [0,1,2])assert.ok(keys.some(k=>k.includes(`default_learner:${ids[i]}:reading-initial-community-garden-v${i===0?1:2}`)));
 await writeFile(join(out,'report.json'),JSON.stringify({status:'pass',run_ids:ids,storage_keys:keys,steps:log,checks:['V1 and V2 drafts remain separate','different V2 runs remain separate','refresh retains drafts','submitted server result overrides altered local draft','other runs retain drafts'],failed:[],skipped:[],retested:[],fixture:'historical V1 created via real API with old version-selection behavior in isolated process'},null,2));
}finally{ws.close();}
