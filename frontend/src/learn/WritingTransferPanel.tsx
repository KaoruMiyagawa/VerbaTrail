import { useEffect, useRef, useState } from 'react';
import { ApiClientError } from '../api/client';
import { writingTransfer, type WritingTransfer } from '../api/writingTransfer';
import { readWritingBuffer, writeWritingBuffer, writingBufferIsPersistent } from './writingRecoveryStorage';

interface LocalDraft {
  assignmentId: string;
  body: string;
  version: number;
  submitKey: string | null;
}

const targetLabels = { position: '观点表达', reasons: '理由展开', reasoning_link: '论证联系' };

function readLocalDraft(key: string): LocalDraft | null {
  try {
    const value = JSON.parse(readWritingBuffer(key) ?? 'null');
    return typeof value?.assignmentId === 'string' && typeof value.body === 'string'
      && Number.isSafeInteger(value.version) && value.version >= 0
      && (value.submitKey === null || typeof value.submitKey === 'string') ? value : null;
  } catch { return null; }
}

export function WritingTransferPanel({ runId, learner, onBack }: {
  runId: string; learner: string; onBack: () => void;
}) {
  const storageKey = `assessment:writing-transfer:v1:${encodeURIComponent(learner)}:${encodeURIComponent(runId)}`;
  const [local, setLocal] = useState(() => readLocalDraft(storageKey));
  const cached = useRef(local);
  const [assignment, setAssignment] = useState<WritingTransfer | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState('');
  const [storageAvailable, setStorageAvailable] = useState(() => writingBufferIsPersistent(storageKey));
  const gate = useRef(false);
  const mounted = useRef(false);
  const heading = useRef<HTMLHeadingElement>(null);

  function remember(draft: LocalDraft | null) {
    cached.current = draft;
    setLocal(draft);
    setStorageAvailable(writeWritingBuffer(storageKey, draft ? JSON.stringify(draft) : null));
  }

  function adopt(next: WritingTransfer | null) {
    if (!mounted.current) return;
    setAssignment(next);
    setLoaded(true);
    const saved = cached.current;
    if (!next || !saved || saved.assignmentId !== next.id) return;
    if (next.response ? next.response.body === saved.body : !saved.submitKey && next.draft?.body === saved.body) {
      remember(null);
    }
  }

  async function perform(
    work: () => Promise<WritingTransfer | null>, { signal, write = false }: { signal?: AbortSignal; write?: boolean } = {},
  ) {
    if (gate.current) return;
    gate.current = true;
    setBusy(true);
    setError('');
    try {
      adopt(await work());
    } catch (cause) {
      if (!mounted.current || signal?.aborted) return;
      const status = cause instanceof ApiClientError ? cause.status : undefined;
      if (write && (status === 409 || status === 422) && cached.current) {
        remember({ ...cached.current, submitKey: null });
      }
      setError(cause instanceof ApiClientError && !cause.retryable
        ? cause.message : '暂时无法确认服务器状态。可以重新读取或用原提交编号重试。');
      if (write) {
        try {
          const next = await writingTransfer.read(runId, learner);
          const saved = cached.current;
          adopt(next);
          if (mounted.current && next?.response && saved?.assignmentId === next.id && next.response.body === saved.body) {
            setError('');
          }
        } catch { /* Keep the recovery text and the original error. */ }
      }
    } finally {
      gate.current = false;
      if (mounted.current && !signal?.aborted) setBusy(false);
    }
  }

  useEffect(() => {
    mounted.current = true;
    heading.current?.focus();
    const controller = new AbortController();
    queueMicrotask(() => {
      if (!controller.signal.aborted) {
        void perform(() => writingTransfer.read(runId, learner, controller.signal), { signal: controller.signal });
      }
    });
    return () => { mounted.current = false; controller.abort(); };
  }, [runId, learner]);

  const draft = assignment?.draft;
  const current = local && local.assignmentId === assignment?.id ? local : null;
  const body = current?.body ?? draft?.body ?? '';
  const pending = !!current?.submitKey;
  const conflict = !!draft && !!current && current.version !== draft.version && current.body !== draft.body;

  function save(useLatest = false) {
    if (!assignment || !draft || gate.current || (!useLatest && (pending || conflict))) return;
    const version = useLatest ? draft.version : current?.version ?? draft.version;
    remember({ assignmentId: assignment.id, body, version, submitKey: null });
    void perform(() => writingTransfer.save(runId, learner, { body, version }), { write: true });
  }

  function submit() {
    if (!assignment || !draft || gate.current || conflict || !body.trim()) return;
    const request = {
      body, version: pending ? current!.version : draft.version,
      idempotency_key: current?.submitKey ?? crypto.randomUUID(),
    };
    remember({ assignmentId: assignment.id, body, version: request.version, submitKey: request.idempotency_key });
    void perform(() => writingTransfer.submit(runId, learner, request), { write: true });
  }

  return <section className="focus-card assessment-writing-transfer" aria-labelledby="writing-transfer-title" aria-busy={busy}>
    <div className="assessment-writing-header">
      <h2 id="writing-transfer-title" ref={heading} tabIndex={-1}>Writing 独立迁移</h2>
      <button type="button" className="focus-button-quiet" onClick={onBack}>返回 Writing 结果</button>
    </div>
    <p>请根据新材料独立作答。每份正式作文可进行一次迁移，提交后正文锁定。</p>
    {busy && <p role="status">正在读取或保存迁移…</p>}
    {error && <div className="focus-notice warning" role="alert"><p>{error}</p>
      <button type="button" className="focus-button-quiet" disabled={busy}
        onClick={() => void perform(() => writingTransfer.read(runId, learner))}>重新读取迁移</button>
    </div>}
    {!storageAvailable && <p role="alert">当前标签页无法保留恢复副本。离开前请确认草稿已保存至服务器。</p>}
    {loaded && !assignment && <button type="button" className="focus-button" disabled={busy}
      onClick={() => void perform(() => writingTransfer.start(runId, learner), { write: true })}>开始独立迁移</button>}
    {local && (!assignment || local.assignmentId !== assignment.id ||
      (assignment.response && local.body !== assignment.response.body)) && <details>
      <summary>查看本地保留的作答</summary>
      <pre className="assessment-writing-submitted-body" lang="en">{local.body}</pre>
    </details>}
    {assignment && <>
      <div className="assessment-writing-transfer-task">
        <h3 lang="en">{assignment.task.title}</h3>
        <p lang="en">{assignment.task.material}</p>
        <p lang="en">{assignment.task.prompt}</p>
        <small>原创 VerbaTrail 材料 · {assignment.task.content_version}</small>
      </div>
      {assignment.repeated_exposure && <p className="focus-notice warning">此材料曾向该学习者派发，本次记录将标记为重复曝光。</p>}
      {draft && <form className="assessment-writing-practice-editor" onSubmit={event => { event.preventDefault(); submit(); }}>
        <label htmlFor="writing-transfer-body">迁移作答</label>
        <textarea id="writing-transfer-body" className="focus-textarea" lang="en" value={body}
          maxLength={assignment.task.max_characters} readOnly={busy || pending} onChange={event => {
            if (!gate.current && !pending) remember({
              assignmentId: assignment.id, body: event.target.value, version: current?.version ?? draft.version, submitKey: null,
            });
          }} />
        <p role="status">{pending ? '正式提交待确认，将沿用原提交编号。'
          : body === draft.body ? `服务端草稿版本 ${draft.version}` : '有尚未同步的修改。'}</p>
        {conflict && <div className="focus-notice warning" role="alert">
          <p>服务器草稿已变化。本地作答仍保留。</p>
          <details><summary>查看服务器草稿</summary>
            <pre className="assessment-writing-submitted-body" lang="en">{draft.body}</pre>
          </details>
          <div className="focus-actions">
            <button type="button" className="focus-button-secondary" disabled={busy}
              onClick={() => { remember(null); setError(''); }}>采用服务器稿</button>
            <button type="button" className="focus-button-secondary" disabled={busy}
              onClick={() => save(true)}>明确保存本地稿</button>
          </div>
        </div>}
        <div className="focus-actions">
          <button type="button" className="focus-button-secondary"
            disabled={busy || pending || conflict || body === draft.body} onClick={() => save()}>保存迁移草稿</button>
          <button type="submit" className="focus-button" disabled={busy || conflict || !body.trim()}>
            {pending ? '重试正式提交' : '正式提交迁移'}
          </button>
        </div>
      </form>}
      {assignment.response && <TransferResult response={assignment.response} />}
    </>}
  </section>;
}

function TransferResult({ response }: { response: NonNullable<WritingTransfer['response']> }) {
  const { observation, lineage } = response;
  return <section aria-label="迁移作答记录">
    <h3>迁移作答已保存 · 语义质量未评估</h3>
    <p>{targetLabels[observation.target]} · {observation.word_count} 词 · {observation.paragraph_count} 段</p>
    <p>材料与训练不同 · {observation.repeated_exposure ? '已有派发记录' : '首次记录到派发'} · 证据不足</p>
    <pre className="assessment-writing-submitted-body" lang="en">{response.body}</pre>
    <p>这次作答只增加一份新材料下的文本记录。能力结论仍需语义评价、跨材料观察和延迟复习。</p>
    <details className="focus-disclosure">
      <summary>提交时间与证据来源</summary>
      <p>{new Date(response.submitted_at).toLocaleString()} · {observation.evaluation_version} · {observation.evaluation_status} · {observation.evidence_validity}</p>
      <dl className="assessment-writing-evidence-meta">
        <div><dt>迁移提交</dt><dd>{response.id}</dd></div>
        <div><dt>正文 SHA-256</dt><dd>{response.body_sha256}</dd></div>
        <div><dt>原始作文</dt><dd>{lineage.original_submission_id}</dd></div>
        <div><dt>初始诊断</dt><dd>{lineage.initial_diagnostic_id}</dd></div>
        {lineage.followup_diagnostic_id && <div><dt>追问复核</dt><dd>{lineage.followup_diagnostic_id}</dd></div>}
        <div><dt>训练记录</dt><dd>{lineage.practice_id}</dd></div>
        <div><dt>训练修订</dt><dd>{lineage.practice_revision_id}</dd></div>
      </dl>
    </details>
  </section>;
}
