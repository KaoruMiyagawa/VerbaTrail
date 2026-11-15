import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';

const workspace = await readFile(new URL('../src/learn/AssessmentWorkspace.tsx', import.meta.url), 'utf8');
const api = await readFile(new URL('../src/api/assessment.ts', import.meta.url), 'utf8');

test('assessment keeps browser drafts scoped by learner, run and content version', () => {
  assert.match(workspace, /assessment:draft:\$\{learner\}:\$\{run\}:\$\{version\}/);
  assert.match(workspace, /loadDraft\(id,task\.task_version\)/);
});

test('failed submit checks server state before returning control to the learner', () => {
  assert.match(workspace, /catch \(e\) \{ try \{ const result=await assessmentApi\.reading\(run\.run_id,learner\)/);
  assert.match(workspace, /result\.task_status==='completed'/);
  assert.match(workspace, /if \(gate\.current\) return/);
});

test('creation requests initial-v2 with a persistent retry key', () => {
  assert.match(api, /assessment_version:'initial-v2'/);
  assert.match(workspace, /localStorage\.getItem\(createKey\)/);
  assert.match(workspace, /localStorage\.removeItem\(createKey\)/);
});
