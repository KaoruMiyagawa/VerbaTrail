import assert from 'node:assert/strict';
import {mkdir,writeFile} from 'node:fs/promises';
import {join} from 'node:path';
const {SMOKE_UI_URL:ui,SMOKE_CDP_URL:cdp,SMOKE_OUTPUT_DIR:out}=process.env;
const target=(await (await fetch(`${cdp}/json/list`)).json()).find(x=>x.type==='page');
const ws=new WebSocket(target.webSocketDebuggerUrl);await new Promise((r,j)=>{ws.onopen=r;ws.onerror=j;});
let seq=0;const pending=new Map(),requests=[],intercepts=[];let dropSubmit=false,dropRecovery=false,runId;
const send=(method,params={})=>new Promise((resolve,reject)=>{const id=++seq;const timer=setTimeout(()=>reject(Error(method)),20000);pending.set(id,m=>{clearTimeout(timer);m.error?reject(Error(JSON.stringify(m.error))):resolve(m.result);});ws.send(JSON.stringify({id,method,params}));});
ws.onmessage=({data})=>{const m=JSON.parse(data);if(m.id){pending.get(m.id)?.(m);pending.delete(m.id);return;}if(m.method==='Network.requestWillBeSent' && m.params.request.url.includes('/api/v1/assessment-runs'))requests.push({method:m.params.request.method,url:m.params.request.url,body:m.params.request.postData||null});if(m.method==='Fetch.requestPaused'){const p=m.params;const submit=p.request.method==='POST' && p.request.url.includes('/reading/submit');const recovery=p.request.method==='GET' && p.request.url.includes('/reading?');if(submit && dropSubmit){dropSubmit=false;intercepts.push({kind:'submit_response',status:p.responseStatusCode,url:p.request.url,body:p.request.postData});void send('Fetch.failRequest',{requestId:p.requestId,errorReason:'ConnectionReset'});}else if(recovery && !dropSubmit && dropRecovery){dropRecovery=false;intercepts.push({kind:'recovery_response',status:p.responseStatusCode,url:p.request.url});void send('Fetch.failRequest',{requestId:p.requestId,errorReason:'ConnectionReset'});}else void send('Fetch.continueRequest',{requestId:p.requestId});}};
const ev=async expression=>{const r=await send('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true});if(r.exceptionDetails)throw Error(JSON.stringify(r.exceptionDetails));return r.result.value;};
const wait=async expression=>{for(let i=0;i<200;i++){if(await ev(expression))return;await new Promise(r=>setTimeout(r,100));}throw Error(`Timeout ${expression}`);};
const click=async text=>{await wait(`Array.from(document.querySelectorAll('button')).some(x=>x.textContent.includes(${JSON.stringify(text)})&&!x.disabled)`);await ev(`Array.from(document.querySelectorAll('button')).find(x=>x.textContent.includes(${JSON.stringify(text)})&&!x.disabled).click()`);};
const shot=async name=>{const {data}=await send('Page.captureScreenshot',{format:'png'});await writeFile(join(out,name),Buffer.from(data,'base64'));};
try{
 await mkdir(out,{recursive:true});await send('Runtime.enable');await send('Page.enable');await send('Network.enable');await send('Page.navigate',{url:ui+'/learn/assessment'});await wait("document.body.innerText.includes('评估记录')");
 const ids=JSON.parse(process.env.Q1_V1_IDS);await ev(`localStorage.setItem('assessment:selected:default_learner',${JSON.stringify(ids[0])});localStorage.setItem('assessment:skill:default_learner','reading')`);await send('Page.reload');
 await wait("document.body.innerText.includes('本次答对 4 / 4')");assert.equal(await ev("document.body.innerText.includes('旧版题目设计存在答案位置偏差')"),true);await shot('v1-reading.png');
 await click('Listening：');await wait("document.body.innerText.includes('此历史任务无法按已固定版本恢复')");await shot('v1-listening-409.png');await click('Reading：');await wait("document.body.innerText.includes('本次答对 4 / 4')");
 await writeFile(join(out,'v1-browser.json'),JSON.stringify({status:'pass',run_id:ids[0],checks:['V1 Reading completed recovered','Listening 409 displayed','Reading remains visible after 409'],requests},null,2));
}finally{ws.close();}
