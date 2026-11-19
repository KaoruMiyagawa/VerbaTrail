import type { AssessmentWritingSubmitRequest } from '../api/assessment';
import { readWritingBuffer, writeWritingBuffer } from './writingRecoveryStorage';

export interface WritingRecovery {
  body: string;
  baseVersion: number;
  taskVersion: string | null;
  pendingSubmission: AssessmentWritingSubmitRequest | null;
  savedAt: string;
}

export function writingRecoveryKey(learnerId: string, runId: string) {
  return `assessment:writing-recovery:v1:${encodeURIComponent(learnerId)}:${encodeURIComponent(runId)}`;
}

export function readWritingRecovery(key: string): WritingRecovery | null {
  try {
    const value: unknown = JSON.parse(readWritingBuffer(key, true) ?? 'null');
    if (!value || typeof value !== 'object') return null;
    const candidate = value as Partial<WritingRecovery>;
    if (typeof candidate.body !== 'string' || !Number.isSafeInteger(candidate.baseVersion) || candidate.baseVersion! < 0) return null;
    if (candidate.taskVersion != null && typeof candidate.taskVersion !== 'string') return null;
    const pending = candidate.pendingSubmission;
    const validPending = pending && typeof pending === 'object' && pending.body === candidate.body
      && pending.version === candidate.baseVersion && pending.task_version === candidate.taskVersion
      && typeof pending.learner_id === 'string' && typeof pending.idempotency_key === 'string'
      && typeof pending.prompt_id === 'string' && typeof pending.prompt_version === 'string';
    return {
      body: candidate.body,
      baseVersion: candidate.baseVersion!,
      taskVersion: candidate.taskVersion ?? null,
      pendingSubmission: validPending ? pending : null,
      savedAt: typeof candidate.savedAt === 'string' ? candidate.savedAt : '',
    };
  } catch {
    return null;
  }
}

export function writeWritingRecovery(key: string, recovery: WritingRecovery): boolean {
  return writeWritingBuffer(key, JSON.stringify(recovery));
}

export function clearWritingRecovery(key: string): boolean {
  return writeWritingBuffer(key, null);
}

export interface SourceReference {
  submission_id: string;
  body_sha256: string;
  coordinate_system: 'utf16_code_units';
  start: number;
  end: number;
  quote: string;
}

export interface TextObservation {
  id: string;
  kind: 'paragraph_segment';
  fact: string;
  source_reference: SourceReference;
  source_validity: 'exact_quote';
  response_performance: 'not_evaluated';
  ability_inference: 'not_supported';
}

const WORD = /[\p{L}\p{N}]+(?:['’\-][\p{L}\p{N}]+)*/gu;

/** Creates structural facts only; every observation quotes the exact submitted source range. */
export function observeSubmittedParagraphs(
  body: string,
  submissionId: string,
  bodySha256: string,
): TextObservation[] {
  const observations: TextObservation[] = [];
  const separator = /\n[^\S\n]*\n+/g;
  let cursor = 0;
  const add = (start: number, end: number) => {
    const raw = body.slice(start, end);
    const leading = raw.length - raw.trimStart().length;
    const quote = raw.trim();
    if (!quote) return;
    const quoteStart = start + leading;
    const quoteEnd = quoteStart + quote.length;
    const wordCount = [...quote.matchAll(WORD)].length;
    const ordinal = observations.length + 1;
    observations.push({
      id: `paragraph-${ordinal}`,
      kind: 'paragraph_segment',
      fact: `第 ${ordinal} 个空行分隔文本片段，包含 ${wordCount} 个可复现分词单元。`,
      source_reference: {
        submission_id: submissionId,
        body_sha256: bodySha256,
        coordinate_system: 'utf16_code_units',
        start: quoteStart,
        end: quoteEnd,
        quote,
      },
      source_validity: 'exact_quote',
      response_performance: 'not_evaluated',
      ability_inference: 'not_supported',
    });
  };
  for (const match of body.matchAll(separator)) {
    const start = match.index ?? cursor;
    add(cursor, start);
    cursor = start + match[0].length;
  }
  add(cursor, body.length);
  return observations;
}
