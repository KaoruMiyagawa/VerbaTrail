import {useEffect,useRef,useState} from 'react';
import {assessmentApi,type AssessmentRun,type ReadingAssessment,type ListeningAssessment} from '../api/assessment';
import {ApiClientError,getApiBaseUrl} from '../api/client';
import type {WorkspaceId} from '../app-shell/navigation';
import {AssessmentSpeakingTask} from './AssessmentSpeakingTask';
import {AssessmentWritingTask} from './AssessmentWritingTask';
import {draftKey,loadDraft,submitWithRecovery,type AssessmentSkill} from './assessmentState';
const learner='default_learner',selectedKey=`assessment:selected:${learner}`,createKey=`assessment:create:${learner}`;
const skillKey=`assessment:skill:${learner}`;
const skillLabels={reading:'Reading',listening:'Listening',speaking:'Speaking',writing:'Writing'};
type AvailableSkill=keyof typeof skillLabels;
function isAvailableSkill(skill:string|null):skill is AvailableSkill {
 return skill==='reading'||skill==='listening'||skill==='speaking'||skill==='writing';
}
const taskStatus=(skill:string,status:string)=>skill==='speaking'&&status==='completed'?'任务已登记 · 口语能力未评分':skill==='writing'&&status==='completed'?'已提交 · 写作能力未评估':status;
const key=()=>crypto.randomUUID();
const message=(e:unknown)=>e instanceof ApiClientError && e.status===409?'此历史任务无法按已固定版本恢复，请开始新的评估。'+e.message:e instanceof Error?e.message:'请求失败，请重试。';
type Task=ReadingAssessment|ListeningAssessment;
function SkillTask({runId,skill,visible,onChanged}:{runId:string;skill:AssessmentSkill;visible:boolean;onChanged:(id:string)=>Promise<void>}) {
 const [task,setTask]=useState<Task|null>(null),[draft,setDraft]=useState<Record<string,string>>({}),[busy,setBusy]=useState(false),[error,setError]=useState(''),[audioError,setAudioError]=useState('');
 const alive=useRef(true),gate=useRef(false),audio=useRef<HTMLAudioElement>(null);
 const read=()=>skill==='reading'?assessmentApi.reading(runId,learner):assessmentApi.listening(runId,learner);
 const accept=(next:Task)=>{if(!alive.current)return;setTask(next);setDraft(next.task_version?loadDraft(localStorage,learner,runId,skill,next.task_version):{});};
 const action=async(fn:()=>Promise<void>)=>{if(gate.current)return;gate.current=true;setBusy(true);setError('');try{await fn();}catch(e){if(alive.current)setError(message(e));}finally{gate.current=false;if(alive.current)setBusy(false);}};
 useEffect(()=>{alive.current=true;void action(async()=>accept(await read()));return()=>{alive.current=false;};},[]);
 useEffect(()=>{if(!visible)audio.current?.pause();},[visible]);
 const start=()=>void action(async()=>{accept(await(skill==='reading'?assessmentApi.start(runId,learner):assessmentApi.startListening(runId,learner)));await onChanged(runId);});
 const submit=()=>void action(async()=>{if(!task?.task_version)return;const pending=`assessment:submit:${learner}:${runId}:${skill}:${task.task_version}`;const prior=localStorage.getItem(pending),id=prior||key();localStorage.setItem(pending,id);const next=await submitWithRecovery<Task>(read,()=>skill==='reading'?assessmentApi.submit(runId,learner,draft,id):assessmentApi.submitListening(runId,learner,draft,id),!!prior);accept(next);if(next.task_status==='completed')localStorage.removeItem(pending);await onChanged(runId);});
 const done=task?.task_status==='completed',label=skill==='reading'?'Reading':'Listening';
 return <div hidden={!visible} data-assessment-skill={skill}>
 {busy && <p role="status">正在读取或保存 {label}…</p>}
 {error && <div role="alert" className="focus-card">{error} <button disabled={busy} onClick={()=>void action(async()=>{accept(await read());await onChanged(runId);})}>重新连接 {label}</button></div>}
 {task?.task_status==='not_started' && <button className="focus-button" disabled={busy} onClick={start}>开始 {label}</button>}
 {task && task.task_status!=='not_started' && <section className="focus-card"><h2>{label} 初始任务</h2><p>任务版本：{task.task_version}</p>
 {task.repeated_exposure && <p>重复曝光：你之前接触过相同材料，结果不能作为全新独立测量。</p>}
 {'diagnostic_quality' in task && task.diagnostic_quality==='legacy_answer_position_biased' && <p>旧版题目设计存在答案位置偏差，请谨慎解读。</p>}
 {'passage' in task && <article lang="en" style={{whiteSpace:'pre-wrap',lineHeight:1.8,maxWidth:'75ch'}}>{task.passage}</article>}
 {'audio_url' in task && task.audio_url && <><audio ref={audio} controls preload="metadata" aria-label="Listening 初始评估音频" style={{width:'100%',maxWidth:600}} src={new URL(task.audio_url,getApiBaseUrl()+'/').href} onError={()=>setAudioError('音频无法加载，请检查连接后重试。')} onCanPlay={()=>setAudioError('')}/>{audioError && <p role="alert">{audioError} <button onClick={()=>audio.current?.load()}>重新加载音频</button></p>}</>}
 <p>{done?`本次答对 ${task.correct_count} / ${task.questions.length} 题`:'你的选择作为本地草稿保存在此浏览器。'}</p>
 {task.questions.map((q,i)=><fieldset key={q.question_id} disabled={busy||done} style={{margin:'1.5rem 0',minWidth:0}}><legend lang="en">{i+1}. {q.prompt}</legend>{q.options.map(option=><label key={option} style={{display:'flex',gap:8,margin:'0.7rem 0'}}><input type="radio" name={q.question_id} checked={(done?q.answer:draft[q.question_id])===option} onChange={()=>{if(!task.task_version)return;const next={...draft,[q.question_id]:option};setDraft(next);localStorage.setItem(draftKey(learner,runId,skill,task.task_version),JSON.stringify(next));}}/><span lang="en">{option}</span></label>)}{done && <div><p>你的答案：{q.answer} · {q.is_correct?'正确':'错误'}</p><p>正确答案：{q.correct_answer}</p><p>{q.explanation}</p><p>证据有效性：{q.evidence_validity}</p></div>}</fieldset>)}
 {!done && <button className="focus-button" disabled={busy||task.questions.some(q=>!q.options.includes(draft[q.question_id]))} onClick={submit}>提交作答</button>}
 {task.evidence_insufficient && <p>证据不足：短测评只能观察有限范围的{skill==='reading'?'阅读':'听力'}表现。</p>}{task.limitations.map(x=><p key={x}>{x}</p>)}</section>}
 </div>;
}
export function AssessmentWorkspace({onNavigate}:{onNavigate:(workspace:WorkspaceId)=>void}) {
 const [history,setHistory]=useState<AssessmentRun[]>([]),[run,setRun]=useState<AssessmentRun|null>(null),[skill,setSkill]=useState<AvailableSkill>(()=>{const saved=localStorage.getItem(skillKey);return isAvailableSkill(saved)?saved:'reading';}),[busy,setBusy]=useState(false),[error,setError]=useState('');
 const gate=useRef(false),alive=useRef(true),historyRead=useRef(0);
 const refreshHistory=async()=>{const request=++historyRead.current;const items=await assessmentApi.history(learner);if(alive.current&&request===historyRead.current)setHistory(items);};
 const changed=async(id:string)=>{const next=await assessmentApi.run(id,learner);if(alive.current)setRun(current=>current?.run_id===id&&Date.parse(next.updated_at)>=Date.parse(current.updated_at)?next:current);await refreshHistory();};
 const select=async(id:string)=>{const next=await assessmentApi.run(id,learner);if(!alive.current)return;setRun(next);localStorage.setItem(selectedKey,id);};
 const action=async(fn:()=>Promise<void>)=>{if(gate.current)return;gate.current=true;setBusy(true);setError('');try{await fn();}catch(e){if(alive.current)setError(message(e));}finally{gate.current=false;if(alive.current)setBusy(false);}};
 useEffect(()=>{alive.current=true;void action(async()=>{await refreshHistory();const id=localStorage.getItem(selectedKey);if(id)await select(id);});return()=>{alive.current=false;};},[]);
 const create=()=>void action(async()=>{let id=localStorage.getItem(createKey);if(!id){id=key();localStorage.setItem(createKey,id);}const created=await assessmentApi.create(learner,id);localStorage.removeItem(createKey);await refreshHistory();await select(created.run_id);});
 return <div className="learn-page assessment-page"><button className="focus-button-quiet" onClick={()=>onNavigate('home')}>← 返回 Learn</button><header className="focus-page-header"><p className="focus-eyebrow">INITIAL ASSESSMENT</p><h1 data-workspace-heading tabIndex={-1}>初始能力评估</h1><p>逐步收集四科的学习证据，无需一次完成。</p></header>
 {error && <div role="alert" className="focus-card">{error} <button disabled={busy} onClick={()=>void action(async()=>{await refreshHistory();const id=localStorage.getItem(selectedKey);if(id)await select(id);})}>重新连接</button></div>}
 <section className="focus-card"><h2>评估记录</h2><button className="focus-button" disabled={busy} onClick={create}>开始新一轮评估</button>{history.length===0 && <p>暂无评估记录。</p>}<ul>{history.map(item=><li key={item.run_id}><button disabled={busy} aria-pressed={item.run_id===run?.run_id} onClick={()=>void action(()=>select(item.run_id))}>{new Date(item.created_at).toLocaleString()} · {item.assessment_version} · {item.tasks.filter(t=>isAvailableSkill(t.skill_id)).map(t=>`${t.skill_id}: ${taskStatus(t.skill_id,t.status)}`).join(' / ')}</button></li>)}</ul></section>
 {run && <>
  <section className="focus-card"><h2>四科进度</h2><ul>{run.tasks.map(({skill_id,status})=><li key={skill_id}>{isAvailableSkill(skill_id)?
   <button aria-pressed={skill===skill_id} onClick={()=>{setSkill(skill_id);localStorage.setItem(skillKey,skill_id);}}>{skillLabels[skill_id]}：{taskStatus(skill_id,status)}</button>:
   <span>{skill_id}：初始评估后续开放</span>}</li>)}</ul></section>
  {(['reading','listening'] as const).map(s=><SkillTask key={`${run.run_id}:${s}`} runId={run.run_id} skill={s} visible={skill===s} onChanged={changed}/>)}
  {skill==='speaking' && <AssessmentSpeakingTask key={run.run_id} runId={run.run_id} learner={learner} onChanged={changed}/>}
  <AssessmentWritingTask key={run.run_id} runId={run.run_id} learner={learner} visible={skill==='writing'} onChanged={changed}/>
 </>}
 </div>;
}
