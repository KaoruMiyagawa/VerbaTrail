import test from 'node:test';
import assert from 'node:assert/strict';
import {draftKey,loadDraft,submitWithRecovery} from '../src/learn/assessmentState.ts';
const storage=()=>{const m=new Map();return {getItem:k=>m.get(k)??null,setItem:(k,v)=>m.set(k,v)};};
test('drafts isolate learner run skill and version and migrate legacy Reading only',()=>{
 const s=storage();s.setItem(draftKey('a','r','reading','v'),JSON.stringify({q:'A'}));
 assert.deepEqual(loadDraft(s,'a','r','reading','v'),{q:'A'});
 for(const args of [['b','r','reading','v'],['a','s','reading','v'],['a','r','listening','v'],['a','r','reading','v2']])assert.deepEqual(loadDraft(s,...args),{});
 s.setItem('assessment:draft:a:old:v',JSON.stringify({q:'legacy'}));assert.deepEqual(loadDraft(s,'a','old','reading','v'),{q:'legacy'});assert.deepEqual(loadDraft(s,'a','old','listening','v'),{});
});
test('lost submit response restores server result without another write',async()=>{
 const calls=[];const result={task_status:'completed',correct_count:5};
 assert.equal(await submitWithRecovery(async()=>{calls.push('read');return result;},async()=>{calls.push('write');throw Error('lost');},false),result);assert.deepEqual(calls,['write','read']);
 calls.length=0;assert.equal(await submitWithRecovery(async()=>{calls.push('read');return result;},async()=>{calls.push('write');},true),result);assert.deepEqual(calls,['read']);
});
test('pending retry requires successful read before writing and keeps errors actionable',async()=>{
 let writes=0;await assert.rejects(submitWithRecovery(async()=>{throw Error('offline');},async()=>{writes++;},true),/offline/);assert.equal(writes,0);
 const events=[];await submitWithRecovery(async()=>{events.push('read');return {task_status:'in_progress'};},async()=>{events.push('write');return {task_status:'completed'};},true);assert.deepEqual(events,['read','write']);
});
test('corrupt local draft cannot replace server result or crash task loading',()=>{const s=storage();s.setItem(draftKey('a','r','listening','v'),'null');assert.deepEqual(loadDraft(s,'a','r','listening','v'),{});s.setItem(draftKey('a','r','listening','v'),'not json');assert.deepEqual(loadDraft(s,'a','r','listening','v'),{});});
