// Keep local edits scoped to the current tab and retain them across in-app navigation
// even when browser storage rejects writes.
const memory = new Map<string, { value: string | null; persistent: boolean }>();

function protectUnstoredText(event: BeforeUnloadEvent) {
  if (![...memory.values()].some(entry => !entry.persistent && entry.value !== null)) return;
  event.preventDefault();
  event.returnValue = '';
}

export function writingBufferIsPersistent(key: string): boolean {
  return memory.get(key)?.persistent ?? true;
}

export function readWritingBuffer(key: string, migrateLegacy = false): string | null {
  if (memory.has(key)) return memory.get(key)?.value ?? null;
  try {
    const saved = sessionStorage.getItem(key);
    if (saved !== null) return saved;
  } catch { /* Try the older storage location if applicable. */ }
  if (!migrateLegacy) return null;
  try {
    const legacy = localStorage.getItem(key);
    if (legacy !== null && writeWritingBuffer(key, legacy) && localStorage.getItem(key) === legacy) {
      localStorage.removeItem(key);
    }
    return legacy;
  } catch { return memory.get(key)?.value ?? null; }
}

export function writeWritingBuffer(key: string, value: string | null): boolean {
  let persistent = true;
  try {
    if (value === null) sessionStorage.removeItem(key);
    else sessionStorage.setItem(key, value);
  } catch { persistent = false; }
  memory.set(key, { value, persistent });
  // The listener must survive switching runs or leaving the Writing workspace.
  if (typeof window !== 'undefined') {
    if ([...memory.values()].some(entry => !entry.persistent && entry.value !== null)) {
      window.addEventListener('beforeunload', protectUnstoredText);
    } else {
      window.removeEventListener('beforeunload', protectUnstoredText);
    }
  }
  return persistent;
}
