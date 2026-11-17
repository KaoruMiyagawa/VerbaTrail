import type { SpeakingAssessment, SpeakingAssessmentMetadata } from '../api/assessment';

export interface AssessmentRecording {
  runId: string;
  metadata: SpeakingAssessmentMetadata;
  blob: Blob;
  registration: 'local' | 'pending' | 'registered';
}

const DATABASE = 'verbatrail-speaking-assessment-v1';
const STORE = 'recordings';
const scope = ['metadata.learner_id', 'runId', 'metadata.task_version', 'metadata.prompt_id', 'metadata.prompt_version'];

function openDatabase(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(DATABASE, 1);
    let blocked = false;
    request.onupgradeneeded = () => {
      const store = request.result.createObjectStore(STORE, { keyPath: [...scope, 'metadata.recording_id'] });
      store.createIndex('task', scope);
    };
    request.onsuccess = () => blocked ? request.result.close() : resolve(request.result);
    request.onerror = () => reject(request.error);
    request.onblocked = () => { blocked = true; reject(new Error('IndexedDB is blocked')); };
  });
}

async function transaction<T>(mode: IDBTransactionMode, operation: (store: IDBObjectStore) => IDBRequest<T>): Promise<T> {
  const db = await openDatabase();
  try {
    return await new Promise<T>((resolve, reject) => {
      const tx = db.transaction(STORE, mode);
      const request = operation(tx.objectStore(STORE));
      tx.oncomplete = () => resolve(request.result);
      tx.onabort = () => reject(tx.error ?? new Error('IndexedDB transaction aborted'));
      tx.onerror = () => reject(tx.error);
    });
  } finally {
    db.close();
  }
}

export const saveAssessmentRecording = (recording: AssessmentRecording) =>
  transaction('readwrite', store => store.put(recording));

export async function listAssessmentRecordings(task: SpeakingAssessment): Promise<AssessmentRecording[]> {
  if (!task.task_version || !task.prompt) return [];
  const key = [task.learner_id, task.assessment_run_id, task.task_version, task.prompt.prompt_id, task.prompt.prompt_version];
  return transaction<AssessmentRecording[]>('readonly', store => store.index('task').getAll(key));
}
