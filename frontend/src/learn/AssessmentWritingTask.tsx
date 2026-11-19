import { useEffect, useMemo, useRef, useState } from 'react';
import {
  assessmentApi,
  type AssessmentWriting,
  type AssessmentWritingDraft,
  type AssessmentWritingSubmitRequest,
} from '../api/assessment';
import { ApiClientError } from '../api/client';
import {
  clearWritingRecovery,
  observeSubmittedParagraphs,
  readWritingRecovery,
  writingRecoveryKey,
  writeWritingRecovery,
  type TextObservation,
  type WritingRecovery,
} from './assessmentWritingState';
import { WritingDiagnosticPanel } from './WritingDiagnosticPanel';
import { WritingTransferPanel } from './WritingTransferPanel';
import { writingBufferIsPersistent } from './writingRecoveryStorage';
import './assessmentWriting.css';

const WORD = /[\p{L}\p{N}]+(?:['’\-][\p{L}\p{N}]+)*/gu;
const countWords = (value: string) => [...value.matchAll(WORD)].length;
const normalizedBody = (body: string) => body.replace(/\r\n?/g, '\n');
const sameSubmission = (task: AssessmentWriting, payload: AssessmentWritingSubmitRequest) => {
  const submission = task.submission;
  return task.task_status === 'completed' && !!submission &&
    submission.body === normalizedBody(payload.body) && submission.draft_version === payload.version &&
    submission.task_version === payload.task_version && submission.prompt_id === payload.prompt_id &&
    submission.prompt_version === payload.prompt_version;
};
const errorMessage = (cause: unknown) => cause instanceof Error
  ? cause.message : '请求失败。已保留本地文本，请重试。';

interface WritingTaskProps {
  runId: string;
  learner: string;
  visible: boolean;
  onChanged: (id: string) => Promise<void>;
}

export function AssessmentWritingTask(props: WritingTaskProps) {
  const { runId, learner, visible } = props;
  const viewKey = `assessment:writing-transfer:view:${encodeURIComponent(learner)}:${encodeURIComponent(runId)}`;
  const [transferOpen, setTransferOpen] = useState(() => {
    try { return sessionStorage.getItem(viewKey) === 'open'; }
    catch { return false; }
  });

  function openTransfer(open: boolean) {
    setTransferOpen(open);
    try {
      if (open) sessionStorage.setItem(viewKey, 'open');
      else sessionStorage.removeItem(viewKey);
    } catch { /* The server still preserves any saved transfer. */ }
  }

  // Mount only the transfer view while working on the new material.
  if (transferOpen) {
    return <div hidden={!visible} data-assessment-skill="writing" className="assessment-writing">
      <WritingTransferPanel runId={runId} learner={learner} onBack={() => openTransfer(false)} />
    </div>;
  }
  return <WritingAssessmentResult {...props} onOpenTransfer={() => openTransfer(true)} />;
}

function WritingAssessmentResult({ runId, learner, visible, onChanged, onOpenTransfer }: WritingTaskProps & {
  onOpenTransfer: () => void;
}) {
  const recoveryKey = writingRecoveryKey(learner, runId);
  const [task, setTask] = useState<AssessmentWriting | null>(null);
  const [recovery, setRecovery] = useState<WritingRecovery | null>(() => readWritingRecovery(recoveryKey));
  const [body, setBody] = useState(recovery?.body ?? '');
  const [serverDraft, setServerDraft] = useState<AssessmentWritingDraft | null>(null);
  const [draftConflict, setDraftConflict] = useState(false);
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [storageAvailable, setStorageAvailable] = useState(() => writingBufferIsPersistent(recoveryKey));
  const gate = useRef(false);
  const mounted = useRef(false);

  const remember = (nextBody: string, baseVersion: number, taskVersion: string | null,
    pendingSubmission: AssessmentWritingSubmitRequest | null = null): boolean => {
    if (!mounted.current) return false;
    const next: WritingRecovery = { body: nextBody, baseVersion, taskVersion, pendingSubmission, savedAt: new Date().toISOString() };
    setRecovery(next);
    const stored = writeWritingRecovery(recoveryKey, next);
    setStorageAvailable(stored);
    return stored;
  };

  const forgetRecovery = () => {
    if (!mounted.current) return;
    setRecovery(null);
    setStorageAvailable(clearWritingRecovery(recoveryKey));
  };

  const adopt = (next: AssessmentWriting) => {
    if (!mounted.current) return;
    setTask(next);
    setServerDraft(null);
    const saved = readWritingRecovery(recoveryKey);
    if (next.task_status === 'completed' && next.submission) {
      const preserveLocal = !!saved && normalizedBody(saved.body) !== next.submission.body;
      setBody(preserveLocal ? saved.body : next.submission.body);
      setRecovery(preserveLocal ? saved : null);
      if (!preserveLocal) forgetRecovery();
      setDraftConflict(preserveLocal);
      return;
    }
    if (next.task_status === 'in_progress' && next.draft) {
      if (saved && (saved.pendingSubmission || normalizedBody(saved.body) !== next.draft.body)) {
        const conflict = saved.taskVersion !== next.task_version || saved.baseVersion !== next.draft.version;
        setBody(saved.body);
        setRecovery(saved);
        setDraftConflict(conflict);
        if (conflict) setServerDraft(next.draft);
        return;
      }
      setBody(next.draft.body);
      setDraftConflict(false);
      forgetRecovery();
      return;
    }
    setBody(saved?.body ?? '');
    setRecovery(saved);
    setDraftConflict(false);
  };

  const readLatest = async (signal?: AbortSignal) => {
    const next = await assessmentApi.writing(runId, learner, signal);
    adopt(next);
    return next;
  };

  const refresh = async () => {
    if (gate.current) return;
    gate.current = true;
    setLoading(true);
    setError('');
    try { await readLatest(); }
    catch (cause) { if (mounted.current) setError(errorMessage(cause)); }
    finally { gate.current = false; if (mounted.current) setLoading(false); }
  };

  useEffect(() => {
    mounted.current = true;
    const controller = new AbortController();
    setLoading(true);
    assessmentApi.writing(runId, learner, controller.signal).then(next => {
      if (!controller.signal.aborted) adopt(next);
    }).catch(cause => {
      if (mounted.current && !controller.signal.aborted && !(cause instanceof ApiClientError && cause.code === 'REQUEST_ABORTED')) {
        setError(errorMessage(cause));
      }
    }).finally(() => { if (mounted.current && !controller.signal.aborted) setLoading(false); });
    return () => { mounted.current = false; controller.abort(); };
  }, [runId, learner]);

  useEffect(() => {
    const hasUnconfirmedText = !!recovery?.pendingSubmission ||
      (task?.task_status === 'in_progress' && body !== task.draft?.body) || draftConflict;
    if (!hasUnconfirmedText) return;
    const protect = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = '';
    };
    window.addEventListener('beforeunload', protect);
    return () => window.removeEventListener('beforeunload', protect);
  }, [body, draftConflict, recovery?.pendingSubmission, task]);

  const perform = async (work: () => Promise<void>) => {
    if (gate.current) return;
    gate.current = true;
    setBusy(true);
    setError('');
    setNotice('');
    try { await work(); }
    catch (cause) { if (mounted.current) setError(errorMessage(cause)); }
    finally { gate.current = false; if (mounted.current) setBusy(false); }
  };

  const refreshRun = async () => {
    try { await onChanged(runId); }
    catch { if (mounted.current) setNotice('Writing 已保存。评估进度暂未刷新，重新读取即可同步。'); }
  };

  const start = () => void perform(async () => {
    const next = await assessmentApi.startWriting(runId, learner);
    adopt(next);
    await refreshRun();
  });

  const saveDraft = (explicitVersion?: number) => void perform(async () => {
    if (!task?.draft || task.task_status !== 'in_progress' ||
        (explicitVersion === undefined && (draftConflict || recovery?.pendingSubmission)) ||
        (recovery && recovery.taskVersion !== task.task_version)) return;
    const baseVersion = explicitVersion ?? task.draft.version;
    try {
      const saved = await assessmentApi.saveWritingDraft(runId, { learner_id: learner, body, version: baseVersion });
      if (!mounted.current) return;
      const updated = { ...task, draft: saved };
      setTask(updated);
      setServerDraft(null);
      setDraftConflict(false);
      if (normalizedBody(body) === saved.body) { setBody(saved.body); forgetRecovery(); }
      else remember(body, saved.version, task.task_version);
      await refreshRun();
    } catch (cause) {
      if (cause instanceof ApiClientError && cause.status === 409) {
        const latest = await assessmentApi.writing(runId, learner);
        if (!mounted.current) return;
        adopt(latest);
        if ((latest.submission?.body ?? latest.draft?.body) !== normalizedBody(body)) {
          setError('服务器版本已变化。本地正文仍保留。');
        }
        return;
      }
      throw cause;
    }
  });

  const useServerDraft = () => {
    if (!serverDraft || !task) return;
    setBody(serverDraft.body);
    setTask({ ...task, draft: serverDraft });
    setServerDraft(null);
    setDraftConflict(false);
    setError('');
    forgetRecovery();
  };

  const setEssay = (value: string) => {
    if (gate.current || loading || recovery?.pendingSubmission ||
        (recovery && recovery.taskVersion !== task?.task_version)) return;
    setBody(value);
    setError('');
    const baseVersion = recovery?.baseVersion ?? task?.draft?.version ?? 0;
    remember(value, baseVersion, task?.task_version ?? recovery?.taskVersion ?? null);
  };

  const acceptSubmission = async (next: AssessmentWriting, payload: AssessmentWritingSubmitRequest) => {
    if (!mounted.current) return false;
    if (!sameSubmission(next, payload)) {
      setTask(next);
      setServerDraft(null);
      setDraftConflict(true);
      setError('服务器已有另一份正式提交。该提交保持只读；你的本地正文已保留在此浏览器中。');
      return false;
    }
    adopt(next);
    await refreshRun();
    return true;
  };

  const submit = () => void perform(async () => {
    if (!task?.draft || task.task_status !== 'in_progress' || (draftConflict && !recovery?.pendingSubmission) ||
        (recovery && recovery.taskVersion !== task.task_version)) return;
    if (!countWords(body)) {
      setError('请先输入作文正文。');
      return;
    }
    let payload = recovery?.pendingSubmission ?? null;
    if (payload) {
      if (payload.learner_id !== learner || payload.task_version !== task.task_version ||
          payload.prompt_id !== task.prompt?.prompt_id || payload.prompt_version !== task.prompt?.prompt_version) {
        remember(body, recovery?.baseVersion ?? task.draft.version, task.task_version);
        setError('待确认请求与当前任务不一致。正文已保留，请重新读取后再决定是否提交。');
        return;
      }
      if (!remember(payload.body, payload.version, payload.task_version, payload)) {
        setError('无法恢复待确认提交的本地副本。正文仍保留在编辑器中，暂不重试提交。');
        return;
      }
      const current = await assessmentApi.writing(runId, learner);
      if (!mounted.current) return;
      if (current.task_status === 'completed') {
        await acceptSubmission(current, payload);
        return;
      }
      if (current.task_status !== 'in_progress' || current.task_version !== payload.task_version ||
          current.draft?.version !== payload.version) {
        setTask(current);
        setDraftConflict(true);
        setServerDraft(current.draft);
        // GET confirms there is no formal submission, so the pending intent can be
        // returned to a recoverable local draft and explicitly resolved by the learner.
        remember(payload.body, payload.version, payload.task_version, null);
        setError('待确认提交对应的草稿版本已变化，服务端尚无正式提交。原始正文已保留；请比较草稿后选择采用服务器版本或明确保存本地版本。');
        return;
      }
    } else {
      if (!task.prompt || !task.task_version) return;
      payload = {
        learner_id: learner,
        body,
        version: task.draft.version,
        task_version: task.task_version,
        prompt_id: task.prompt.prompt_id,
        prompt_version: task.prompt.prompt_version,
        idempotency_key: crypto.randomUUID(),
      };
      if (!remember(body, payload.version, payload.task_version, payload)) {
        remember(body, payload.version, payload.task_version);
        setError('无法安全保存待确认提交。作文仍保留在编辑器中，请先启用浏览器本地存储。');
        return;
      }
    }
    try {
      const next = await assessmentApi.submitWriting(runId, payload);
      await acceptSubmission(next, payload);
    } catch (cause) {
      if (cause instanceof ApiClientError && cause.status === 422) {
        remember(payload.body, payload.version, payload.task_version, null);
        setError(cause.message);
        return;
      }
      if (cause instanceof ApiClientError && cause.status === 409) {
        const latest = await assessmentApi.writing(runId, learner).catch(() => null);
        if (!mounted.current) return;
        if (latest?.task_status === 'completed') {
          await acceptSubmission(latest, payload);
          return;
        }
        if (latest) {
          setTask(latest);
          setServerDraft(latest.draft);
          setDraftConflict(true);
          if (latest.task_status === 'in_progress') {
            remember(payload.body, payload.version, payload.task_version, null);
          }
        }
        setError(latest?.task_status === 'in_progress'
          ? '服务器确认尚无正式提交，但草稿版本发生冲突。原文仍保留；请比较草稿后选择服务器版本或明确保存本地版本。'
          : '正式提交发生冲突。服务器正文不会被覆盖；本地正文和恢复状态仍保留。');
        return;
      }
      // The POST may have committed before the connection failed. Keep its exact payload/key.
      const latest = await assessmentApi.writing(runId, learner).catch(() => null);
      if (!mounted.current) return;
      if (latest?.task_status === 'completed') {
        await acceptSubmission(latest, payload);
        return;
      }
      if (latest) setTask(latest);
      setError('暂时无法确认提交结果。正文和原提交编号已保留；重试会先读取服务器并沿用相同提交请求。');
    }
  });

  const submission = task?.submission;
  const observations = useMemo(() => submission
    ? observeSubmittedParagraphs(submission.body, submission.submission_id, submission.body_sha256)
    : [], [submission?.submission_id, submission?.body_sha256, submission?.body]);
  const pending = recovery?.pendingSubmission ?? null;
  const incompatibleRecovery = !!recovery && recovery.taskVersion !== task?.task_version;
  const localBodyDiffersFromResult = !!submission && !!recovery && recovery.body !== submission.body;

  return <div hidden={!visible} data-assessment-skill="writing" className="assessment-writing">
    {loading && <p role="status">正在读取 Writing 任务…</p>}
    {error && <div className="focus-notice danger" role="alert">{error}
      <button type="button" disabled={busy} onClick={() => void refresh()}>重新读取 Writing</button>
    </div>}
    {notice && <p role="status" className="assessment-writing-notice">{notice}</p>}
    {recovery && (!task || task.task_status === 'not_started') && <section className="focus-card assessment-writing-recovery">
      <h2>本地恢复正文</h2>
      <p>服务器暂时不可用。以下正文只在此浏览器恢复区中，尚未确认已同步到服务端。</p>
      <pre lang="en">{recovery.body}</pre>
    </section>}
    {task?.task_status === 'not_started' && <section className="focus-card assessment-writing-entry">
      <h2>Writing 初始任务</h2>
      <p>一项原创 Task 2 风格文本采集任务。建议 250 词；建议长度和完成状态不用于推断写作水平。</p>
      <button type="button" className="focus-button" disabled={busy} onClick={start}>开始 Writing</button>
      <p className="assessment-writing-source">任务来源：原创 VerbaTrail 内容；不包含 IELTS 真题。</p>
    </section>}
    {task?.prompt && task.task_status === 'in_progress' && <>
      <section className="focus-card assessment-writing-prompt">
        <div className="assessment-writing-header">
          <div><p className="focus-eyebrow">WRITING · TASK 2 STYLE</p><h2>Writing 初始任务</h2></div>
          <span className="assessment-writing-version">{task.task_version}</span>
        </div>
        <p className="assessment-writing-question" lang="en">{task.prompt.text}</p>
        <p>{task.prompt.instructions}</p>
        <p className="assessment-writing-source">建议 {task.prompt.suggested_words} 词 · 最多 {task.prompt.max_characters.toLocaleString()} 字符。建议长度不是评分标准。</p>
      </section>
      <section className="focus-card assessment-writing-editor" aria-labelledby="assessment-writing-editor-title">
        <div className="assessment-writing-header">
          <label id="assessment-writing-editor-title" htmlFor="assessment-writing-body">作文正文</label>
          <span>{countWords(body)} 词 · 草稿版本 {task.draft?.version ?? 0}</span>
        </div>
        <textarea id="assessment-writing-body" className="focus-textarea" lang="en" spellCheck
          value={body} maxLength={task.prompt.max_characters} readOnly={loading || busy || !!pending || incompatibleRecovery}
          aria-describedby="assessment-writing-save-state"
          onChange={event => setEssay(event.target.value)} />
        <div className="assessment-writing-toolbar">
          <span id="assessment-writing-save-state" role="status">
            {pending ? '正式提交待确认 · 重试将沿用原提交编号' : draftConflict ? '服务器草稿已变化 · 本地正文仍保留' :
              body === task.draft?.body ? `服务端草稿已保存 · ${task.draft?.updated_at ? new Date(task.draft.updated_at).toLocaleString() : ''}` : '有尚未同步的本地修改'}
          </span>
          <div className="focus-actions">
            <button type="button" className="focus-button-secondary" disabled={loading || busy || !!pending || draftConflict || !task.draft || body === task.draft.body}
              onClick={() => saveDraft()}>{busy ? '正在保存…' : '保存服务端草稿'}</button>
            <button type="button" className="focus-button" disabled={loading || busy || incompatibleRecovery || (draftConflict && !pending) || (!pending && !countWords(body))}
              onClick={submit}>{pending ? '重试正式提交' : '正式提交作文'}</button>
          </div>
        </div>
        {!storageAvailable && <p className="focus-notice danger" role="alert">本地恢复存储不可用。提交前请确认服务端草稿已经保存。</p>}
        {draftConflict && (!pending || incompatibleRecovery) && serverDraft && <div className="focus-notice warning" role="alert">
          <p>检测到另一个标签页或会话保存了不同草稿。没有自动覆盖任一正文。</p>
          <details><summary>查看服务器草稿</summary>
            <pre className="assessment-writing-submitted-body" lang="en">{serverDraft.body}</pre>
          </details>
          <div className="focus-actions">
            <button type="button" className="focus-button-secondary" disabled={busy} onClick={useServerDraft}>采用服务器草稿</button>
            {!incompatibleRecovery && <button type="button" className="focus-button-secondary" disabled={busy}
              onClick={() => saveDraft(serverDraft.version)}>明确保存当前本地正文</button>}
          </div>
        </div>}
      </section>
    </>}
    {submission && <>
      <section className="focus-card assessment-writing-result" aria-labelledby="assessment-writing-result-title">
        <div className="assessment-writing-header"><div><p className="focus-eyebrow">原始文本证据</p><h2 id="assessment-writing-result-title">作文已提交 · 写作能力未评估</h2></div>
          <span className="assessment-writing-version">{submission.task_version}</span></div>
        <p>提交时间 · {new Date(submission.submitted_at).toLocaleString()} · {submission.word_count} 词 · {submission.paragraph_count} 段</p>
        <pre className="assessment-writing-submitted-body" lang="en">{submission.body}</pre>
        <dl className="assessment-writing-evidence-meta">
          <div><dt>提交证据</dt><dd>{submission.submission_id}</dd></div>
          <div><dt>正文 SHA-256</dt><dd>{submission.body_sha256}</dd></div>
          <div><dt>语义质量</dt><dd>not_assessed</dd></div>
          <div><dt>能力证据有效性</dt><dd>false · 未形成能力结论</dd></div>
        </dl>
      </section>
      {localBodyDiffersFromResult && <section className="focus-notice warning" role="alert">
        <strong>服务器正式提交已锁定。</strong> 当前浏览器另有一份未提交或冲突正文，未用它替换正式结果。
        <details><summary>查看并复制本地恢复正文</summary><pre lang="en">{recovery.body}</pre></details>
        <button type="button" className="focus-button-secondary" onClick={() => { setBody(submission.body); forgetRecovery(); setDraftConflict(false); }}>清除本地副本</button>
      </section>}
      <TextObservationPanel submissionId={submission.submission_id} body={submission.body}
        bodySha256={submission.body_sha256} observations={observations} />
      <WritingDiagnosticPanel key={submission.submission_id} runId={runId} learner={learner}
        submissionId={submission.submission_id} onOpenTransfer={onOpenTransfer} />
      <section className="focus-card assessment-writing-limitations">
        <h3>评价边界</h3>
        <p>这次结果只证明原文已被采集。Task Response、Coherence and Cohesion、Lexical Resource、Grammatical Range and Accuracy 和 IELTS Writing Band 均未评估。</p>
        {task?.limitations.map(item => <p key={item}>{item}</p>)}
      </section>
    </>}
  </div>;
}

function TextObservationPanel({ submissionId, body, bodySha256, observations }: {
  submissionId: string; body: string; bodySha256: string; observations: TextObservation[];
}) {
  return <section className="focus-card assessment-writing-observations" aria-labelledby="assessment-writing-observations-title">
    <div className="assessment-writing-header"><div><p className="focus-eyebrow">D2 · 可复核文本事实</p><h2 id="assessment-writing-observations-title">原文引用观察</h2></div>
      <span className="assessment-writing-version">{observations.length} 个文本片段</span></div>
    <p>这里是浏览器按空行生成的原文预览。诊断和训练使用各自的服务端引用及版本。片段和计数只描述文本。</p>
    <div className="assessment-writing-observation-grid">
      {observations.map(observation => <article className="assessment-writing-observation" key={observation.id}>
        <header><strong>{observation.fact}</strong><code>{observation.id}</code></header>
        <blockquote lang="en">“{observation.source_reference.quote}”</blockquote>
        <dl>
          <div><dt>来源有效性</dt><dd>精确引用 · {observation.source_validity}</dd></div>
          <div><dt>作答表现评价</dt><dd>{observation.response_performance}</dd></div>
          <div><dt>能力推断</dt><dd>{observation.ability_inference}</dd></div>
        </dl>
        <code className="assessment-writing-source-range">{observation.source_reference.start}–{observation.source_reference.end} UTF-16 · {submissionId} · {bodySha256.slice(0, 12)}…</code>
        {body.slice(observation.source_reference.start, observation.source_reference.end) !== observation.source_reference.quote &&
          <p role="alert">此引用与提交原文不匹配，观察不可用。</p>}
      </article>)}
    </div>
  </section>;
}
