import { useCallback, useEffect, useRef, useState } from 'react';
import type { WorkspaceId } from '../app-shell/navigation';
import { ApiClientError } from '../api/client';
import { readingApi, type ReadingArticle, type ReadingArticleView, type ReadingItem, type ReadingItemResult,
  type ReadingResult, type ReadingSection, type ReadingSectionType } from '../api/reading';
import { useApiResource } from '../learning-workspace/useApiResource';
import { ExamGoalSettings } from './ExamGoalSettings';
import './reading.css';

const learner = 'default_learner';
const selectedKey = `reading:article:${learner}`;
const draftKey = (id: string) => `reading:draft:${learner}:${id}`;
const difficultyLabels: Record<number, string> = { 1: '基础', 2: '进阶', 3: '挑战' };
const sectionLabels: Record<ReadingSectionType, string> = {
  tfng: 'True / False / Not Given',
  matching_headings: 'Matching Headings',
  multiple_choice: 'Multiple Choice',
};

function readStorage(key: string) {
  try { return sessionStorage.getItem(key); } catch { return null; }
}

function store(key: string, value: string | null) {
  try {
    if (value === null) sessionStorage.removeItem(key);
    else sessionStorage.setItem(key, value);
    return true;
  } catch { return false; }
}

function restoreDraft(article: ReadingArticle): Record<string, string> {
  try {
    const stored = JSON.parse(readStorage(draftKey(article.id)) ?? 'null');
    const entries: [string, string][] = [];
    for (const section of article.sections) {
      const used = new Set<string>();
      for (const item of section.items) {
        const option = item.options.find(candidate => candidate.id === stored?.[item.id]);
        if (option && (section.type !== 'matching_headings' || !used.has(option.id))) {
          entries.push([item.id, option.id]);
          used.add(option.id);
        }
      }
    }
    return Object.fromEntries(entries);
  } catch { return {}; }
}

function loadMessage(error: ApiClientError) {
  if (error.code === 'NETWORK_ERROR' || error.code === 'REQUEST_TIMEOUT') return '暂时无法连接后端，请检查连接后重试。';
  if (error.status === 503 && error.message.includes('题库尚未导入')) return error.message;
  return `阅读内容加载失败。${error.message}`;
}

export function ReadingWorkspace({ onNavigate }: { onNavigate: (workspace: WorkspaceId) => void }) {
  const [selected, setSelected] = useState(() => readStorage(selectedKey));
  const [completed, setCompleted] = useState<Record<string, number>>({});
  const [choiceOpen, setChoiceOpen] = useState(false);
  const load = useCallback((signal: AbortSignal) => readingApi.list(learner, signal), []);
  const { state, retry } = useApiResource(load);
  const catalog = state.status === 'success' ? state.data : null;
  const article = catalog?.articles.find(item => item.id === selected);

  function open(id: string | null) {
    setSelected(id);
    store(selectedKey, id);
    window.scrollTo({ top: 0, behavior: 'instant' });
  }

  if (article) {
    return <ArticlePage key={article.id} articleId={article.id} onBack={() => open(null)}
      onCompleted={result => setCompleted(previous => ({ ...previous, [article.id]: result.correct_count }))} />;
  }

  return <div className="learn-page reading-workspace">
    <button type="button" className="focus-button-quiet" onClick={() => onNavigate('home')}>← 返回 Learn</button>
    <header className="focus-page-header reading-header">
      <h1 data-workspace-heading tabIndex={-1}>Reading · 阅读</h1>
      <p>选一篇英文文章，完成题目，再对照原文查看解析。</p>
    </header>
    {(state.status === 'idle' || state.status === 'loading') && <p role="status">正在读取阅读目录…</p>}
    {state.status === 'error' && <section className="focus-card">
      <p role="alert">{loadMessage(state.error)}</p>
      <button type="button" className="focus-button-secondary" onClick={() => void retry()}>重新加载目录</button>
    </section>}
    {catalog?.status === 'selection_required' && <p className="focus-help">先保存考试类型，即可查看对应练习。</p>}
    {catalog && (catalog.status === 'selection_required' || choiceOpen) && <ExamGoalSettings onSelectionSaved={() => {
      setChoiceOpen(false);
      void retry();
    }} />}
    {catalog?.status === 'unimported' && <section className="focus-card">
      <h2>Academic 题库尚未导入</h2>
      <p>后端已连接。题库导入后可重新加载阅读目录。</p>
      <button type="button" className="focus-button-secondary" onClick={() => void retry()}>重新加载目录</button>
    </section>}
    {catalog?.status === 'empty' && <section className="focus-card">
      <h2>{catalog.exam_variant === 'general_training' ? 'General Training 暂无整篇练习' : '暂无可用文章'}</h2>
      <p>{catalog.exam_variant === 'general_training'
        ? '可以打开原有阅读练习，或切换考试类型查看 Academic 文章。'
        : '当前考试类型没有已发布的整篇文章。'}</p>
      {catalog.exam_variant !== 'academic' && !choiceOpen && <button type="button" className="focus-button-secondary"
        onClick={() => setChoiceOpen(true)}>更改考试类型</button>}
    </section>}
    {catalog?.status === 'ready' && <>
      <div className="reading-list-heading">
        <h2 lang="en">IELTS Academic</h2>
        {!choiceOpen && <button type="button" className="focus-button-quiet" onClick={() => setChoiceOpen(true)}>更改考试类型</button>}
      </div>
      {catalog.missing_count > 0 && <p role="status">还有 {catalog.missing_count} 篇文章的题库尚未导入。</p>}
      <ul className="reading-catalog">
        {catalog.articles.map(item => {
          const score = completed[item.id] ?? item.correct_count;
          const done = item.status === 'completed' || score !== null;
          return <li key={item.id} className="focus-card reading-catalog-item">
            <div>
              <span className="reading-meta">{item.theme} · {difficultyLabels[item.difficulty]} · {item.question_count} 题</span>
              <h3 lang="en">{item.title}</h3>
              <p lang="en">{item.summary}</p>
              <span className="reading-meta">{done ? `已提交 · ${score}/${item.question_count}`
                : readStorage(draftKey(item.id)) ? '有本地草稿' : '未开始'}</span>
            </div>
            <button type="button" className="focus-button-secondary"
              aria-label={`${done ? '查看解析' : '打开文章'} · ${item.title}`} onClick={() => open(item.id)}>
              {done ? '查看解析' : '打开文章'} <span aria-hidden="true">→</span>
            </button>
          </li>;
        })}
      </ul>
    </>}
    <button type="button" className="focus-button-quiet" onClick={() => onNavigate('learning')}>原有阅读练习与诊断 →</button>
  </div>;
}

function ArticlePage({ articleId, onBack, onCompleted }: {
  articleId: string; onBack: () => void; onCompleted: (result: ReadingResult) => void;
}) {
  const load = useCallback((signal: AbortSignal) => readingApi.get(articleId, learner, signal), [articleId]);
  const { state, retry } = useApiResource(load);
  return <div className="learn-page reading-workspace">
    <button type="button" className="focus-button-quiet" onClick={onBack}>← 返回文章列表</button>
    {(state.status === 'idle' || state.status === 'loading') && <p role="status">正在读取文章…</p>}
    {state.status === 'error' && <section className="focus-card">
      <h1 data-workspace-heading tabIndex={-1}>阅读文章</h1>
      <p role="alert">{loadMessage(state.error)}</p>
      <button type="button" className="focus-button-secondary" onClick={() => void retry()}>重新加载文章</button>
    </section>}
    {state.status === 'success' && <Exercise initial={state.data} onCompleted={onCompleted} />}
  </div>;
}

function Exercise({ initial, onCompleted }: { initial: ReadingArticleView; onCompleted: (result: ReadingResult) => void }) {
  const { article } = initial;
  const [answers, setAnswers] = useState(() => initial.result ? {} : restoreDraft(article));
  const [result, setResult] = useState(initial.result);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [storageFailed, setStorageFailed] = useState(false);
  const submitGate = useRef(false);
  const title = useRef<HTMLHeadingElement>(null);
  const resultTitle = useRef<HTMLHeadingElement>(null);
  const numbered = article.sections.flatMap(section => section.items);
  const answered = numbered.filter(item => answers[item.id]).length;
  const results = new Map(result?.items.map(item => [item.item_id, item]));

  useEffect(() => { title.current?.focus({ preventScroll: true }); }, []);

  const choose = (item: ReadingItem, choice: string) => {
    if (submitGate.current || result) return;
    const next = { ...answers };
    if (choice) next[item.id] = choice;
    else delete next[item.id];
    setAnswers(next);
    setStorageFailed(!store(draftKey(article.id), Object.keys(next).length ? JSON.stringify(next) : null));
    setError('');
  };

  const submit = async () => {
    if (submitGate.current || result || answered !== numbered.length) return;
    submitGate.current = true;
    setBusy(true);
    setError('');
    try {
      const next = await readingApi.submit(article.id, learner, answers).catch(async (cause: unknown) => {
        if (cause instanceof ApiClientError && (cause.status === 409 || cause.retryable)) {
          const current = await readingApi.get(article.id, learner).catch(() => null);
          if (current?.result) return current;
        }
        throw cause;
      });
      if (!next.result) throw new Error('暂时无法确认提交结果。');
      store(draftKey(article.id), null);
      setResult(next.result);
      onCompleted(next.result);
      requestAnimationFrame(() => resultTitle.current?.focus());
    } catch (cause) {
      setError(cause instanceof ApiClientError && cause.retryable
        ? '暂时无法确认提交结果。已保留选择，可重新提交。'
        : cause instanceof Error ? cause.message : '提交失败，已保留选择。');
    } finally {
      setBusy(false);
      submitGate.current = false;
    }
  };

  return <>
    <header className="focus-page-header reading-header">
      <p className="reading-meta">{article.theme} · {difficultyLabels[article.difficulty]} · {article.question_count} 题</p>
      <h1 lang="en" data-workspace-heading tabIndex={-1} ref={title}>{article.title}</h1>
      <p lang="en">{article.summary}</p>
      <a href="#reading-questions" className="reading-jump">跳至题目 ↓</a>
    </header>
    {result && <section className="focus-card reading-result">
      <h2 ref={resultTitle} tabIndex={-1}>已提交 · {result.correct_count}/{result.total_count} 题正确</h2>
      <p>得分仅反映本篇练习。下方可逐题查看解释与原文依据。</p>
    </section>}
    <div className="reading-exercise">
      <article className="focus-card reading-passage" aria-label="英文原文">
        <h2>英文原文</h2>
        <div lang="en">
          {article.paragraphs.map(paragraph => <p key={paragraph.id} id={`reading-paragraph-${paragraph.id}`}>
            <strong>{paragraph.id}</strong>{paragraph.text}
          </p>)}
        </div>
      </article>
      <form id="reading-questions" className="focus-card reading-questions" onSubmit={event => {
        event.preventDefault();
        void submit();
      }}>
        <h2>{result ? '逐题解析' : '阅读题目'}</h2>
        {article.sections.map(section => <QuizSection key={section.type} section={section} answers={answers}
          results={results} locked={busy || !!result} onChoose={choose} />)}
        {!result && <div className="reading-submit">
          {storageFailed && <p role="alert">浏览器无法保存草稿，请在离开前提交或记下选项。</p>}
          {error && <p role="alert">{error}</p>}
          <span role="status">已作答 {answered}/{numbered.length}</span>
          <button type="submit" className="focus-button" disabled={busy || answered !== numbered.length}>
            {busy ? '提交中…' : '提交答案'}
          </button>
        </div>}
      </form>
    </div>
  </>;
}

function QuizSection({ section, answers, results, locked, onChoose }: {
  section: ReadingSection; answers: Record<string, string>; results: Map<string, ReadingItemResult>;
  locked: boolean; onChoose: (item: ReadingItem, choice: string) => void;
}) {
  const headings = section.type === 'matching_headings';
  return <section className="reading-question-group">
    <h3 lang="en">{sectionLabels[section.type]}</h3>
    <p lang="en">{section.instructions}</p>
    {headings && !results.size && <ul lang="en" className="reading-headings">
      {section.items[0]?.options.map(option => <li key={option.id}><b>{option.id}</b> {option.text}</li>)}
    </ul>}
    {section.items.map(item => {
      const feedback = results.get(item.id);
      const title = `${item.number}. ${item.prompt}`;
      if (feedback) return <div key={item.id} className="reading-question">
        <h4 lang="en">{title}</h4>
        <ReviewItem item={item} result={feedback} />
      </div>;
      const name = `reading-answer-${item.id}`;
      return <fieldset key={item.id} className="reading-question" disabled={locked} lang="en">
        <legend>{title}</legend>
        {headings ? <select aria-label={title} value={answers[item.id] ?? ''} onChange={event => onChoose(item, event.target.value)}>
          <option value="">Choose a heading</option>
          {item.options.map(option => <option key={option.id} value={option.id} disabled={section.items.some(other =>
            item.id !== other.id && answers[other.id] === option.id)}>{option.id}. {option.text}</option>)}
        </select> : item.options.map(option => <label className="reading-option" key={option.id}>
          <input type="radio" name={name} value={option.id} checked={answers[item.id] === option.id}
            onChange={() => onChoose(item, option.id)} />
          <span>{section.type === 'multiple_choice' ? `${option.id}. ` : ''}{option.text}</span>
        </label>)}
      </fieldset>;
    })}
  </section>;
}

function ReviewItem({ item, result }: { item: ReadingItem; result: ReadingItemResult }) {
  const label = (id: string) => item.options.find(option => option.id === id)?.text ?? id;
  return <div className="reading-feedback">
    <p className={result.is_correct ? 'reading-correct' : 'reading-incorrect'}>
      {result.is_correct ? '正确' : '需复核'} · 你的答案 <b lang="en">{label(result.selected_option)}</b>
    </p>
    {!result.is_correct && <p>正确答案 <b lang="en">{label(result.correct_option)}</b></p>}
    <p lang="en">{result.explanation}</p>
    <a href={`#reading-paragraph-${result.evidence.paragraph}`}>
      {result.correct_option === 'not_given' ? '相关原文' : '原文依据'} · 段落 {result.evidence.paragraph}
    </a>
    <blockquote lang="en">{result.evidence.quote}</blockquote>
  </div>;
}
