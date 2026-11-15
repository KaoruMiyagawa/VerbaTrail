import assert from 'node:assert/strict';
import {mkdir,writeFile} from 'node:fs/promises';
import {join} from 'node:path';
const {SMOKE_UI_URL:ui,SMOKE_CDP_URL:cdp,SMOKE_OUTPUT_DIR:out}=process.env;
const target=(await (await fetch(`${cdp}/json/list`)).json()).find(x=>x.type==='page');
const ws=new WebSocket(target.webSocketDebuggerUrl);await new Promise((r,j)=>{ws.onopen=r;ws.onerror=j;});let seq=0;const pending=new Map(),requests=[],events=[],scroll=[];
ws.onmessage=({data})=>{const m=JSON.parse(data);if(m.id){pending.get(m.id)?.(m);pending.delete(m.id);}else if(m.method==='Network.requestWillBeSent'&&m.params.request.url.includes('/api/v1/assessment-runs'))requests.push({method:m.params.request.method,url:m.params.request.url});};
const send=(method,params={})=>new Promise((resolve,reject)=>{const id=++seq;const t=setTimeout(()=>reject(Error(method)),20000);pending.set(id,m=>{clearTimeout(t);m.error?reject(Error(JSON.stringify(m.error))):resolve(m.result);});ws.send(JSON.stringify({id,method,params}));});
const ev=async expression=>{const r=await send('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true});if(r.exceptionDetails)throw Error(JSON.stringify(r.exceptionDetails));return r.result.value;};
const wait=async expression=>{for(let i=0;i<200;i++){if(await ev(expression))return;await new Promise(r=>setTimeout(r,100));}throw Error(`Timeout ${expression}`);};
const button= text => text==='theme' ? "document.querySelector('.focus-theme-toggle')" : `Array.from(document.querySelectorAll('button')).find(x=>x.textContent.includes(${JSON.stringify(text)})&&!x.disabled)`;
const mouse=async(text,n=1)=>{const expr=button(text);await wait(`!!(${expr})`);await new Promise(r=>setTimeout(r,400));await wait(`!!(${expr})`);const p=await ev(`(()=>{let e=(${expr});e.scrollIntoView({block:'center',behavior:'instant'});let r=e.getBoundingClientRect();return {x:r.x+r.width/2,y:r.y+r.height/2}})()`);for(let i=0;i<n;i++){for(const type of ['mousePressed','mouseReleased']){events.push({type,...p});await send('Input.dispatchMouseEvent',{type,...p,button:'left',clickCount:1});}}};
const shot=async name=>{const {data}=await send('Page.captureScreenshot',{format:'png'});await writeFile(join(out,name),Buffer.from(data,'base64'));};
const swipe=async (from,to)=>{const before=await ev('scrollY');for(const [type,y] of [['touchStart',from],['touchMove',Math.round((from+to)/2)],['touchMove',to],['touchEnd',to]]){events.push({type,x:180,y});await send('Input.dispatchTouchEvent',{type,touchPoints:type==='touchEnd'?[]:[{x:180,y}]});await new Promise(r=>setTimeout(r,100));}await new Promise(r=>setTimeout(r,350));const after=await ev('scrollY');scroll.push({before,after,from,to});};
try{
 await mkdir(out,{recursive:true});await send('Runtime.enable');await send('Page.enable');await send('Network.enable');await send('Emulation.setDeviceMetricsOverride',{width:1440,height:1000,deviceScaleFactor:1,mobile:false});
 await send('Page.navigate',{url:ui});await wait("document.body.innerText.includes('初始能力评估')");await mouse('进入初始能力评估');await mouse('开始新一轮评估',3);await wait("document.body.innerText.includes('开始 Reading')");await mouse('开始 Reading',3);await wait("document.querySelectorAll('fieldset').length===4");
 await ev("document.querySelectorAll('fieldset').forEach(f=>f.querySelector('input').click())");await mouse('提交作答',3);await wait("document.body.innerText.includes('本次答对')");await shot('pointer-result.png');
 await mouse('开始新一轮评估');await wait("document.body.innerText.includes('开始 Reading')");await mouse('开始 Reading');await wait("document.querySelectorAll('fieldset').length===4");
 const focus=[];
 const tab=async(shift=false)=>{await send('Input.dispatchKeyEvent',{type:'keyDown',key:'Tab',code:'Tab',windowsVirtualKeyCode:9,modifiers:shift?8:0});await send('Input.dispatchKeyEvent',{type:'keyUp',key:'Tab',code:'Tab',windowsVirtualKeyCode:9,modifiers:shift?8:0});};
 for(const theme of ['premium','noir']){
  if(await ev('document.documentElement.dataset.theme')!==theme)await mouse('theme');
  for(let i=0;i<45;i++) {await tab();const f=await ev(`(()=>{let e=document.activeElement,r=e.getBoundingClientRect(),s=getComputedStyle(e);return {tag:e.tagName,text:(e.textContent||'').trim().slice(0,60),outline:s.outlineStyle,outlineWidth:s.outlineWidth,shadow:s.boxShadow,rect:{top:r.top,bottom:r.bottom},visible:e.matches(':focus-visible')}})()`);focus.push({theme,...f});if(f.visible&&f.rect.top>=80&&f.rect.bottom<=1000) {await shot('focus-'+theme+'.png');break;}}
 }
 await writeFile(join(out,'focus-styles.json'),JSON.stringify(focus,null,2));
 await send('Emulation.setDeviceMetricsOverride',{width:390,height:844,deviceScaleFactor:1,mobile:true});await send('Emulation.setTouchEmulationEnabled',{enabled:true,maxTouchPoints:1});await shot('touch-before.png');for(let i=0;i<8;i++)await swipe(650,250);await shot('touch-after.png');assert.ok(scroll.some(x=>x.after>x.before));await swipe(250,650);await shot('touch-up.png');
 await writeFile(join(out,'browser-report.json'),JSON.stringify({pointer:{status:'partial',events:events.filter(x=>x.type.startsWith('mouse'))},touch:{status:'partial',events:events.filter(x=>x.type.startsWith('touch')),scroll},focus:{status:'partial',evidence:'focus-styles.json'},requests,failed:[],skipped:['full focus audit','complete touch traversal and result reading'],retested:['first run button replaced during coordinate lookup','second run theme toggle label selector failed; corrected']},null,2));
}finally{ws.close();}
