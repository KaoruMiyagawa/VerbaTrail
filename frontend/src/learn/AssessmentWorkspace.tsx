import {useEffect, useRef, useState} from 'react';
import {assessmentApi, type AssessmentRun, type ReadingAssessment} from '../api/assessment';
import type {WorkspaceId} from '../app-shell/navigation';

const learner = 'default_learner';
const selectedKey = `assessment:selected:${learner}`;
const createKey = `assessment:create:${learner}`;
const key = () => globalThis.crypto.randomUUID();
const draftKey = (run: string, version: string) => `assessment:draft:${learner}:${run}:${version}`;
function loadDraft(run: string, version: string): Record<string,string> {
  try { return JSON.parse(localStorage.getItem(draftKey(run, version)) || '{}'); } catch { return {}; }
}

export function AssessmentWorkspace({onNavigate}: {onNavigate: (workspace: WorkspaceId) => void}) {
  const [history,setHistory] = useState<AssessmentRun[]>([]);
  const [run,setRun] = useState<AssessmentRun | null>(null);
  const [reading,setReading] = useState<ReadingAssessment | null>(null);
  const [draft,setDraft] = useState<Record<string,string>>({});
  const [busy,setBusy] = useState(false);
  const [error,setError] = useState('');
  const gate = useRef(false);
  const refreshHistory = async () => setHistory(await assessmentApi.history(learner));
  const select = async (id: string) => {
    const [next, task] = await Promise.all([assessmentApi.run(id,learner), assessmentApi.reading(id,learner)]);
    setRun(next); setReading(task); setDraft(loadDraft(id,task.task_version)); localStorage.setItem(selectedKey,id);
  };
  const action = async (fn: () => Promise<void>) => {
    if (gate.current) return; gate.current = true; setBusy(true); setError('');
    try { await fn(); } catch (e) { setError(e instanceof Error ? e.message : '请求失败，请重试。'); }
    finally {gate.current = false; setBusy(false);}
  };
  useEffect(() => { void action(async () => {await refreshHistory(); const id=localStorage.getItem(selectedKey); if(id) await select(id);}); }, []);
  const create = () => void action(async () => {
    let id = localStorage.getItem(createKey); if (!id) {id=key(); localStorage.setItem(createKey,id);}
    const created = await assessmentApi.create(learner,id); localStorage.removeItem(createKey); await refreshHistory(); await select(created.run_id);
  });
  const submit = () => void action(async () => {
    if (!run || !reading) return;
    const pending = `assessment:submit:${learner}:${run.run_id}:${reading.task_version}`;
    let id=localStorage.getItem(pending); if(!id) {id=key(); localStorage.setItem(pending,id);}
    try { const result=await assessmentApi.submit(run.run_id,learner,draft,id); setReading(result); localStorage.removeItem(pending); await refreshHistory(); }
    catch (e) { try { const result=await assessmentApi.reading(run.run_id,learner); setReading(result); if(result.task_status==='completed') {localStorage.removeItem(pending); await refreshHistory(); return;} } catch { /* Retain pending request for recovery. */ } throw e; }
  });
  const done=reading?.task_status==='completed';
  return <div className="learn-page assessment-page">
    <button type="button" className="focus-button-quiet" onClick={() => onNavigate('home')}>← 返回 Learn</button>
    <header className="focus-page-header"><p className="focus-eyebrow">INITIAL ASSESSMENT</p><h1 data-workspace-heading tabIndex={-1}>初始能力评估</h1><p>逐步收集四科的学习证据，无需一次完成。</p></header>
    {error && <div role="alert" className="focus-card">{error} <button type="button" onClick={() => void action(async()=>{await refreshHistory(); if(run) await select(run.run_id);})}>重新连接</button></div>}
    <section className="focus-card"><h2>评估记录</h2><button type="button" className="focus-button" disabled={busy} onClick={create}>开始新一轮评估</button>{history.length===0 && <p>暂无评估记录。</p>}<ul>{history.map(item=><li key={item.run_id}><button type="button" disabled={busy} onClick={()=>void action(()=>select(item.run_id))}>{new Date(item.created_at).toLocaleString()} · {item.assessment_version} · {item.tasks.find(t=>t.skill_id==='reading')?.status}</button></li>)}</ul></section>
    {run && reading && <><section className="focus-card"><h2>四科进度</h2><ul>{run.tasks.map(task=><li key={task.skill_id}><span lang="en">{task.skill_id}</span>：{task.skill_id==='reading' ? reading.task_status : '初始评估后续开放'}</li>)}</ul>{reading.task_status==='not_started' && <button className="focus-button" disabled={busy} onClick={()=>void action(async()=>{const r=await assessmentApi.start(run.run_id,learner);setReading(r);setDraft(loadDraft(run.run_id,r.task_version));await refreshHistory();})}>开始 Reading</button>}</section>
    {reading.passage && <section className="focus-card"><h2>Reading 初始任务</h2><p>任务版本：{reading.task_version}</p>{reading.repeated_exposure && <p>重复曝光：你之前接触过相同材料，结果不能作为全新独立测量。</p>}{reading.diagnostic_quality==='legacy_answer_position_biased' && <p>旧版题目设计存在答案位置偏差，请谨慎解读。</p>}<article lang="en" style={{whiteSpace:'pre-wrap',lineHeight:1.8,maxWidth:'75ch'}}>{reading.passage}</article><p>{done ? `本次答对 ${reading.correct_count} / ${reading.questions.length} 题` : '你的选择作为本地草稿保存在此浏览器。'}</p>
    {reading.questions.map((q,i)=><fieldset key={q.question_id} disabled={busy || done} style={{margin:'1.5rem 0',minWidth:0}}><legend lang="en">{i+1}. {q.prompt}</legend>{q.options.map(option=><label key={option} style={{display:'flex',gap:8,margin:'0.7rem 0'}}><input type="radio" name={q.question_id} checked={(done?q.answer:draft[q.question_id])===option} onChange={()=>{const next={...draft,[q.question_id]:option};setDraft(next);localStorage.setItem(draftKey(run.run_id,reading.task_version),JSON.stringify(next));}}/><span lang="en">{option}</span></label>)}{done && <div><p>你的答案：{q.answer} · {q.is_correct?'正确':'错误'}</p><p>正确答案：{q.correct_answer}</p><p>{q.explanation}</p><p>证据有效性：{q.evidence_validity}</p></div>}</fieldset>)}
    {!done && <button className="focus-button" disabled={busy || reading.questions.some(q=>!draft[q.question_id])} onClick={submit}>提交作答</button>}{reading.evidence_insufficient && <p>证据不足：短测评只能观察有限范围的阅读表现。</p>}{reading.limitations.map(x=><p key={x}>{x}</p>)}</section>}</>}
  </div>;
}
