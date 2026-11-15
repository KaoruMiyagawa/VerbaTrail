import assert from 'node:assert/strict';
import {mkdir,writeFile} from 'node:fs/promises';
import {join} from 'node:path';
const {SMOKE_UI_URL:ui,SMOKE_CDP_URL:cdp,SMOKE_OUTPUT_DIR:out}=process.env;
const target=(await (await fetch(`${cdp}/json/list`)).json()).find(x=>x.type==='page');
const ws=new WebSocket(target.webSocketDebuggerUrl); await new Promise((resolve,reject)=>{ws.onopen=resolve;ws.onerror=reject;});
let seq=0;const pending=new Map();
ws.onmessage=({data})=>{const m=JSON.parse(data);if(m.id){pending.get(m.id)?.(m);pending.delete(m.id);}};
const send=(method,params={})=>new Promise((resolve,reject)=>{const id=++seq;const timer=setTimeout(()=>reject(Error(method)),20000);pending.set(id,m=>{clearTimeout(timer);m.error?reject(Error(JSON.stringify(m.error))):resolve(m.result);});ws.send(JSON.stringify({id,method,params}));});
const ev=async expression=>{const r=await send('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true});if(r.exceptionDetails)throw Error(JSON.stringify(r.exceptionDetails));return r.result.value;};
const wait=async expression=>{for(let i=0;i<150;i++){if(await ev(expression))return;await new Promise(r=>setTimeout(r,150));}throw Error(`Timeout: ${expression}`);};
const click=async text=>{await wait(`Array.from(document.querySelectorAll('button')).some(x=>x.textContent.includes(${JSON.stringify(text)})&&!x.disabled)`);await ev(`Array.from(document.querySelectorAll('button')).find(x=>x.textContent.includes(${JSON.stringify(text)})&&!x.disabled).click()`);};
const screenshot=async name=>{const {data}=await send('Page.captureScreenshot',{format:'png',captureBeyondViewport:false});await writeFile(join(out,name),Buffer.from(data,'base64'));};
const checks=[];
try{
 await mkdir(out,{recursive:true});await send('Runtime.enable');await send('Page.enable');
 await send('Page.navigate',{url:ui});await wait("document.body.innerText.includes('初始能力评估')");await click('进入初始能力评估');
 await wait("location.pathname === '/learn/assessment' && document.body.innerText.includes('暂无评估记录')");await click('开始新一轮评估');
 await wait("document.body.innerText.includes('四科进度')");assert.equal(await ev("(document.body.innerText.match(/初始评估后续开放/g)||[]).length"),3);await click('开始 Reading');
 await wait("document.querySelectorAll('fieldset').length===4");assert.equal(await ev("Array.from(document.querySelectorAll('button')).find(x=>x.textContent.includes('提交作答')).disabled"),true);
 for(const [theme,width,height] of [['premium',1440,1000],['noir',1440,1000],['premium',390,844],['noir',390,844]]){
  if(await ev('document.documentElement.dataset.theme')!==theme)await ev("document.querySelector('.focus-theme-toggle').click()");
  await wait(`document.documentElement.dataset.theme === '${theme}'`);await send('Emulation.setDeviceMetricsOverride',{width,height,deviceScaleFactor:1,mobile:width<500});
  await wait(`innerWidth===${width}`);assert.ok(await ev(`document.documentElement.scrollWidth<=${width}`),`overflow ${theme} ${width}`);await screenshot(`assessment-${theme}-${width}.png`);
 }
 await ev("document.querySelectorAll('fieldset').forEach(f=>f.querySelector('input').click())");await click('提交作答');
 await wait("document.body.innerText.includes('本次答对 1 / 4 题')");assert.ok(await ev("document.body.innerText.includes('正确答案：') && document.body.innerText.includes('证据有效性：')"));await screenshot('result.png');
 await send('Page.reload');await wait("document.body.innerText.includes('本次答对 1 / 4 题')");await screenshot('restored.png');
 await click('返回 Learn');await click('进入初始能力评估');await wait("document.body.innerText.includes('本次答对 1 / 4 题')");
 await click('开始新一轮评估');await wait("document.body.innerText.includes('开始 Reading')");await click('开始 Reading');await wait("document.body.innerText.includes('重复曝光：')");
 checks.push('home entry','four skill statuses','v2 reading','incomplete submit disabled','four answers and real score','result details','reload and return restore','new run repeated exposure','four viewport screenshots');
 await writeFile(join(out,'browser-report.json'),JSON.stringify({passed:checks,failed:[],skipped:['network interruption','v1 browser history','keyboard and touch','four skill regression'],retested:[]},null,2));
}finally{ws.close();}
