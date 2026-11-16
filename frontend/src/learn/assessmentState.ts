export type AssessmentSkill = 'reading' | 'listening';
export function draftKey(learner: string, run: string, skill: AssessmentSkill, version: string) {
  return `assessment:draft:${learner}:${run}:${skill}:${version}`;
}
export function loadDraft(storage: Storage, learner: string, run: string, skill: AssessmentSkill, version: string): Record<string,string> {
  try {
    // Preserve pre-B2 Reading drafts; Listening never reads this legacy key.
    const raw = storage.getItem(draftKey(learner,run,skill,version)) ?? (skill === 'reading' ? storage.getItem(`assessment:draft:${learner}:${run}:${version}`) : null);
    const value: unknown = JSON.parse(raw || '{}');
    return value && typeof value === 'object' && !Array.isArray(value) ? Object.fromEntries(Object.entries(value).filter(([,v])=>typeof v==='string')) : {};
  } catch { return {}; }
}
export async function submitWithRecovery<T extends {task_status:string}>(read:()=>Promise<T>, write:()=>Promise<T>, pending:boolean): Promise<T> {
  // A prior ambiguous submission must be reconciled before another write.
  if (pending) { const current=await read(); if(current.task_status==='completed') return current; }
  try { return await write(); }
  catch(error) { try { const current=await read(); if(current.task_status==='completed') return current; } catch { /* Keep the original failure and retry key. */ } throw error; }
}
