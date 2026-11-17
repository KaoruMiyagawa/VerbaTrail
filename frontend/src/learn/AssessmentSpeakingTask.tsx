import { useEffect, useRef, useState } from 'react';
import { assessmentApi, type SpeakingAssessment } from '../api/assessment';
import { ApiClientError } from '../api/client';
import { chooseRecording, matchesRegistration, registerAssessmentRecording, SpeakingRegistrationConflict } from './assessmentSpeakingState';
import { listAssessmentRecordings, saveAssessmentRecording, type AssessmentRecording } from './assessmentSpeakingStorage';

const MAX_SECONDS = 600;

interface Capture {
  stream?: MediaStream;
  recorder?: MediaRecorder;
  timer?: number;
  startedAt: number;
  stoppedAt?: number;
}

function stopRecording(capture: Capture | null) {
  if (capture?.recorder?.state !== 'recording') return;
  capture.stoppedAt = performance.now();
  capture.recorder.stop();
}

function releaseCapture(capture: Capture) {
  window.clearInterval(capture.timer);
  capture.stream?.getTracks().forEach(track => {
    track.onended = null;
    track.stop();
  });
}

function Playback({ item, onError }: { item: AssessmentRecording; onError: () => void }) {
  const [url, setUrl] = useState('');
  useEffect(() => {
    const localUrl = URL.createObjectURL(item.blob);
    setUrl(localUrl);
    return () => URL.revokeObjectURL(localUrl);
  }, [item.blob]);
  return url && <audio src={url} controls preload="metadata" aria-label="播放初始评估本地录音"
    onError={onError} style={{ width: '100%', maxWidth: '32rem' }} />;
}

export function AssessmentSpeakingTask({ runId, learner, onChanged }: {
  runId: string; learner: string; onChanged: (id: string) => Promise<void>;
}) {
  const [task, setTask] = useState<SpeakingAssessment>();
  const [local, setLocal] = useState<AssessmentRecording>();
  const [unsaved, setUnsaved] = useState<AssessmentRecording>();
  const [storage, setStorage] = useState<'loading' | 'ready' | 'error'>('loading');
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState('');
  const [playbackError, setPlaybackError] = useState(false);
  const [phase, setPhase] = useState<'idle' | 'permission' | 'recording' | 'saving'>('idle');
  const [seconds, setSeconds] = useState(0);
  const gate = useRef(false);
  const mounted = useRef(false);
  const capture = useRef<Capture | null>(null);
  const read = () => assessmentApi.speaking(runId, learner);

  async function accept(next: SpeakingAssessment) {
    if (!mounted.current) return;
    setTask(next);
    setStorage('loading');
    setPlaybackError(false);
    try {
      const items = await listAssessmentRecordings(next);
      if (!mounted.current) return;
      const item = chooseRecording(next, items);
      setLocal(item);
      setStorage('ready');
      if (next.task_status === 'completed') {
        if (items.some(entry => entry.registration === 'pending' && !matchesRegistration(next, entry))) {
          setError('服务端登记与此处的待登记录音不一致。本地录音已保留。');
        }
        if (item && item.registration !== 'registered') {
          await saveAssessmentRecording({ ...item, registration: 'registered' });
        }
      }
    } catch {
      if (mounted.current) setStorage('error');
    }
  }

  async function action(work: () => Promise<void>, failure: (error: unknown) => string) {
    if (gate.current) return;
    gate.current = true;
    setBusy(true);
    setError('');
    try { await work(); }
    catch (cause) { if (mounted.current) setError(failure(cause)); }
    finally {
      gate.current = false;
      if (mounted.current) setBusy(false);
    }
  }

  const taskError = (cause: unknown) => cause instanceof ApiClientError && cause.status === 409
    ? '此 Speaking 任务的历史版本或登记记录无法恢复。你可以打开另一轮评估。'
    : '无法读取 Speaking 任务，请检查连接后重试。';
  const reload = () => action(async () => { await accept(await read()); }, taskError);

  useEffect(() => {
    mounted.current = true;
    // Defer the initial load until the current effect is active (including in StrictMode).
    let canceled = false;
    queueMicrotask(() => { if (!canceled) void reload(); });
    const cancelCapture = () => {
      const current = capture.current;
      capture.current = null;
      if (!current) return;
      if (current.recorder && current.recorder.state !== 'inactive') current.recorder.stop();
      releaseCapture(current);
    };
    const onPageHide = () => {
      const interrupted = !!capture.current;
      cancelCapture();
      if (interrupted) {
        setPhase('idle');
        setError('录音已中断，请重新录制。');
      }
    };
    window.addEventListener('pagehide', onPageHide);
    return () => {
      canceled = true;
      mounted.current = false;
      window.removeEventListener('pagehide', onPageHide);
      cancelCapture();
    };
  }, []);

  async function persist(item: AssessmentRecording) {
    setPhase('saving');
    setError('');
    try {
      await saveAssessmentRecording(item);
      if (mounted.current) {
        setLocal(item);
        setUnsaved(undefined);
        setPlaybackError(false);
      }
    } catch {
      if (mounted.current) {
        setUnsaved(item);
        setError('录音保存失败。请检查浏览器存储空间或设置，再重试保存。');
      }
    } finally { if (mounted.current) setPhase('idle'); }
  }

  async function startRecording() {
    if (capture.current || gate.current || phase !== 'idle' || storage !== 'ready' || unsaved ||
        task?.task_status !== 'in_progress' || !task.task_version || !task.prompt || local?.registration === 'pending') return;
    if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === 'undefined') {
      setError('此浏览器无法录音。请使用支持麦克风的浏览器，通过 HTTPS 或 localhost 打开页面。');
      return;
    }
    const { task_version, prompt } = task;
    const current: Capture = { startedAt: 0 };
    capture.current = current;
    setPhase('permission');
    setError('');
    setSeconds(0);
    const interrupt = (message: string) => {
      if (capture.current !== current) return;
      capture.current = null;
      if (current.recorder && current.recorder.state !== 'inactive') current.recorder.stop();
      releaseCapture(current);
      setPhase('idle');
      setError(message);
    };
    try {
      current.stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      if (capture.current !== current) { releaseCapture(current); return; }
      const recorder = new MediaRecorder(current.stream);
      current.recorder = recorder;
      const chunks: Blob[] = [];
      const recordedAt = new Date().toISOString();
      current.stream.getAudioTracks().forEach(track => {
        track.onended = () => interrupt('麦克风已断开，本次录音未保存。请重新录制。');
      });
      recorder.ondataavailable = event => { if (event.data.size) chunks.push(event.data); };
      recorder.onerror = () => interrupt('录音失败，请检查麦克风并重试。');
      recorder.onstop = () => {
        if (capture.current !== current) return;
        if (current.stoppedAt === undefined || current.stream?.getAudioTracks().some(track => track.readyState === 'ended')) {
          interrupt('录音已中断，本次录音未保存。请重新录制。');
          return;
        }
        const duration = Math.min(MAX_SECONDS, (current.stoppedAt - current.startedAt) / 1000);
        releaseCapture(current);
        capture.current = null;
        const blob = new Blob(chunks, { type: recorder.mimeType || chunks[0]?.type });
        if (!blob.size || duration < 0.1) {
          setPhase('idle');
          setError('录音为空或时间太短，请重新录制。');
          return;
        }
        void persist({
          runId, blob, registration: 'local',
          metadata: {
            learner_id: learner, recording_id: crypto.randomUUID(), task_version,
            prompt_id: prompt.prompt_id, prompt_version: prompt.prompt_version,
            duration_seconds: Number(duration.toFixed(2)), recorded_at: recordedAt,
            idempotency_key: crypto.randomUUID(),
          },
        });
      };
      current.startedAt = performance.now();
      recorder.start();
      setPhase('recording');
      current.timer = window.setInterval(() => {
        const elapsed = Math.min(MAX_SECONDS, Math.floor((performance.now() - current.startedAt) / 1000));
        setSeconds(elapsed);
        if (elapsed >= MAX_SECONDS) { setPhase('saving'); stopRecording(current); }
      }, 250);
    } catch (cause) {
      const name = cause instanceof DOMException ? cause.name : '';
      interrupt(name === 'NotAllowedError' ? '麦克风权限被拒绝，请在浏览器设置中允许访问。'
        : name === 'NotFoundError' ? '未找到麦克风，请连接设备后重试。'
          : '无法启动麦克风，请检查设备和浏览器设置。');
    }
  }

  function register() {
    if (!local || unsaved || capture.current || phase !== 'idle' || playbackError || storage !== 'ready' || task?.task_status !== 'in_progress') return;
    void action(async () => {
      const next = await registerAssessmentRecording(local, read, metadata => assessmentApi.completeSpeaking(runId, metadata), async pending => {
        await saveAssessmentRecording(pending);
        if (mounted.current) setLocal(pending);
      });
      await accept(next);
      if (mounted.current) void onChanged(runId).catch(() => {
        if (mounted.current) setError('任务已登记。评估列表暂未更新，刷新页面可重新读取。');
      });
    }, cause => cause instanceof SpeakingRegistrationConflict ? cause.message
      : cause instanceof ApiClientError && cause.status === 409 ? '录音登记发生冲突。请重新读取任务，本地录音已保留。'
        : '录音登记尚未确认，本地录音已保留。请重试登记。');
  }

  const done = task?.task_status === 'completed';
  const active = task?.task_status === 'in_progress';
  const canRecord = !busy && phase === 'idle' && storage === 'ready' && !unsaved && local?.registration !== 'pending';
  return <div data-assessment-skill="speaking">
    {busy && <p role="status">正在读取或保存 Speaking…</p>}
    {error && <div className="focus-notice danger" role="alert">{error}{!unsaved && phase === 'idle' &&
      <button type="button" disabled={busy} onClick={() => void reload()}>重新读取 Speaking</button>}</div>}
    {task?.task_status === 'not_started' && <button type="button" className="focus-button" disabled={busy}
      onClick={() => void action(async () => {
        await accept(await assessmentApi.startSpeaking(runId, learner));
        if (mounted.current) await onChanged(runId);
      }, taskError)}>开始 Speaking</button>}
    {task?.prompt && <section className="focus-card" style={{ overflowWrap: 'anywhere' }}>
      <h2>Speaking 初始任务</h2>
      <p lang="en">{task.prompt.text}</p>
      <p>请用英语作答，建议录音 {task.prompt.suggested_seconds} 秒。录音只保存在当前浏览器，服务端仅登记录音信息。</p>
      <p>任务版本 · {task.task_version}</p>
      {task.repeated_exposure && <p>你之前已开始过相同版本的任务。</p>}
      {done && <>
        <h3>任务已登记 · 口语能力未评分</h3>
        <p>已登记时长 · {task.recording?.duration_seconds.toFixed(1)} 秒</p>
        {storage === 'ready' && (!local || playbackError) && <p>录音任务已登记，但当前浏览器没有可播放的本地录音。</p>}
      </>}
      {storage === 'error' && <p className="focus-notice danger" role="alert">无法访问本地录音存储，请检查浏览器设置。
        <button type="button" disabled={busy} onClick={() => void reload()}>重试本地存储</button></p>}
      {storage === 'loading' && <p role="status">正在检查本地录音…</p>}
      {active && <>
        <p aria-live="polite">{phase === 'permission' ? '等待麦克风权限…' : phase === 'recording' ? <>正在录音 <span role="timer">· {seconds} 秒</span></> :
          phase === 'saving' ? '正在保存本地录音…' : unsaved ? '录音尚未保存到此浏览器' : local ?
            (local.registration === 'pending' ? '本地录音已保存，登记尚未确认' : '本地录音已保存，尚未登记') : '当前浏览器尚无录音'}</p>
        <div className="focus-actions">
          {phase === 'recording' ? <button type="button" className="focus-button" onClick={() => {
            setPhase('saving');
            stopRecording(capture.current);
          }}>停止录音</button> : <button type="button" className="focus-button" disabled={!canRecord}
            onClick={() => void startRecording()}>{local ? '重新录音' : '开始录音'}</button>}
          {unsaved && <button type="button" className="focus-button-secondary" disabled={phase !== 'idle'}
            onClick={() => void persist(unsaved)}>重试保存录音</button>}
        </div>
      </>}
      {local && <div className="speaking-recording" style={{ marginTop: '1rem' }}>
        <p>本地录音 · {local.metadata.duration_seconds.toFixed(1)} 秒</p>
        <Playback item={local} onError={() => setPlaybackError(true)} />
        {playbackError && <p role="alert">无法播放本地录音，文件可能已损坏或格式不受支持。</p>}
        {active && <div className="focus-actions"><button type="button" className="focus-button-secondary"
          disabled={busy || phase !== 'idle' || !!unsaved || playbackError || storage !== 'ready'} onClick={register}>
          {local.registration === 'pending' ? '重试登记录音任务' : '登记本次录音任务'}
        </button></div>}
      </div>}
    </section>}
  </div>;
}
