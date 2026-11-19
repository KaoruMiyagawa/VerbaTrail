import assert from 'node:assert/strict';
import { registerHooks } from 'node:module';
import test from 'node:test';

// Vite resolves this TS import in the application. Limit Node's shim to this module.
const hook = registerHooks({
  resolve(specifier, context, nextResolve) {
    if (specifier === './writingRecoveryStorage' && context.parentURL?.endsWith('/assessmentWritingState.ts')) {
      return nextResolve('./writingRecoveryStorage.ts', context);
    }
    return nextResolve(specifier, context);
  },
});
const {
  clearWritingRecovery, observeSubmittedParagraphs, readWritingRecovery, writeWritingRecovery, writingRecoveryKey,
} = await import('../src/learn/assessmentWritingState.ts').finally(() => hook.deregister());
const { readWritingBuffer, writingBufferIsPersistent } = await import('../src/learn/writingRecoveryStorage.ts');

function storage({ failWrites = false } = {}) {
  const entries = new Map();
  return {
    getItem: key => entries.get(key) ?? null,
    setItem(key, value) {
      if (failWrites) throw new DOMException('storage blocked', 'QuotaExceededError');
      entries.set(key, value);
    },
    removeItem: key => entries.delete(key),
  };
}

function useStorage(t, session = storage(), local = storage()) {
  const replacements = { sessionStorage: session, localStorage: local, window: new EventTarget() };
  for (const [name, value] of Object.entries(replacements)) {
    const original = Object.getOwnPropertyDescriptor(globalThis, name);
    Object.defineProperty(globalThis, name, { configurable: true, value });
    t.after(() => original ? Object.defineProperty(globalThis, name, original) : Reflect.deleteProperty(globalThis, name));
  }
  return replacements;
}

function recovery(body, version = 2) {
  return {
    body,
    baseVersion: version,
    taskVersion: 'writing-v1',
    savedAt: '2026-11-19T00:00:00.000Z',
    pendingSubmission: {
      learner_id: 'learner/one', body, version, task_version: 'writing-v1',
      prompt_id: 'prompt-one', prompt_version: 'one', idempotency_key: 'exact-original-request',
    },
  };
}

test('Writing recovery keeps the full pending request scoped to its learner and AssessmentRun', t => {
  const { sessionStorage } = useStorage(t);
  const key = writingRecoveryKey('learner/one', 'offline-run');
  const value = recovery('Use a shared garden 🌱.\n\nTalk regularly.');
  assert.equal(writeWritingRecovery(key, value), true);
  assert.deepEqual(JSON.parse(sessionStorage.getItem(key)), value);
  assert.deepEqual(readWritingRecovery(key), value);
  assert.equal(readWritingRecovery(writingRecoveryKey('learner/two', 'offline-run')), null);
  assert.equal(readWritingRecovery(writingRecoveryKey('learner/one', 'another-run')), null);
  assert.equal(clearWritingRecovery(key), true);
  assert.equal(readWritingRecovery(key), null);
});

test('Writing recovery migrates legacy storage and does not restore a conflicting pending request', t => {
  const { localStorage, sessionStorage } = useStorage(t);
  const key = writingRecoveryKey('learner/one', 'legacy-run');
  const value = recovery('Keep this text.');
  localStorage.setItem(key, JSON.stringify({
    ...value, pendingSubmission: { ...value.pendingSubmission, body: 'Conflicting body', version: 1 },
  }));
  assert.deepEqual(readWritingRecovery(key), { ...value, pendingSubmission: null });
  assert.equal(localStorage.getItem(key), null);
  assert.ok(sessionStorage.getItem(key));
  const malformed = writingRecoveryKey('learner/one', 'malformed-run');
  sessionStorage.setItem(malformed, '{"baseVersion":-1}');
  assert.equal(readWritingRecovery(malformed), null);
});

test('blocked storage retains in-tab text and protects navigation until the buffer is cleared', t => {
  const { window } = useStorage(t, storage({ failWrites: true }));
  const key = writingRecoveryKey('learner/one', 'blocked-run');
  const value = { ...recovery('Unsynced text'), pendingSubmission: null };
  function beforeUnload() {
    const event = new Event('beforeunload', { cancelable: true });
    Object.defineProperty(event, 'returnValue', { writable: true, value: '' });
    window.dispatchEvent(event);
    return event;
  }
  try {
    assert.equal(writeWritingRecovery(key, value), false);
    assert.equal(writingBufferIsPersistent(key), false);
    assert.deepEqual(readWritingRecovery(key), value);
    assert.equal(readWritingBuffer(writingRecoveryKey('learner/one', 'away-run')), null);
    assert.equal(beforeUnload().defaultPrevented, true);
  } finally {
    clearWritingRecovery(key);
  }
  assert.equal(beforeUnload().defaultPrevented, false);
});

test('Writing observations quote exact UTF-16 spans without awarding writing ability', () => {
  const body = '  A garden 🌱 supports cooperation.\n \n A cafe\u0301 offers a place to meet.  ';
  const evidence = observeSubmittedParagraphs(body, 'formal-submission', 'formal-hash');
  assert.equal(evidence.length, 2);
  for (const item of evidence) {
    const ref = item.source_reference;
    assert.equal(body.slice(ref.start, ref.end), ref.quote);
    assert.equal(ref.coordinate_system, 'utf16_code_units');
    assert.equal(ref.submission_id, 'formal-submission');
    assert.equal(ref.body_sha256, 'formal-hash');
    assert.equal(item.ability_inference, 'not_supported');
    assert.equal(item.response_performance, 'not_evaluated');
  }
  const nextStart = body.indexOf('A cafe');
  assert.equal(evidence[1].source_reference.start, nextStart);
  assert.equal(nextStart, [...body.slice(0, nextStart)].length + 1);
});
