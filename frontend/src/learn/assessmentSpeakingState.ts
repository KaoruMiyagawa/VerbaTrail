import type { SpeakingAssessment, SpeakingAssessmentMetadata } from '../api/assessment';
import type { AssessmentRecording } from './assessmentSpeakingStorage';

export function matchesRegistration(task: SpeakingAssessment, item: AssessmentRecording) {
  const { metadata } = item;
  const saved = task.recording;
  return task.assessment_run_id === item.runId && task.learner_id === metadata.learner_id &&
    task.task_version === metadata.task_version && task.prompt?.prompt_id === metadata.prompt_id &&
    task.prompt.prompt_version === metadata.prompt_version && task.task_status === 'completed' &&
    task.metadata_status === 'registered' && saved?.recording_id === metadata.recording_id &&
    saved.duration_seconds === metadata.duration_seconds &&
    Date.parse(saved.recorded_at) === Date.parse(metadata.recorded_at);
}

export function chooseRecording(task: SpeakingAssessment, items: AssessmentRecording[]) {
  if (task.task_status === 'completed') {
    return items.find(item => item.blob.size > 0 && matchesRegistration(task, item));
  }
  const candidates = items.filter(item => item.registration !== 'registered' && item.blob.size > 0);
  candidates.sort((a, b) => b.metadata.recorded_at.localeCompare(a.metadata.recorded_at));
  return candidates.find(item => item.registration === 'pending') ?? candidates[0];
}

export class SpeakingRegistrationConflict extends Error {}

export async function registerAssessmentRecording(
  item: AssessmentRecording,
  read: () => Promise<SpeakingAssessment>,
  write: (metadata: SpeakingAssessmentMetadata) => Promise<SpeakingAssessment>,
  persist: (item: AssessmentRecording) => Promise<unknown>,
) {
  const confirm = (task: SpeakingAssessment) => {
    if (!matchesRegistration(task, item)) {
      throw new SpeakingRegistrationConflict('服务端登记与此处的本地录音不一致。请重新读取任务，保留本地录音。');
    }
    return task;
  };

  if (!item.blob.size) throw new Error('录音为空，请重新录制。');
  if (item.registration === 'pending') {
    const current = await read();
    if (current.task_status === 'completed') return confirm(current);
  } else {
    // The Blob is already stored. Commit the exact retry payload before the first POST.
    await persist({ ...item, registration: 'pending' });
  }

  let result: SpeakingAssessment;
  try {
    result = await write(item.metadata);
  } catch (error) {
    const current = await read().catch(() => null);
    if (current?.task_status === 'completed') return confirm(current);
    throw error;
  }
  return confirm(result);
}
