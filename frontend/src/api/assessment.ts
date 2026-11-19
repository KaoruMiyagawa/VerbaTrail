import { apiRequest } from './client';

export type AssessmentRun = { run_id: string; assessment_version: string; created_at: string; updated_at: string; tasks: {skill_id: string; status: string}[] };
export type ReadingQuestion = {question_id: string; prompt: string; options: string[]; answer?: string; correct_answer?: string; explanation?: string; is_correct?: boolean; evidence_validity?: string};
export type ReadingAssessment = {task_status: string; task_version: string; repeated_exposure: boolean; evidence_insufficient: boolean; diagnostic_quality: string; passage: string | null; questions: ReadingQuestion[]; correct_count: number | null; limitations: string[]};
export type ListeningAssessment = Omit<ReadingAssessment, 'passage' | 'diagnostic_quality' | 'task_version'> & {
  assessment_run_id: string; learner_id: string; skill_id: 'listening'; task_id: string | null;
  task_version: string | null; rule_version: string | null; assessment_version: string;
  learning_session_id: string | null; audio_url: string | null; audio_sha256: string | null;
  attempt_references: {attempt_id: string; question_id: string; evidence_validity: string}[];
  unassessed_capabilities: string[]; observation?: string;
};
export interface SpeakingAssessmentMetadata {
  learner_id: string;
  recording_id: string;
  task_version: string;
  prompt_id: string;
  prompt_version: string;
  duration_seconds: number;
  recorded_at: string;
  idempotency_key: string;
}
export interface SpeakingAssessment {
  assessment_run_id: string;
  learner_id: string;
  assessment_version: string;
  skill_id: 'speaking';
  task_status: 'not_started' | 'in_progress' | 'skipped' | 'completed';
  task_version: string | null;
  prompt: {task_id: string; prompt_id: string; prompt_version: string; text: string;
    instructions: string; suggested_seconds: number; source: string} | null;
  repeated_exposure: boolean;
  metadata_status: 'not_registered' | 'registered';
  recording: {recording_id: string; duration_seconds: number; recorded_at: string;
    registered_at: string; provenance: 'client_reported'} | null;
  evidence_source: string | null;
  evidence_reference: string | null;
  evidence_validity: 'not_evaluated';
  audio_available_on_server: false;
  local_audio_availability: 'unknown';
  evaluation_status: 'not_assessed';
  limitations: string[];
}
export interface AssessmentWritingPrompt {
  task_id: string;
  task_version: string;
  prompt_id: string;
  prompt_version: string;
  text: string;
  instructions: string;
  suggested_words: number;
  max_characters: number;
  source: string;
  scope: string;
}
export interface AssessmentWritingDraft {
  body: string;
  version: number;
  updated_at: string;
}
export interface AssessmentWritingSubmission {
  submission_id: string;
  task_version: string;
  prompt_id: string;
  prompt_version: string;
  draft_version: number;
  body: string;
  body_sha256: string;
  word_count: number;
  paragraph_count: number;
  submitted_at: string;
  evidence_type: 'assessment_activity';
  source: 'assessment_writing_submission';
  evaluation_status: 'not_assessed';
  semantic_quality: 'not_assessed';
  ability_evidence_valid: false;
}
export interface AssessmentWriting {
  assessment_run_id: string;
  learner_id: string;
  assessment_version: string;
  skill_id: 'writing';
  task_status: 'not_started' | 'in_progress' | 'skipped' | 'completed';
  task_id: string | null;
  task_version: string | null;
  prompt: AssessmentWritingPrompt | null;
  draft: AssessmentWritingDraft | null;
  submission: AssessmentWritingSubmission | null;
  evidence_source: string | null;
  evidence_reference: string | null;
  limitations: string[];
}
export interface AssessmentWritingSubmitRequest {
  learner_id: string;
  body: string;
  version: number;
  task_version: string;
  prompt_id: string;
  prompt_version: string;
  idempotency_key: string;
}
const root = '/api/v1/assessment-runs';
const query = (learner: string) => `?learner_id=${encodeURIComponent(learner)}`;
const path = (run: string) => `${root}/${encodeURIComponent(run)}`;
export const assessmentApi = {
  speaking: (run: string, learner: string) => apiRequest<SpeakingAssessment>(path(run) + '/speaking' + query(learner)),
  startSpeaking: (run: string, learner: string) => apiRequest<SpeakingAssessment>(path(run) + '/speaking/start' + query(learner), {method:'POST'}),
  completeSpeaking: (run: string, metadata: SpeakingAssessmentMetadata) => apiRequest<SpeakingAssessment>(path(run) + '/speaking/complete', {method:'POST', body:JSON.stringify(metadata)}),
  listening: (run: string, learner: string) => apiRequest<ListeningAssessment>(path(run) + '/listening' + query(learner)),
  startListening: (run: string, learner: string) => apiRequest<ListeningAssessment>(path(run) + '/listening/start' + query(learner), {method:'POST'}),
  submitListening: (run: string, learner: string, answers: Record<string,string>, key: string) => apiRequest<ListeningAssessment>(path(run) + '/listening/submit', {method:'POST', body:JSON.stringify({learner_id:learner, answers, idempotency_key:key})}),
  history: (learner: string) => apiRequest<AssessmentRun[]>(root + query(learner)),
  run: (run: string, learner: string) => apiRequest<AssessmentRun>(path(run) + query(learner)),
  create: (learner: string, key: string) => apiRequest<AssessmentRun>(root, {method:'POST', body:JSON.stringify({learner_id:learner, assessment_version:'initial-v2', idempotency_key:key})}),
  reading: (run: string, learner: string) => apiRequest<ReadingAssessment>(path(run) + '/reading' + query(learner)),
  start: (run: string, learner: string) => apiRequest<ReadingAssessment>(path(run) + '/reading/start' + query(learner), {method:'POST'}),
  submit: (run: string, learner: string, answers: Record<string,string>, key: string) => apiRequest<ReadingAssessment>(path(run) + '/reading/submit', {method:'POST', body:JSON.stringify({learner_id:learner, answers, idempotency_key:key})}),
  writing: (run: string, learner: string, signal?: AbortSignal) => apiRequest<AssessmentWriting>(path(run) + '/writing' + query(learner), {signal}),
  startWriting: (run: string, learner: string) => apiRequest<AssessmentWriting>(path(run) + '/writing/start' + query(learner), {method:'POST'}),
  saveWritingDraft: (run: string, payload: Pick<AssessmentWritingSubmitRequest, 'learner_id' | 'body' | 'version'>) => apiRequest<AssessmentWritingDraft>(path(run) + '/writing/draft', {method:'PUT', body:JSON.stringify(payload)}),
  submitWriting: (run: string, payload: AssessmentWritingSubmitRequest) => apiRequest<AssessmentWriting>(path(run) + '/writing/submit', {method:'POST', body:JSON.stringify(payload)}),
};
