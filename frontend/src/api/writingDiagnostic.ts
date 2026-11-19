import { apiRequest } from './client';

export interface WritingDiagnosticEvidence {
  id: string;
  paragraph_number: number;
  source_reference: {
    submission_id: string;
    body_sha256: string;
    task_version: string;
    start: number;
    end: number;
    quote: string;
    coordinate_system: 'utf16_code_units';
  };
}

type HypothesisStatus = 'candidate' | 'supported' | 'lowered' | 'ruled_out' | 'insufficient_evidence';
type EvidenceRelation = 'supports' | 'contradicts' | 'neutral';

export interface WritingDiagnosticOutcome {
  method: 'structured_model' | 'deterministic_rules';
  method_version: string;
  fallback_reason: string | null;
  uncertainty_level: 'insufficient_evidence';
  argument_analysis: {
    aspect: 'position' | 'reasons' | 'reasoning_link';
    interpretation: string;
    evidence_ids: string[];
    assessment?: 'candidate' | 'insufficient_evidence';
    excerpt?: string | null;
  }[];
  hypotheses: {
    id: string;
    statement: string;
    status: HypothesisStatus;
    unresolved_question: string;
    evidence_links: { evidence_id: string; relation: EvidenceRelation; reason: string; excerpt?: string | null }[];
  }[];
  followup: {
    question: string;
    hypothesis_ids: string[];
    evidence_ids: string[];
    discriminating_reason: string;
  } | null;
  decision_reason: string;
  unresolved_uncertainties: string[];
  training_suggestion: { hypothesis_ids: string[]; suggestion: string } | null;
}

export interface WritingDiagnosticDecision {
  id: string;
  phase: 'initial' | 'followup';
  state: 'processing' | 'completed';
  allow_model: boolean;
  idempotency_key: string;
  recover_after: string;
  recoverable: boolean;
  answer: { evidence_id: string; question_id: string; body: string; body_sha256: string } | null;
  outcome: WritingDiagnosticOutcome | null;
  planned_model: { provider_name: string; model_name: string; prompt_version: string } | null;
  model_call: {
    provider_name: string;
    model_name: string;
    prompt_version: string;
    status: string;
    error_code: string | null;
  } | null;
}

export interface WritingDiagnosticSession {
  state: 'not_started' | 'processing' | 'awaiting_answer' | 'stopped';
  model_available: boolean;
  token_budget: number;
  tokens_charged: number;
  tokens_reported: number;
  tokens_pending_reservation: number;
  max_answer_characters: number;
  initial: WritingDiagnosticDecision | null;
  followup: WritingDiagnosticDecision | null;
  evidence: WritingDiagnosticEvidence[];
  verified_conclusion: null;
}

interface StartRequest { idempotency_key: string; allow_model: boolean; recover_decision_id?: string }
interface AnswerRequest extends StartRequest { question_id: string; body: string }

export type WritingDiagnosticIntent =
  | { kind: 'start'; payload: StartRequest }
  | { kind: 'answer'; payload: AnswerRequest };

const diagnosticPath = (run: string, learner: string, action: string) =>
  `/api/v1/assessment-runs/${encodeURIComponent(run)}/writing/diagnostic/${action}?learner_id=${encodeURIComponent(learner)}`;

export const readWritingDiagnostic = (run: string, learner: string, signal?: AbortSignal) =>
  apiRequest<WritingDiagnosticSession>(diagnosticPath(run, learner, 'session'), { signal });

export const advanceWritingDiagnostic = (run: string, learner: string, intent: WritingDiagnosticIntent) =>
  apiRequest<WritingDiagnosticSession>(diagnosticPath(run, learner, intent.kind), {
    method: 'POST', body: JSON.stringify(intent.payload),
  }, 45_000);
