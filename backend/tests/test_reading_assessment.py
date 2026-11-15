from datetime import datetime, timezone

from sqlalchemy import select, update
from app.persistence.models import AssessmentTask, Attempt, EvidenceValidityEvent, LearningSession

BASE='/api/v1/assessment-runs'

def create(client, learner, key):
    return client.post(BASE,json={'learner_id':learner,'idempotency_key':key}).json()

def test_reading_assessment_end_to_end_and_exposure(client,session_factory):
    learner='reading-assess-a2'
    run=create(client,learner,'run-one')
    path=f"{BASE}/{run['run_id']}/reading"
    assert client.get(path,params={'learner_id':learner}).json()['task_status']=='not_started'
    started=client.post(path+'/start',params={'learner_id':learner})
    assert started.status_code==200
    task=started.json()
    assert task['task_status']=='in_progress' and len(task['questions'])==4
    assert task['evidence_insufficient'] is True and task['correct_count'] is None
    assert all('correct_answer' not in q and 'explanation' not in q for q in task['questions'])
    again=client.post(path+'/start',params={'learner_id':learner}).json()
    assert again['learning_session_id']==task['learning_session_id']
    assert client.get(path,params={'learner_id':'stranger'}).status_code==404
    answers={q['question_id']:q['options'][0] for q in task['questions']}
    for q in task['questions']:
        answers[q['question_id']]=next((o for o in q['options'] if o=={'readgarden-q1':'Herbs for visitors without a rented plot','readgarden-q2':'To reduce water loss from the soil','readgarden-q3':'It included community participation as well as produce','readgarden-q4':'To make shared work and deliveries easier'}[q['question_id']]),q['options'][1])
    body={'learner_id':learner,'answers':answers,'idempotency_key':'reading-submit-first'}
    assert client.post(path+'/submit',json=body|{'learner_id':'stranger'}).status_code==404
    result=client.post(path+'/submit',json=body)
    assert result.status_code==200,result.text
    data=result.json()
    assert data['task_status']=='completed' and data['correct_count']==4
    assert len(data['attempt_references'])==4 and all(r['evidence_validity']=='eligible' for r in data['attempt_references'])
    assert data['evidence_insufficient'] and all(q['is_correct'] and q['explanation'] for q in data['questions'])
    assert client.post(path+'/submit',json=body).json()==data
    changed=dict(body); changed['answers']=dict(answers); changed['answers']['readgarden-q1']='Vegetables for the café'
    assert client.post(path+'/submit',json=changed).status_code==409
    assert client.post(path+'/start',params={'learner_id':learner}).json()['task_status']=='completed'
    assert client.get(path,params={'learner_id':'stranger'}).status_code==404
    with session_factory() as db:
        taskrow=db.scalar(select(AssessmentTask).where(AssessmentTask.run_id==run['run_id'],AssessmentTask.skill_id=='reading'))
        assert taskrow.status=='completed' and taskrow.evidence_reference==data['attempt_references'][-1]['attempt_id']
        attempts=db.scalars(select(Attempt).where(Attempt.session_id==taskrow.learning_session_id)).all()
        assert len(attempts)==4 and all(a.question_snapshot_json['content_version']==taskrow.content_version for a in attempts)
        db.add(EvidenceValidityEvent(target_type='attempt',target_id=attempts[0].id,status='invalidated',reason='test invalidation',source='test',idempotency_key='a2-invalid-reading'))
        db.add(EvidenceValidityEvent(target_type='attempt',target_id=attempts[1].id,status='review_required',reason='test review',source='test',idempotency_key='a2-review-reading'))
        db.commit()
    model=client.get('/api/v1/learner-model',params={'learner_id':learner}).json()
    reading=next(s for s in model['skills'] if s['skill_id']=='reading')
    assert 'initial_assessment' in reading['evidence_levels']
    assert all(e['evidence_type']=='initial_assessment' for e in reading['evidence'] if e['evidence_reference'] in {a.id for a in attempts})
    by_ref={e['evidence_reference']:e for e in reading['evidence']}
    assert by_ref[attempts[0].id]['evidence_validity']=='invalidated'
    assert by_ref[attempts[1].id]['evidence_validity']=='review_required'
    assert by_ref[attempts[0].id]['assessment_status']=='not_assessed'
    assert by_ref[attempts[1].id]['assessment_status']=='not_assessed'
    invalidated=client.get(path,params={'learner_id':learner}).json()
    assert 'invalidated' in {r['evidence_validity'] for r in invalidated['attempt_references']}
    second=create(client,learner,'run-two')
    secondpath=f"{BASE}/{second['run_id']}/reading"
    repeated=client.post(secondpath+'/start',params={'learner_id':learner}).json()
    assert repeated['repeated_exposure'] is True
    assert client.post(secondpath+'/submit',json=body|{'idempotency_key':'reading-submit-second'}).status_code==200
    history=client.get(BASE,params={'learner_id':learner}).json()
    assert len(history)==2

def test_reading_rejects_task_content_version_conflict(client,session_factory):
    learner='reading-version-conflict'
    run=create(client,learner,'run-version')
    path=f"{BASE}/{run['run_id']}/reading"
    assert client.post(path+'/start',params={'learner_id':learner}).status_code==200
    with session_factory() as db:
        db.execute(update(AssessmentTask).where(AssessmentTask.run_id==run['run_id'],AssessmentTask.skill_id=='reading').values(content_version='retired-version'))
        db.commit()
    assert client.get(path,params={'learner_id':learner}).status_code==409
    assert client.post(path+'/start',params={'learner_id':learner}).status_code==409
    body={'learner_id':learner,'answers':{f'readgarden-q{i}':'x' for i in range(1,5)},'idempotency_key':'version-conflict-submit'}
    assert client.post(path+'/submit',json=body).status_code==409
