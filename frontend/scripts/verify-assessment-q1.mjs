import assert from 'node:assert/strict';
import {mkdir,writeFile} from 'node:fs/promises';
import {join} from 'node:path';
const {SMOKE_UI_URL:ui,SMOKE_CDP_URL:cdp,SMOKE_OUTPUT_DIR:out}=process.env;
const target=(await (await fetch(`${cdp}/json/list`)).json()).find(x=>x.type==='page');
const ws=new WebSocket(target.webSocketDebuggerUrl);await new Promise((r,j)=>{ws.onopen=r;ws.onerror=j;});
let seq=0;const pending=new Map();ws.onmessage=({data})=>{const m=JSON.parse(data);if(m.id){pending.get(m.id)?.(m);pending.delete(m.id);}};
const send=(method,params={})=>new Promise((resolve,reject)=>{const id=++seq;const timer=setTimeout(()=>reject(Error(method)),20000);pending.set(id,m=>{clearTimeout(timer);m.error?reject(Error(JSON.stringify(m.error))):resolve(m.result);});ws.send(JSON.stringify({id,method,params}));});
const ev=async expression=>{const r=await send('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true});if(r.exceptionDetails)throw Error(JSON.stringify(r.exceptionDetails));return r.result.value;};
const wait=async expression=>{for(let i=0;i<150;i++){if(await ev(expression))return;await new Promise(r=>setTimeout(r,150));}throw Error(`Timeout ${expression}`);};
const click=async text=>{await wait(`Array.from(document.querySelectorAll('button')).some(x=>x.textContent.includes(${JSON.stringify(text)})&&!x.disabled)`);await ev(`Array.from(document.querySelectorAll('button')).find(x=>x.textContent.includes(${JSON.stringify(text)})&&!x.disabled).click()`);};
const shot=async name=>{const {data}=await send('Page.captureScreenshot',{format:'png'});await writeFile(join(out,name),Buffer.from(data,'base64'));};
const offline=async value=>send('Network.emulateNetworkConditions',{offline:value,latency:0,downloadThroughput:-1,uploadThroughput:-1});
try{
 await mkdir(out,{recursive:true});await send('Runtime.enable');await send('Page.enable');await send('Network.enable');
 await send('Page.navigate',{url:ui+'/learn/assessment'});await wait("document.body.innerText.includes('暂无评估记录')");
 await offline(true);await click('开始新一轮评估');await wait("!!document.querySelector('[role=alert]')");await shot('create-offline.png');
 const firstKey=await ev("localStorage.getItem('assessment:create:default_learner')");assert.ok(firstKey);
 await offline(false);await click('开始新一轮评估');await wait("document.body.innerText.includes('开始 Reading')");const run=await ev("localStorage.getItem('assessment:selected:default_learner')");
 await click('开始 Reading');await wait("document.querySelectorAll('fieldset').length===4");await ev("document.querySelectorAll('fieldset').forEach(f=>f.querySelector('input').click())");
 await offline(true);await click('提交作答');await wait("!!document.querySelector('[role=alert]')");assert.equal(await ev("document.body.innerText.includes('本次答对')"),false);await shot('submit-offline.png');
 await offline(false);await click('重新连接');await wait("document.querySelectorAll('fieldset').length===4");await click('提交作答');await wait("document.body.innerText.includes('本次答对 1 / 4 题')");await shot('submit-recovered.png');
 await writeFile(join(out,'network-report.json'),JSON.stringify({passed:['create request offline and stable key retry','submit request offline and state query after reconnect','real backend retry result'],run_id:run,create_idempotency_key:firstKey,failed:[],skipped:['successful backend submit with lost response','V1 browser history'],retested:[]},null,2));
}finally{await offline(false).catch(()=>{});ws.close();}
