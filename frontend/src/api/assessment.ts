import { apiRequest } from './client';

export type AssessmentRun = { run_id: string; assessment_version: string; created_at: string; tasks: {skill_id: string; status: string}[] };
export type ReadingQuestion = {question_id: string; prompt: string; options: string[]; answer?: string; correct_answer?: string; explanation?: string; is_correct?: boolean; evidence_validity?: string};
export type ReadingAssessment = {task_status: string; task_version: string; repeated_exposure: boolean; evidence_insufficient: boolean; diagnostic_quality: string; passage: string | null; questions: ReadingQuestion[]; correct_count: number | null; limitations: string[]};
const root = '/api/v1/assessment-runs';
const query = (learner: string) => `?learner_id=${encodeURIComponent(learner)}`;
const path = (run: string) => `${root}/${encodeURIComponent(run)}`;
export const assessmentApi = {
  history: (learner: string) => apiRequest<AssessmentRun[]>(root + query(learner)),
  run: (run: string, learner: string) => apiRequest<AssessmentRun>(path(run) + query(learner)),
  create: (learner: string, key: string) => apiRequest<AssessmentRun>(root, {method:'POST', body:JSON.stringify({learner_id:learner, assessment_version:'initial-v2', idempotency_key:key})}),
  reading: (run: string, learner: string) => apiRequest<ReadingAssessment>(path(run) + '/reading' + query(learner)),
  start: (run: string, learner: string) => apiRequest<ReadingAssessment>(path(run) + '/reading/start' + query(learner), {method:'POST'}),
  submit: (run: string, learner: string, answers: Record<string,string>, key: string) => apiRequest<ReadingAssessment>(path(run) + '/reading/submit', {method:'POST', body:JSON.stringify({learner_id:learner, answers, idempotency_key:key})}),
};
