import { apiRequest } from './client';
import type { WritingDiagnosticEvidence } from './writingDiagnostic';

type Citation = { evidence_id: string; excerpt: string };
interface CandidateComparison {
  observation: string;
  original: Citation;
  revision: Citation;
}

export interface WritingPractice {
  id: string;
  plan: {
    version: string;
    evidence_version: string;
    decision_id: string;
    diagnostic_method: string;
    diagnostic_method_version: string;
    aspect: 'position' | 'reasons' | 'reasoning_link';
    mode: 'exploratory';
    title: string;
    task_prompt: string;
    instructions: string;
    selection_reason: string;
    new_evidence: string;
    evaluation_boundary: string;
    focus_hypothesis_id: string | null;
    hypotheses: { id: string; statement: string; diagnostic_status: string }[];
    unresolved_uncertainties: string[];
    anchor: WritingDiagnosticEvidence;
    diagnostic_excerpt: string | null;
  };
  draft: { body: string; version: number; updated_at: string };
  revision: {
    id: string;
    body: string;
    body_sha256: string;
    base_version: number;
    submitted_at: string;
    feedback: {
      method: 'deterministic_comparison' | 'structured_candidate';
      version: string;
      text_changed: boolean;
      observations: { metric: 'word_count' | 'code_point_count'; original: number; revision: number }[];
      changes: CandidateComparison[];
      hypothesis_evidence: {
        hypothesis_id: string; relation: 'supports' | 'contradicts' | 'neutral'; comparison: CandidateComparison;
      }[];
      unresolved_uncertainties: string[];
      next_step: 'more_practice' | 'independent_transfer' | 'insufficient_evidence';
      next_step_reason: string;
      capability_evidence_valid: false;
    };
  } | null;
  max_characters: number;
}

interface DraftRequest { body: string; version: number }
export interface SubmitRevision extends DraftRequest { idempotency_key: string }

const route = (run: string, learner: string, action = '') =>
  `/api/v1/assessment-runs/${encodeURIComponent(run)}/writing/training${action}?learner_id=${encodeURIComponent(learner)}`;

export const writingPractice = {
  read: (run: string, learner: string, signal?: AbortSignal) =>
    apiRequest<WritingPractice | null>(route(run, learner), { signal }),
  start: (run: string, learner: string) =>
    apiRequest<WritingPractice>(route(run, learner, '/start'), { method: 'POST' }),
  save: (run: string, learner: string, request: DraftRequest) =>
    apiRequest<WritingPractice>(route(run, learner, '/draft'), { method: 'PUT', body: JSON.stringify(request) }),
  submit: (run: string, learner: string, request: SubmitRevision) =>
    apiRequest<WritingPractice>(route(run, learner, '/submit'), { method: 'POST', body: JSON.stringify(request) }),
};
