import { apiRequest } from './client';

export type WritingTransferTarget = 'position' | 'reasons' | 'reasoning_link';

export interface WritingTransfer {
  id: string;
  task: {
    task_id: string;
    content_version: string;
    catalog_version: string;
    material_id: string;
    aspect: WritingTransferTarget;
    title: string;
    material: string;
    prompt: string;
    source: string;
    max_characters: number;
  };
  assigned_at: string;
  repeated_exposure: boolean;
  draft: { body: string; version: number; updated_at: string } | null;
  response: {
    id: string;
    body: string;
    body_sha256: string;
    base_version: number;
    submitted_at: string;
    observation: {
      target: WritingTransferTarget;
      word_count: number;
      paragraph_count: number;
      repeated_exposure: boolean;
      evidence_validity: 'not_evaluated' | 'repeated_exposure';
      evaluation_method: 'deterministic_text_observation';
      evaluation_version: string;
      evaluation_status: 'not_assessed';
      completed: true;
      distinct_material_from_training: true;
      assessment_sufficient: false;
      ability_evidence_valid: false;
      ability_conclusion: null;
    };
    lineage: {
      original_submission_id: string;
      initial_diagnostic_id: string;
      followup_diagnostic_id: string | null;
      practice_id: string;
      practice_revision_id: string;
    };
  } | null;
}

interface DraftRequest { body: string; version: number }
export interface TransferSubmitRequest extends DraftRequest { idempotency_key: string }

const route = (run: string, learner: string, action = '') =>
  `/api/v1/assessment-runs/${encodeURIComponent(run)}/writing/transfer${action}?learner_id=${encodeURIComponent(learner)}`;

export const writingTransfer = {
  read: (run: string, learner: string, signal?: AbortSignal) =>
    apiRequest<WritingTransfer | null>(route(run, learner), { signal }),
  start: (run: string, learner: string) =>
    apiRequest<WritingTransfer>(route(run, learner, '/start'), { method: 'POST' }),
  save: (run: string, learner: string, payload: DraftRequest) =>
    apiRequest<WritingTransfer>(route(run, learner, '/draft'), { method: 'PUT', body: JSON.stringify(payload) }),
  submit: (run: string, learner: string, payload: TransferSubmitRequest) =>
    apiRequest<WritingTransfer>(route(run, learner, '/submit'), { method: 'POST', body: JSON.stringify(payload) }),
};
