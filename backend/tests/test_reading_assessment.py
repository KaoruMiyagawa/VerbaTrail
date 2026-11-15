from sqlalchemy import select, update

from app.content.reading_initial_assessment import (
    PASSAGE, V1_QUESTIONS, V1_RULE_VERSION, V1_TASK_ID, V1_VERSION,
    V2_QUESTIONS, V2_RULE_VERSION, V2_TASK_ID, V2_VERSION,
)
from app.persistence.models import (
    AssessmentRun, AssessmentTask, Attempt, EvidenceValidityEvent,
    KnowledgeTopic, LearningSession, Question,
)

BASE = "/api/v1/assessment-runs"


def create(client, learner, key, version=None):
    payload = {"learner_id": learner, "idempotency_key": key}
    if version is not None:
        payload["assessment_version"] = version
    return client.post(BASE, json=payload).json()


def answers(questions, *, first=False, wrong=False):
    if first:
        return {q["id"]: q["options"][0] for q in questions}
    if wrong:
        return {q["id"]: next(option for option in q["options"] if option != q["answer"]) for q in questions}
    return {q["id"]: q["answer"] for q in questions}


def seed_in_progress_v1(session_factory, run_id, learner):
    """Recreate the persistent rows an A2 deployment can already contain."""
    with session_factory() as db:
        run = db.get(AssessmentRun, run_id)
        task = db.scalar(select(AssessmentTask).where(
            AssessmentTask.run_id == run_id, AssessmentTask.skill_id == "reading"))
        topic_id = "assessment-reading-initial-v1"
        topic = KnowledgeTopic(id=topic_id, slug=topic_id, domain_code="ielts_academic",
                               title="Legacy Reading Assessment", summary="Historical v1 task")
        db.add(topic)
        db.flush()
        for item in V1_QUESTIONS:
            db.add(Question(
                id=item["id"], topic_id=topic_id, question_role="initial",
                question_type="multiple_choice", prompt_text=item["prompt"],
                context_snippet=PASSAGE,
                standard_answer=item["answer"], answer_explanation=item["explanation"],
                evaluation_rubric={"accepted_answers": [item["answer"]], "content_version": V1_VERSION,
                                   "rule_version": V1_RULE_VERSION, "skill": item["skill"]},
                difficulty=2, version=1,
            ))
        session = LearningSession(learner_id=learner, topic_id=topic_id,
                                  exam_spec_version=V1_VERSION, status="active")
        db.add(session)
        db.flush()
        task.learning_session_id = session.id
        task.content_version = V1_VERSION
        task.status = "in_progress"
        db.commit()


def test_v2_answer_positions_and_end_to_end_scoring(client, session_factory):
    correct_positions = []
    for question in V2_QUESTIONS:
        assert question["answer"] in question["options"]
        correct_positions.append(question["options"].index(question["answer"]))
    assert correct_positions == [2, 0, 3, 1]
    assert len(set(correct_positions)) == 4

    # Selecting option 1 is only one correct response out of four in V2.
    learner = "reading-v2-position-check"
    run = create(client, learner, "v2-position-run")
    assert run["assessment_version"] == "initial-v2"
    path = f"{BASE}/{run['run_id']}/reading"
    started = client.post(path + "/start", params={"learner_id": learner}).json()
    assert started["task_version"] == V2_VERSION
    assert [q["question_id"] for q in started["questions"]] == [q["id"] for q in V2_QUESTIONS]
    assert all("correct_answer" not in q and "explanation" not in q for q in started["questions"])
    assert started["design_rationale"] is None
    assert "第 3、1、4、2" not in str(started)
    first_result = client.post(path + "/submit", json={
        "learner_id": learner, "answers": answers(V2_QUESTIONS, first=True),
        "idempotency_key": "v2-first-option",
    })
    assert first_result.status_code == 200
    assert first_result.json()["correct_count"] == 1
    assert [q["is_correct"] for q in first_result.json()["questions"]] == [False, True, False, False]
    retry = client.post(path + "/submit", json={
        "learner_id": learner, "answers": answers(V2_QUESTIONS, first=True),
        "idempotency_key": "v2-first-option-retry",
    })
    assert retry.json() == first_result.json()
    changed = client.post(path + "/submit", json={
        "learner_id": learner, "answers": answers(V2_QUESTIONS),
        "idempotency_key": "v2-changed-answers",
    })
    assert changed.status_code == 409
    with session_factory() as db:
        task = db.scalar(select(AssessmentTask).where(AssessmentTask.run_id == run["run_id"], AssessmentTask.skill_id == "reading"))
        attempts = db.scalars(select(Attempt).where(Attempt.session_id == task.learning_session_id)).all()
        assert len(attempts) == 4

    # A clean V2 learner can submit all four correct answers for a real 4/4.
    learner = "reading-v2-correct"
    run = create(client, learner, "v2-correct-run")
    path = f"{BASE}/{run['run_id']}/reading"
    client.post(path + "/start", params={"learner_id": learner})
    result = client.post(path + "/submit", json={
        "learner_id": learner, "answers": answers(V2_QUESTIONS), "idempotency_key": "v2-all-correct",
    })
    assert result.status_code == 200 and result.json()["correct_count"] == 4
    assert all(q["is_correct"] for q in result.json()["questions"])
    assert all(ref["question_id"].startswith("readgarden-v2-") for ref in result.json()["attempt_references"])

    # Fully incorrect responses are still stored as actual negative observations.
    learner = "reading-v2-incorrect"
    run = create(client, learner, "v2-incorrect-run")
    path = f"{BASE}/{run['run_id']}/reading"
    client.post(path + "/start", params={"learner_id": learner})
    wrong = client.post(path + "/submit", json={
        "learner_id": learner, "answers": answers(V2_QUESTIONS, wrong=True), "idempotency_key": "v2-all-wrong",
    })
    assert wrong.status_code == 200 and wrong.json()["correct_count"] == 0
    assert all(not q["is_correct"] for q in wrong.json()["questions"])
    with session_factory() as db:
        task = db.scalar(select(AssessmentTask).where(AssessmentTask.run_id == run["run_id"], AssessmentTask.skill_id == "reading"))
        attempts = db.scalars(select(Attempt).where(Attempt.session_id == task.learning_session_id)).all()
        assert len(attempts) == 4 and all(a.is_correct is False for a in attempts)
    model = client.get("/api/v1/learner-model", params={"learner_id": learner}).json()
    reading = next(skill for skill in model["skills"] if skill["skill_id"] == "reading")
    assert reading["assessment_status"] == "not_assessed"
    assert "initial_assessment" not in reading["evidence_levels"]


def test_v1_in_progress_and_history_are_version_pinned_and_biased(client, session_factory):
    assert [q["options"].index(q["answer"]) for q in V1_QUESTIONS] == [0, 0, 0, 0]
    learner = "reading-v1-compatibility"
    old_run = create(client, learner, "legacy-run", version="initial-v1")
    seed_in_progress_v1(session_factory, old_run["run_id"], learner)
    old_path = f"{BASE}/{old_run['run_id']}/reading"

    restored = client.get(old_path, params={"learner_id": learner}).json()
    assert restored["design_rationale"] is None
    assert "正确项全部位于第一" not in str(restored)
    assert restored["task_version"] == V1_VERSION
    assert restored["task_id"] == V1_TASK_ID
    assert [q["question_id"] for q in restored["questions"]] == [q["id"] for q in V1_QUESTIONS]
    assert client.post(old_path + "/start", params={"learner_id": learner}).json() == restored

    old_submit = client.post(old_path + "/submit", json={
        "learner_id": learner, "answers": answers(V1_QUESTIONS), "idempotency_key": "legacy-submit",
    })
    assert old_submit.status_code == 200 and old_submit.json()["correct_count"] == 4
    assert old_submit.json()["diagnostic_quality"] == "legacy_answer_position_biased"
    assert "固定选择首项" in old_submit.json()["design_rationale"]

    new_run = create(client, learner, "revised-run")
    new_path = f"{BASE}/{new_run['run_id']}/reading"
    new_start = client.post(new_path + "/start", params={"learner_id": learner}).json()
    assert new_start["task_version"] == V2_VERSION
    assert new_start["repeated_exposure"] is True
    new_result = client.post(new_path + "/submit", json={
        "learner_id": learner, "answers": answers(V2_QUESTIONS), "idempotency_key": "revised-submit",
    })
    assert new_result.status_code == 200 and new_result.json()["repeated_exposure"] is True

    # New IDs and versioned result rendering leave the old run's scores unchanged.
    old_again = client.get(old_path, params={"learner_id": learner}).json()
    assert old_again["task_version"] == V1_VERSION
    assert old_again["correct_count"] == 4
    assert [q["question_id"] for q in old_again["questions"]] == [q["id"] for q in V1_QUESTIONS]
    assert {q["question_id"] for q in old_again["questions"]}.isdisjoint({q["id"] for q in V2_QUESTIONS})

    model = client.get("/api/v1/learner-model", params={"learner_id": learner}).json()
    reading = next(skill for skill in model["skills"] if skill["skill_id"] == "reading")
    legacy = [e for e in reading["evidence"] if e["content_version"] == V1_VERSION]
    assert len(legacy) == 4
    assert reading["assessment_status"] == "not_assessed"
    assert "initial_assessment" not in reading["evidence_levels"]
    assert all(e["evidence_type"] == "legacy_assessment_position_biased" for e in legacy)
    assert all(e["assessment_status"] == "not_assessed" for e in legacy)
    assert all("固定选择首项" in e["observation"] for e in legacy)
    assert all(e["evidence_type"] == "repeated_assessment" and e["assessment_status"] == "not_assessed"
               for e in reading["evidence"] if e["content_version"] == V2_VERSION)


def test_repeat_exposure_validity_and_learner_model_boundaries(client, session_factory):
    learner = "reading-validity-and-repeat"
    run = create(client, learner, "first-valid-run")
    path = f"{BASE}/{run['run_id']}/reading"
    client.post(path + "/start", params={"learner_id": learner})
    body = {"learner_id": learner, "answers": answers(V2_QUESTIONS), "idempotency_key": "first-valid-submit"}
    result = client.post(path + "/submit", json=body).json()
    with session_factory() as db:
        task = db.scalar(select(AssessmentTask).where(AssessmentTask.run_id == run["run_id"], AssessmentTask.skill_id == "reading"))
        attempts = db.scalars(select(Attempt).where(Attempt.session_id == task.learning_session_id).order_by(Attempt.submitted_at)).all()
        assert task.status == "completed" and task.content_version == V2_VERSION
        assert task.evidence_reference == attempts[-1].id
        db.add(EvidenceValidityEvent(target_type="attempt", target_id=attempts[0].id, status="invalidated", reason="test invalidation", source="test", idempotency_key="v2-invalidated"))
        db.add(EvidenceValidityEvent(target_type="attempt", target_id=attempts[1].id, status="review_required", reason="test review", source="test", idempotency_key="v2-review-required"))
        db.commit()
    model = client.get("/api/v1/learner-model", params={"learner_id": learner}).json()
    reading = next(skill for skill in model["skills"] if skill["skill_id"] == "reading")
    by_ref = {e["evidence_reference"]: e for e in reading["evidence"]}
    assert by_ref[attempts[0].id]["evidence_validity"] == "invalidated"
    assert by_ref[attempts[1].id]["evidence_validity"] == "review_required"
    assert by_ref[attempts[0].id]["assessment_status"] == "not_assessed"
    assert by_ref[attempts[1].id]["assessment_status"] == "not_assessed"

    second = create(client, learner, "second-valid-run")
    second_path = f"{BASE}/{second['run_id']}/reading"
    repeated = client.post(second_path + "/start", params={"learner_id": learner}).json()
    assert repeated["repeated_exposure"] is True
    assert client.post(second_path + "/submit", json=body | {"idempotency_key": "second-valid-submit"}).status_code == 200
    assert client.get(BASE, params={"learner_id": learner}).status_code == 200
    assert client.get(path, params={"learner_id": "another-learner"}).status_code == 404
    assert client.post(path + "/submit", json=body | {"learner_id": "another-learner"}).status_code == 404


def test_unknown_task_content_version_conflicts(client, session_factory):
    learner = "reading-version-conflict"
    run = create(client, learner, "run-version")
    path = f"{BASE}/{run['run_id']}/reading"
    assert client.post(path + "/start", params={"learner_id": learner}).status_code == 200
    with session_factory() as db:
        db.execute(update(AssessmentTask).where(
            AssessmentTask.run_id == run["run_id"], AssessmentTask.skill_id == "reading"
        ).values(content_version="retired-version"))
        db.commit()
    assert client.get(path, params={"learner_id": learner}).status_code == 409
    assert client.post(path + "/start", params={"learner_id": learner}).status_code == 409
    payload = {"learner_id": learner, "answers": answers(V2_QUESTIONS), "idempotency_key": "version-conflict-submit"}
    assert client.post(path + "/submit", json=payload).status_code == 409
    with session_factory() as db:
        db.execute(update(AssessmentTask).where(
            AssessmentTask.run_id == run["run_id"], AssessmentTask.skill_id == "reading"
        ).values(content_version=None))
        db.commit()
    assert client.get(path, params={"learner_id": learner}).status_code == 409
    assert client.post(path + "/start", params={"learner_id": learner}).status_code == 409


def test_unstarted_v1_does_not_silently_switch_to_v2(client, migrated_engine):
    learner = "legacy-unstarted"
    run = create(client, learner, "legacy-unstarted-key", version="initial-v1")
    path = f"{BASE}/{run['run_id']}/reading"
    for response in (client.get(path, params={"learner_id": learner}),
                     client.post(path + "/start", params={"learner_id": learner})):
        assert response.status_code == 409
        assert "older unstarted" in response.json()["message"]
