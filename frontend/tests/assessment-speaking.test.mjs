import assert from 'node:assert/strict';
import test from 'node:test';
import { chooseRecording, registerAssessmentRecording, SpeakingRegistrationConflict } from '../src/learn/assessmentSpeakingState.ts';

const recording = {
  runId: 'run-a', blob: new Blob(['test recording']), registration: 'local',
  metadata: {
    learner_id: 'learner-a', recording_id: 'recording-a', task_version: 'task-v1',
    prompt_id: 'prompt', prompt_version: 'prompt-v1', idempotency_key: 'retry-key-a',
    duration_seconds: 2.3, recorded_at: '2026-11-17T03:00:00.300Z',
  },
};
const completed = {
  assessment_run_id: 'run-a', learner_id: 'learner-a', task_version: 'task-v1',
  prompt: { prompt_id: 'prompt', prompt_version: 'prompt-v1' }, task_status: 'completed',
  metadata_status: 'registered', recording: {
    recording_id: 'recording-a', duration_seconds: 2.3, recorded_at: '2026-11-17T03:00:00.300000Z',
  },
};
const pending = { ...recording, registration: 'pending' };

test('commits the pending payload before POST and reconciles a lost response with GET', async () => {
  const calls = [];
  const result = await registerAssessmentRecording(recording,
    async () => { calls.push('GET'); return completed; },
    async metadata => { calls.push('POST'); assert.deepEqual(metadata, recording.metadata); throw Error('lost'); },
    async item => { calls.push('commit'); assert.deepEqual(item, pending); },
  );
  assert.equal(result, completed);
  assert.deepEqual(calls, ['commit', 'POST', 'GET']);
});

test('retry reads first, never overwrites a conflicting registration and reuses the original payload if still open', async () => {
  const calls = [];
  const write = async metadata => { calls.push('POST'); assert.deepEqual(metadata, recording.metadata); return completed; };
  const persist = async () => assert.fail('retry must not replace the persisted payload');
  assert.equal(await registerAssessmentRecording(pending, async () => completed, write, persist), completed);
  await assert.rejects(registerAssessmentRecording(pending, async () => ({
    ...completed, recording: { ...completed.recording, duration_seconds: 9 },
  }), write, persist), SpeakingRegistrationConflict);
  await assert.rejects(registerAssessmentRecording(pending, async () => { throw Error('offline'); }, write, persist), /offline/);
  assert.deepEqual(calls, []);
  assert.equal(await registerAssessmentRecording(pending, async () => {
    calls.push('GET'); return { ...completed, task_status: 'in_progress' };
  }, write, persist), completed);
  assert.deepEqual(calls, ['GET', 'POST']);
});

test('failed local save and empty audio both prevent POST', async () => {
  const forbidden = async () => assert.fail('network or storage action should not run');
  await assert.rejects(registerAssessmentRecording(recording, forbidden, forbidden,
    async () => { throw Error('quota'); }), /quota/);
  await assert.rejects(registerAssessmentRecording({ ...recording, blob: new Blob() }, forbidden, forbidden, forbidden), /录音为空/);
});

test('a completed task only offers matching nonempty local audio; an open task prefers pending retries', () => {
  const wrongPrompt = { ...recording, metadata: { ...recording.metadata, prompt_version: 'other' } };
  const otherRun = { ...recording, runId: 'run-b' };
  const empty = { ...recording, blob: new Blob() };
  assert.equal(chooseRecording(completed, [wrongPrompt, otherRun, empty]), undefined);
  assert.equal(chooseRecording(completed, [wrongPrompt, pending]), pending);
  assert.equal(chooseRecording({ ...completed, task_status: 'in_progress' }, [recording, pending]), pending);
});
