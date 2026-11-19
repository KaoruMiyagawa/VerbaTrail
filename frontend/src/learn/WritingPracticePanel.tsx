import { useEffect, useRef, useState } from 'react';
import { ApiClientError } from '../api/client';
import { writingPractice, type SubmitRevision, type WritingPractice } from '../api/writingPractice';
import { readWritingBuffer, writeWritingBuffer, writingBufferIsPersistent } from './writingRecoveryStorage';

interface Recovery {
  practiceId: string;
  body: string;
  version: number;
  pending: SubmitRevision | null;
}

function loadRecovery(key: string): Recovery | null {
  try {
    const value = JSON.parse(readWritingBuffer(key, true) ?? 'null');
    if (typeof value?.practiceId !== 'string' || typeof value.body !== 'string' ||
        !Number.isSafeInteger(value.version) || value.version < 0) return null;
    if (value.pending && (value.pending.body !== value.body || value.pending.version !== value.version ||
        typeof value.pending.idempotency_key !== 'string')) return null;
    return { ...value, pending: value.pending ?? null };
  } catch { return null; }
}

export function WritingPracticePanel({ runId, learner, submissionId, onOpenTransfer }: {
  runId: string; learner: string; submissionId: string; onOpenTransfer: () => void;
}) {
  const storageKey = `assessment:writing-practice:v1:${encodeURIComponent(learner)}:${submissionId}`;
  const [local, setLocal] = useState(() => loadRecovery(storageKey));
  const recovery = useRef(local);
  const [practice, setPractice] = useState<WritingPractice | null>(null);
  const [body, setBody] = useState(local?.body ?? '');
  const [loaded, setLoaded] = useState(false);
  const [busy, setBusy] = useState(true);
  const [storageAvailable, setStorageAvailable] = useState(() => writingBufferIsPersistent(storageKey));
  const [error, setError] = useState('');
  const gate = useRef(false);
  const mounted = useRef(false);

  function remember(next: Recovery | null) {
    recovery.current = next;
    setLocal(next);
    setStorageAvailable(writeWritingBuffer(storageKey, next ? JSON.stringify(next) : null));
  }

  function adopt(next: WritingPractice | null) {
    if (!mounted.current) return;
    setLoaded(true);
    setPractice(next);
    if (!next) return;
    const saved = recovery.current;
    const serverBody = next.revision?.body ?? next.draft.body;
    const pending = saved?.pending;
    if (saved?.practiceId === next.id &&
        (saved.body !== serverBody || (pending && !next.revision))) {
      setBody(saved.body);
      return;
    }
    setBody(serverBody);
    if (saved?.practiceId === next.id) remember(null);
  }

  async function perform(work: () => Promise<WritingPractice | null>, signal?: AbortSignal) {
    if (gate.current) return;
    gate.current = true;
    setBusy(true);
    setError('');
    try { adopt(await work()); }
    catch (cause) {
      if (!mounted.current || signal?.aborted) return;
      const conflict = cause instanceof ApiClientError && cause.status === 409;
      const invalid = cause instanceof ApiClientError && cause.status === 422;
      if (conflict || invalid) {
        const saved = recovery.current;
        if (saved) remember({ ...saved, pending: null });
      }
      setError(conflict
        ? '服务端版本已变化。请比较两份文字再决定如何处理。'
        : invalid ? '服务端无法接受此修订，请检查正文。'
          : '暂时无法确认操作结果。请重新读取，或用当前请求重试。');
      if (!signal) {
        try { adopt(await writingPractice.read(runId, learner)); } catch { /* Keep the saved recovery text. */ }
      }
    } finally {
      gate.current = false;
      if (mounted.current && !signal?.aborted) setBusy(false);
    }
  }

  useEffect(() => {
    mounted.current = true;
    const controller = new AbortController();
    queueMicrotask(() => {
      if (!controller.signal.aborted) {
        void perform(() => writingPractice.read(runId, learner, controller.signal), controller.signal);
      }
    });
    return () => { mounted.current = false; controller.abort(); };
  }, [runId, learner]);

  function edit(value: string) {
    if (!practice || practice.revision || gate.current || local?.pending) return;
    setBody(value);
    remember({
      practiceId: practice.id, body: value,
      version: local?.practiceId === practice.id ? local.version : practice.draft.version,
      pending: null,
    });
  }

  const pending = local?.practiceId === practice?.id ? local?.pending : null;
  const conflict = !!practice && local?.practiceId === practice.id &&
    local.version !== practice.draft.version && local.body !== practice.draft.body;

  function saveDraft(resolve = false) {
    if (!practice || gate.current || practice.revision || (conflict && !resolve)) return;
    if (pending && !resolve) return;
    const version = !resolve && local?.practiceId === practice.id ? local.version : practice.draft.version;
    remember({ practiceId: practice.id, body, version, pending: null });
    void perform(() => writingPractice.save(runId, learner, { body, version }));
  }

  function submit() {
    if (!practice || gate.current || practice.revision || conflict || !body.trim()) return;
    const request: SubmitRevision = pending ?? {
      body, version: practice.draft.version, idempotency_key: crypto.randomUUID(),
    };
    remember({ practiceId: practice.id, body: request.body, version: request.version, pending: request });
    void perform(() => writingPractice.submit(runId, learner, request));
  }

  return <section className="assessment-writing-practice" aria-labelledby="writing-practice-title" aria-busy={busy}>
    <div className="assessment-writing-header">
      <h3 id="writing-practice-title">针对性训练</h3>
      <button type="button" className="focus-button-quiet" disabled={busy}
        onClick={() => void perform(() => writingPractice.read(runId, learner))}>重新读取训练</button>
    </div>
    {busy && <p role="status">正在读取或保存训练…</p>}
    {error && <p role="alert" className="focus-notice warning">{error}</p>}
    {!storageAvailable && <p role="alert">浏览器无法保留本地副本。离开前请确认服务端已经保存。</p>}
    {loaded && !practice && <button type="button" className="focus-button-secondary" disabled={busy}
      onClick={() => void perform(() => writingPractice.start(runId, learner))}>生成探索性练习</button>}
    {local && (!practice || local.practiceId !== practice.id) && <details>
      <summary>查看此浏览器保留的修订</summary>
      <pre className="assessment-writing-submitted-body" lang="en">{local.body}</pre>
    </details>}
    {practice && <>
      <PracticeInstructions practice={practice} />
      {practice.revision ? <>
        <RevisionResult practice={practice} revision={practice.revision} />
        <button type="button" className="focus-button-secondary" onClick={onOpenTransfer}>进入独立迁移</button>
        {local?.practiceId === practice.id && local.body !== practice.revision.body && <details>
          <summary>查看未采用的本地文字</summary>
          <pre className="assessment-writing-submitted-body" lang="en">{local.body}</pre>
        </details>}
      </> : <form className="assessment-writing-practice-editor" onSubmit={event => { event.preventDefault(); submit(); }}>
        <label htmlFor="writing-practice-revision">修订文字</label>
        <textarea id="writing-practice-revision" className="focus-textarea" lang="en" value={body}
          maxLength={practice.max_characters} readOnly={busy || !!pending}
          onChange={event => edit(event.target.value)} />
        <p role="status">{pending ? '正式提交待确认，将沿用原提交编号。'
          : body === practice.draft.body ? `服务端草稿版本 ${practice.draft.version}` : '当前修改尚未同步。'}</p>
        {conflict && <div className="focus-notice warning" role="alert">
          <p>服务器草稿已变化。本地文字仍保留。</p>
          <details><summary>查看服务器稿</summary>
            <pre className="assessment-writing-submitted-body" lang="en">{practice.draft.body}</pre>
          </details>
          <div className="focus-actions">
            <button type="button" className="focus-button-secondary" disabled={busy} onClick={() => {
              setBody(practice.draft.body);
              remember(null);
              setError('');
            }}>采用服务器稿</button>
            <button type="button" className="focus-button-secondary" disabled={busy}
              onClick={() => saveDraft(true)}>明确保存本地稿</button>
          </div>
        </div>}
        <div className="focus-actions">
          <button type="button" className="focus-button-quiet" disabled={busy || !!pending || !!body}
            onClick={() => edit(Array.from(practice.plan.anchor.source_reference.quote).slice(0, practice.max_characters).join(''))}>以原文为起点</button>
          <button type="button" className="focus-button-secondary"
            disabled={busy || !!pending || conflict || body === practice.draft.body}
            onClick={() => saveDraft()}>保存修订草稿</button>
          <button type="submit" className="focus-button" disabled={busy || conflict || !body.trim()}>
            {pending ? '重试提交修订' : '正式提交修订'}
          </button>
        </div>
      </form>}
    </>}
  </section>;
}

function PracticeInstructions({ practice }: { practice: WritingPractice }) {
  const { plan } = practice;
  const focus = plan.hypotheses.find(item => item.id === plan.focus_hypothesis_id);
  return <>
    <div>
      <p className="focus-eyebrow">探索性练习</p>
      <h4>{plan.title}</h4>
      {focus && <p>待验证问题 · {focus.statement}</p>}
      <p>{plan.selection_reason}</p>
      <p>本次收集 · {plan.new_evidence}</p>
    </div>
    <details className="focus-disclosure">
      <summary>原题、原文及诊断来源</summary>
      <p lang="en">{plan.task_prompt}</p>
      <p>原文第 {plan.anchor.paragraph_number} 段</p>
      <pre className="assessment-writing-submitted-body" lang="en">{plan.anchor.source_reference.quote}</pre>
      {plan.diagnostic_excerpt && <p>诊断引用的片段 · <q lang="en">{plan.diagnostic_excerpt}</q></p>}
      <p>诊断 {plan.decision_id} · {plan.diagnostic_method_version}</p>
      <p>练习 {plan.version} · {plan.evidence_version}</p>
      <p>仍待独立验证的假设</p>
      <ul>{plan.hypotheses.map(hypothesis => <li key={hypothesis.id}>{hypothesis.statement}</li>)}</ul>
      {plan.unresolved_uncertainties.map((item, index) => <p key={index}>{item}</p>)}
    </details>
    <p lang="en">{plan.instructions}</p>
    <p>{plan.evaluation_boundary}</p>
  </>;
}

function RevisionResult({ practice, revision }: { practice: WritingPractice; revision: NonNullable<WritingPractice['revision']> }) {
  const { feedback } = revision;
  return <div className="assessment-writing-practice-result">
    <h4>原文与正式修订对照</h4>
    <div className="assessment-writing-practice-comparison">
      <div><strong>原文第 {practice.plan.anchor.paragraph_number} 段</strong>
        <pre className="assessment-writing-submitted-body" lang="en">{practice.plan.anchor.source_reference.quote}</pre></div>
      <div><strong>修订原文</strong>
        <pre className="assessment-writing-submitted-body" lang="en">{revision.body}</pre></div>
    </div>
    <p>{feedback.text_changed ? '两份文本存在字面差异。' : '修订与所引用的原文一致。'}</p>
    <ul>{feedback.observations.map(item => <li key={item.metric}>
      {item.metric === 'word_count' ? '词数' : 'Unicode 字符数'} · {item.original} → {item.revision}
    </li>)}</ul>
    {feedback.method === 'structured_candidate' && <details>
      <summary>候选复核意见</summary>
      {feedback.changes.map((item, index) => <p key={index}>{item.observation}</p>)}
      {feedback.hypothesis_evidence.map((link, index) => <p key={index}>
        {link.hypothesis_id} · {link.relation} · {link.comparison.observation}
      </p>)}
    </details>}
    <ul>{feedback.unresolved_uncertainties.map((item, index) => <li key={index}>{item}</li>)}</ul>
    <p>{feedback.next_step_reason}</p>
  </div>;
}
