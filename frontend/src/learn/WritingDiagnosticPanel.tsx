import { useEffect, useRef, useState } from 'react';
import {
  advanceWritingDiagnostic, readWritingDiagnostic,
  type WritingDiagnosticDecision, type WritingDiagnosticIntent, type WritingDiagnosticSession,
} from '../api/writingDiagnostic';
import { ApiClientError } from '../api/client';
import { WritingPracticePanel } from './WritingPracticePanel';
import { readWritingBuffer, writeWritingBuffer, writingBufferIsPersistent } from './writingRecoveryStorage';

const statuses = {
  candidate: '待验证', supported: '暂获支持', lowered: '已削弱',
  ruled_out: '本轮暂排除', insufficient_evidence: '证据不足',
};
const relations = { supports: '支持', contradicts: '反对', neutral: '中性' };
const aspects = { position: '立场', reasons: '理由', reasoning_link: '论证联系' };
const fallbackReasons: Record<string, string> = {
  remote_disabled: '服务端未开启模型调用，本轮使用本地规则。',
  model_not_requested: '本轮选择了本地规则。',
  model_unavailable: '模型未配置或不可用，本轮使用本地规则。',
  budget_exhausted: '剩余模型预算不足，本轮使用本地规则。',
  model_error: '模型请求失败，本轮使用本地规则。',
  invalid_model_output: '模型提议未通过校验，本轮使用本地规则。',
  interrupted: '上次处理已中断。本轮从已保存的请求恢复，并使用本地规则。',
};

function readIntent(key: string): WritingDiagnosticIntent | null {
  try {
    const value = JSON.parse(readWritingBuffer(key) ?? 'null');
    const payload = value?.payload;
    if (typeof payload?.idempotency_key !== 'string' || typeof payload.allow_model !== 'boolean') return null;
    if (value.kind === 'start') return value;
    if (value.kind === 'answer' && typeof payload.body === 'string' && typeof payload.question_id === 'string') return value;
  } catch { /* A server reservation can still be restored. */ }
  return null;
}

function savedIntent(decision: WritingDiagnosticDecision): WritingDiagnosticIntent {
  const payload = {
    idempotency_key: decision.idempotency_key, allow_model: decision.allow_model, recover_decision_id: decision.id,
  };
  return decision.answer ? {
    kind: 'answer', payload: { ...payload, question_id: decision.answer.question_id, body: decision.answer.body },
  } : { kind: 'start', payload };
}

interface DiagnosticProps {
  runId: string; learner: string; submissionId: string; onOpenTransfer: () => void;
}

export function WritingDiagnosticPanel(props: DiagnosticProps) {
  const [open, setOpen] = useState(false);
  return <section className="focus-card assessment-writing-diagnostic">
    <h2>Writing 诊断与追问</h2>
    <p>原文引用可复核。诊断假设供追问和修订参考，当前没有已验证的能力结论。</p>
    {open ? <DiagnosticSession {...props} />
      : <button type="button" className="focus-button-secondary" onClick={() => setOpen(true)}>打开作文诊断</button>}
  </section>;
}

function DiagnosticSession({ runId, learner, submissionId, onOpenTransfer }: DiagnosticProps) {
  const storageKey = `assessment:writing-diagnostic:v1:${encodeURIComponent(learner)}:${submissionId}`;
  const draftKey = `${storageKey}:answer`;
  const [intent, setIntent] = useState(() => readIntent(storageKey));
  const [draft, setDraft] = useState(() => intent?.kind === 'answer' ? intent.payload.body : readWritingBuffer(draftKey) ?? '');
  const [session, setSession] = useState<WritingDiagnosticSession | null>(null);
  const [consent, setConsent] = useState(false);
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState('');
  const [storageAvailable, setStorageAvailable] = useState(
    () => writingBufferIsPersistent(storageKey) && writingBufferIsPersistent(draftKey),
  );
  const gate = useRef(false);
  const mounted = useRef(false);
  const pending = useRef(intent);

  function remember(next: WritingDiagnosticIntent | null) {
    pending.current = next;
    setIntent(next);
    if (!writeWritingBuffer(storageKey, next ? JSON.stringify(next) : null)) setStorageAvailable(false);
  }

  function editAnswer(body: string) {
    setDraft(body);
    if (!writeWritingBuffer(draftKey, body || null)) setStorageAvailable(false);
  }

  function adopt(next: WritingDiagnosticSession) {
    if (!mounted.current) return;
    setSession(next);
    if (next.followup?.answer?.body === readWritingBuffer(draftKey)) {
      writeWritingBuffer(draftKey, null);
    }
    const current = pending.current;
    if (!current) return;
    const decision = current.kind === 'start' ? next.initial : next.followup;
    if (decision?.idempotency_key === current.payload.idempotency_key) {
      remember(null);
      setError('');
      return;
    }
    // A competing tab won. Keep any different answer available to copy.
    if (decision) {
      if (current.kind === 'answer') editAnswer(current.payload.body);
      remember(null);
      setError('服务器已有另一条诊断请求。此处的不同回答仍可复制。');
    }
  }

  async function read(signal?: AbortSignal, background = false) {
    if (gate.current) return;
    gate.current = true;
    if (!background) { setBusy(true); setError(''); }
    try {
      const next = await readWritingDiagnostic(runId, learner, signal);
      adopt(next);
      const expired = next.followup?.recoverable ? next.followup : next.initial?.recoverable ? next.initial : null;
      if (expired && mounted.current && !signal?.aborted) {
        // Replay the server's existing reservation. The expired path settles locally.
        adopt(await advanceWritingDiagnostic(runId, learner, savedIntent(expired)));
      }
    }
    catch (cause) {
      if (mounted.current && !signal?.aborted && !background) setError(message(cause));
    } finally {
      gate.current = false;
      if (mounted.current && !signal?.aborted && !background) setBusy(false);
    }
  }

  useEffect(() => {
    mounted.current = true;
    const controller = new AbortController();
    queueMicrotask(() => { if (!controller.signal.aborted) void read(controller.signal); });
    return () => { mounted.current = false; controller.abort(); };
  }, []);

  useEffect(() => {
    if (session?.state !== 'processing') return;
    const timer = window.setInterval(() => void read(undefined, true), 2500);
    return () => window.clearInterval(timer);
  }, [session?.state]);

  async function send(next: WritingDiagnosticIntent) {
    if (gate.current) return;
    gate.current = true;
    remember(next);
    setBusy(true);
    setError('');
    try {
      adopt(await advanceWritingDiagnostic(runId, learner, next));
      if (mounted.current) setConsent(false);
    } catch (cause) {
      if (!mounted.current) return;
      setError(message(cause));
      // A transport error may have happened after the reservation committed.
      if (cause instanceof ApiClientError && [409, 422].includes(cause.status)) remember(null);
      try { adopt(await readWritingDiagnostic(runId, learner)); } catch { /* Keep the original error. */ }
    } finally {
      gate.current = false;
      if (mounted.current) setBusy(false);
    }
  }

  function begin() {
    void send(intent?.kind === 'start' ? intent : {
      kind: 'start', payload: { idempotency_key: crypto.randomUUID(), allow_model: consent && !!session?.model_available },
    });
  }

  function answer(questionId: string) {
    void send(intent?.kind === 'answer' ? intent : {
      kind: 'answer', payload: {
        question_id: questionId, body: draft, idempotency_key: crypto.randomUUID(),
        allow_model: consent && !!session?.model_available,
      },
    });
  }

  const initial = session?.initial;
  const review = session?.followup;
  const processing = review?.state === 'processing' ? review : initial?.state === 'processing' ? initial : null;
  const question = initial?.outcome?.followup;
  const canAct = session?.state === 'not_started' || session?.state === 'awaiting_answer';
  const answerId = review?.answer?.evidence_id;
  const citation = (id: string) => {
    if (id === answerId) return '追问回答';
    const paragraph = session?.evidence.find(item => item.id === id);
    return paragraph ? `原文第 ${paragraph.paragraph_number} 段` : id;
  };
  const cite = (ids: string[]) => ids.length ? ids.map(citation).join('、') : '暂无可引用证据';

  return <div className="assessment-writing-diagnostic-content" aria-busy={busy}>
    <button type="button" className="focus-button-quiet" disabled={busy} onClick={() => void read()}>重新读取诊断状态</button>
    {busy && <p role="status">正在读取或保存诊断…</p>}
    {error && <p className="focus-notice warning" role="alert">{error}</p>}
    {!storageAvailable && <p role="alert">浏览器未能持久保存恢复副本。刷新或关闭页面前请先复制未提交的回答。</p>}
    {session && <>
      <p>模型预算占用 · {session.tokens_charged} / {session.token_budget} tokens
        · 模型已报告 {session.tokens_reported}
        {session.tokens_pending_reservation > 0 && ` · 待确认预留 ${session.tokens_pending_reservation}`}
      </p>
      {!session.model_available && <p>模型调用未开启或模型不可用，可使用本地追问。</p>}
      {canAct && session.model_available && !intent && <label className="assessment-writing-model-consent">
        <input type="checkbox" checked={consent} onChange={event => setConsent(event.target.checked)} />
        允许向已配置模型发送本次正式作文{session.state === 'awaiting_answer' ? '及本次追问回答' : ''}，生成候选诊断
      </label>}
      {session.state === 'not_started' && <button type="button" className="focus-button" disabled={busy} onClick={begin}>
        {intent ? '重试开始诊断' : '开始诊断'}
      </button>}
      {processing && <div className="focus-notice info" role="status">
        <p>请求已保存，诊断正在处理。页面会重新读取状态。</p>
        {processing.recoverable && <button type="button" className="focus-button-secondary"
          disabled={busy} onClick={() => void send(savedIntent(processing))}>恢复中断的诊断</button>}
      </div>}
      {initial?.outcome && <DecisionDetails decision={initial} cite={cite} />}
      {question && initial && <section className="assessment-writing-followup">
        <h3>区分性追问</h3>
        <p lang="en">{question.question}</p>
        <p>{question.discriminating_reason}</p>
        <p>原文依据 · {cite(question.evidence_ids)}</p>
        <details className="focus-disclosure">
          <summary>展开这道追问所引用的原文</summary>
          {session.evidence.filter(item => question.evidence_ids.includes(item.id)).map(item =>
            <div className="assessment-writing-observation" key={item.id}>
              <strong>第 {item.paragraph_number} 段</strong>
              <blockquote lang="en">{item.source_reference.quote}</blockquote>
            </div>)}
        </details>
        <ul>{initial.outcome?.hypotheses.filter(h => question.hypothesis_ids.includes(h.id)).map(h =>
          <li key={h.id}>{h.statement}</li>)}</ul>
        {session.state === 'awaiting_answer' && <form onSubmit={event => { event.preventDefault(); answer(initial.id); }}>
          <label htmlFor="writing-diagnostic-answer">你的回答</label>
          <textarea id="writing-diagnostic-answer" className="focus-textarea" lang="en" value={draft}
            maxLength={session.max_answer_characters} readOnly={busy || !!intent}
            onChange={event => { if (!gate.current && !pending.current) editAnswer(event.target.value); }} />
          <button type="submit" className="focus-button" disabled={busy || (!intent && !draft.trim())}>
            {intent ? '重试保存回答' : '保存回答并更新假设'}
          </button>
        </form>}
      </section>}
      {review?.answer && <section>
        <h3>已保存的回答原文</h3>
        <pre className="assessment-writing-submitted-body" lang="en">{review.answer.body}</pre>
        {draft && draft !== review.answer.body && <details>
          <summary>查看此标签页未采用的回答</summary>
          <pre className="assessment-writing-submitted-body" lang="en">{draft}</pre>
        </details>}
      </section>}
      {review?.outcome && <DecisionDetails decision={review} cite={cite} />}
      {session.state === 'stopped' && <p role="status">本次诊断已停止，后续仍需独立作答验证。</p>}
      {initial?.outcome && <details className="focus-disclosure">
        <summary>查看服务端原文引用</summary>
        {session.evidence.map(item => <div className="assessment-writing-observation" key={item.id}>
          <strong>原文第 {item.paragraph_number} 段</strong>
          <blockquote lang="en">{item.source_reference.quote}</blockquote>
          <small>UTF-16 · {item.source_reference.start}–{item.source_reference.end}</small>
        </div>)}
      </details>}
      {session.state === 'stopped' && <WritingPracticePanel runId={runId} learner={learner} submissionId={submissionId}
        onOpenTransfer={onOpenTransfer} />}
    </>}
  </div>;
}

function message(cause: unknown) {
  return cause instanceof ApiClientError && cause.retryable
    ? '暂时无法确认诊断状态。可以重新读取或用原请求重试。'
    : cause instanceof Error ? cause.message : '暂时无法读取诊断，请重试。';
}

function DecisionDetails({ decision, cite }: {
  decision: WritingDiagnosticDecision; cite: (ids: string[]) => string;
}) {
  const outcome = decision.outcome;
  if (!outcome) return null;
  return <section className="assessment-writing-decision">
    <h3>{decision.phase === 'initial' ? '初始候选假设' : '追问后的假设更新'}</h3>
    <p>{outcome.method === 'structured_model' ? 'AI 候选解释' : '本地规则候选'} · 证据不足</p>
    {outcome.method === 'structured_model' && <p>引文可与原文核对，支持与反对关系仍是待复核的模型解释。</p>}
    {outcome.fallback_reason && <p className="focus-notice neutral">{fallbackReasons[outcome.fallback_reason] ?? '已使用本地规则。'}</p>}
    {decision.model_call && <p>模型调用记录 · {decision.model_call.provider_name} / {decision.model_call.model_name} · {decision.model_call.status}
      {outcome.fallback_reason === 'interrupted' && ' · 结果在本地收尾后返回'}
    </p>}
    {!decision.model_call && decision.planned_model && <p>原定模型 · {decision.planned_model.provider_name} / {decision.planned_model.model_name} · 调用结果未确认</p>}
    <p>{outcome.decision_reason}</p>
    {!!outcome.argument_analysis.length && <dl>{outcome.argument_analysis.map(item => <div key={item.aspect}>
      <dt>{aspects[item.aspect]} · {item.assessment === 'insufficient_evidence' ? '证据不足' : '候选解释'}</dt>
      <dd>{item.interpretation} <small>· {cite(item.evidence_ids)}</small>
        {item.excerpt && <blockquote>{item.excerpt}</blockquote>}
      </dd>
    </div>)}</dl>}
    {outcome.hypotheses.map(hypothesis => <article className="assessment-writing-observation" key={hypothesis.id}>
      <strong>{statuses[hypothesis.status]}</strong>
      <p>{hypothesis.statement}</p>
      <p>{hypothesis.unresolved_question}</p>
      {hypothesis.evidence_links.length ? <ul>{hypothesis.evidence_links.map(link => <li key={link.evidence_id}>
        {relations[link.relation]} · {cite([link.evidence_id])} · {link.reason}
        {link.excerpt && <blockquote>{link.excerpt}</blockquote>}
      </li>)}</ul> : <p>暂未关联证据。</p>}
    </article>)}
    <ul>{outcome.unresolved_uncertainties.map((item, index) => <li key={index}>{item}</li>)}</ul>
  </section>;
}
