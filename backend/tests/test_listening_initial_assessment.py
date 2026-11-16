from concurrent.futures import ThreadPoolExecutor
import hashlib
import wave

from sqlalchemy import select

from app.content import listening_initial_assessment as content
from app.persistence.models import AssessmentRun, AssessmentTask, Attempt, EvidenceValidityEvent, LearningSession

BASE = "/api/v1/assessment-runs"


def create_run(client, learner, key, version="initial-v2"):
    response = client.post(BASE, json={"learner_id": learner, "assessment_version": version,
                                      "idempotency_key": key})
    assert response.status_code == 200
    return response.json()


def path_for(run):
    return f"{BASE}/{run['run_id']}/listening"


def correct_answers():
    return {item["id"]: item["answer"] for item in content.QUESTIONS}


def wrong_answers():
    return {item["id"]: next(option for option in item["options"] if option != item["answer"])
            for item in content.QUESTIONS}


def submit_body(learner, answers, key="listen-assessment-submit"):
    return {"learner_id": learner, "answers": answers, "idempotency_key": key}


def test_original_content_positions_and_audio_is_real_non_silent_wav():
    positions = []
    for item in content.QUESTIONS:
        assert item["answer"] in item["options"]
        positions.append(item["options"].index(item["answer"]) + 1)
    assert positions == [2, 4, 1, 3, 2, 4]
    assert {item["item_type"] for item in content.QUESTIONS} >= {
        "explicit_detail", "cause_effect", "action_inference", "speaker_purpose"
    }
    assert hashlib.sha256(content.AUDIO_PATH.read_bytes()).hexdigest().upper() == content.AUDIO_SHA256
    with wave.open(str(content.AUDIO_PATH), "rb") as audio:
        assert audio.getframerate() == 22050 and audio.getnchannels() == 1 and audio.getsampwidth() == 2
        assert audio.getnframes() / audio.getframerate() > 30
        data = audio.readframes(audio.getnframes())
        assert any(data)


def test_start_hides_answers_pins_versions_and_serves_audio(client, session_factory):
    learner = "listen-assessment-start"
    run = create_run(client, learner, "listen-start-run")
    path = path_for(run)
    before = client.get(path, params={"learner_id": learner})
    assert before.status_code == 200
    assert before.json()["task_status"] == "not_started"
    assert before.json()["audio_url"] is None and before.json()["questions"] == []

    started = client.post(path + "/start", params={"learner_id": learner})
    assert started.status_code == 200
    body = started.json()
    assert body["task_status"] == "in_progress"
    assert body["task_version"] == content.CONTENT_VERSION
    assert body["rule_version"] == content.RULE_VERSION
    assert body["audio_url"] == content.AUDIO_URL
    assert body["repeated_exposure"] is False
    for forbidden in (content.SCRIPT, "correct_answer", "explanation", "standard_answer", content.DESIGN_RATIONALE):
        assert forbidden not in str(body)
    assert all("answer" not in q and "explanation" not in q for q in body["questions"])

    audio = client.get(content.AUDIO_URL)
    assert audio.status_code == 200
    assert audio.headers["content-type"].startswith("audio/wav")
    assert hashlib.sha256(audio.content).hexdigest().upper() == content.AUDIO_SHA256

    again = client.post(path + "/start", params={"learner_id": learner})
    assert again.status_code == 200 and again.json() == body
    with session_factory() as db:
        task = db.scalar(select(AssessmentTask).where(AssessmentTask.run_id == run["run_id"], AssessmentTask.skill_id == "listening"))
        sessions = db.scalars(select(LearningSession).where(LearningSession.id == task.learning_session_id)).all()
        assert len(sessions) == 1 and task.content_version == content.CONTENT_VERSION
        assert db.scalars(select(Attempt).where(Attempt.session_id == task.learning_session_id)).all() == []


def test_scoring_attempts_idempotency_and_history_recovery(client, session_factory):
    learner = "listen-assessment-valid"
    run = create_run(client, learner, "listen-valid-run")
    path = path_for(run)
    client.post(path + "/start", params={"learner_id": learner})
    payload = submit_body(learner, correct_answers(), "listen-valid-submit")
    first = client.post(path + "/submit", json=payload)
    assert first.status_code == 200
    result = first.json()
    assert result["task_status"] == "completed" and result["correct_count"] == 6
    assert result["evidence_insufficient"] is True
    assert result["repeated_exposure"] is False
    assert all(q["is_correct"] and q["correct_answer"] == q["answer"] and q["explanation"] for q in result["questions"])
    assert all(ref["evidence_validity"] == "eligible" for ref in result["attempt_references"])
    assert result["unassessed_capabilities"] and "IELTS Band" in result["unassessed_capabilities"]
    assert client.post(path + "/submit", json=payload).json() == result
    changed = client.post(path + "/submit", json=submit_body(learner, wrong_answers(), "listen-changed-submit"))
    assert changed.status_code == 409
    assert client.post(path + "/start", params={"learner_id": learner}).json() == result
    assert client.get(path, params={"learner_id": learner}).json() == result

    with session_factory() as db:
        task = db.scalar(select(AssessmentTask).where(AssessmentTask.run_id == run["run_id"], AssessmentTask.skill_id == "listening"))
        attempts = db.scalars(select(Attempt).where(Attempt.session_id == task.learning_session_id)).all()
        persisted_run = db.get(AssessmentRun, run["run_id"])
        assert task.evidence_source == "attempt" and task.evidence_reference in {a.id for a in attempts}
        assert len(attempts) == 6
        assert all(a.evaluation_rule_version == content.RULE_VERSION and a.evaluation_status == "evaluated" for a in attempts)
        assert persisted_run.status == "in_progress"
        assert all(t.status != "completed" for t in persisted_run.tasks if t.skill_id != "listening")
        assert all(a.question_snapshot_json["audio_sha256"] == content.AUDIO_SHA256 for a in attempts)

    model = client.get("/api/v1/learner-model", params={"learner_id": learner}).json()
    listening = next(skill for skill in model["skills"] if skill["skill_id"] == "listening")
    assert listening["assessment_status"] == "observed"
    assert "initial_assessment" in listening["evidence_levels"]
    assert "practice_activity" not in listening["evidence_levels"]
    assert len([e for e in listening["evidence"] if e["evidence_type"] == "initial_assessment"]) == 6

    second_run = create_run(client, learner, "listen-second-run")
    second_path = path_for(second_run)
    second_start = client.post(second_path + "/start", params={"learner_id": learner}).json()
    assert second_start["repeated_exposure"] is True
    second_result = client.post(second_path + "/submit", json=submit_body(learner, wrong_answers(), "listen-second-submit")).json()
    assert second_result["correct_count"] == 0 and second_result["repeated_exposure"] is True
    model = client.get("/api/v1/learner-model", params={"learner_id": learner}).json()
    listening = next(skill for skill in model["skills"] if skill["skill_id"] == "listening")
    repeated = [e for e in listening["evidence"] if e["content_version"] == content.CONTENT_VERSION and e["evidence_type"] == "repeated_assessment"]
    assert len(repeated) == 6 and all(e["assessment_status"] == "not_assessed" for e in repeated)


def test_cross_learner_boundary_legacy_version_and_invalid_evidence(client, session_factory):
    learner = "listen-assessment-owner"
    run = create_run(client, learner, "listen-owner-run")
    path = path_for(run)
    assert client.get(path, params={"learner_id": "listen-other"}).status_code == 404
    assert client.post(path + "/start", params={"learner_id": "listen-other"}).status_code == 404
    assert client.post(path + "/submit", json=submit_body("listen-other", correct_answers())).status_code == 404

    legacy = create_run(client, learner, "listen-legacy-run", version="initial-v1")
    legacy_path = path_for(legacy)
    for response in (client.get(legacy_path, params={"learner_id": learner}),
                     client.post(legacy_path + "/start", params={"learner_id": learner}),
                     client.post(legacy_path + "/submit", json=submit_body(learner, correct_answers(), "listen-legacy-sub"))):
        assert response.status_code == 409
    with session_factory() as db:
        task = db.scalar(select(AssessmentTask).where(AssessmentTask.run_id == legacy["run_id"], AssessmentTask.skill_id == "listening"))
        assert task.status == "not_started" and task.content_version is None and task.learning_session_id is None

    client.post(path + "/start", params={"learner_id": learner})
    client.post(path + "/submit", json=submit_body(learner, correct_answers(), "listen-validity-submit"))
    with session_factory() as db:
        task = db.scalar(select(AssessmentTask).where(AssessmentTask.run_id == run["run_id"], AssessmentTask.skill_id == "listening"))
        attempts = db.scalars(select(Attempt).where(Attempt.session_id == task.learning_session_id)).all()
        db.add(EvidenceValidityEvent(target_type="attempt", target_id=attempts[0].id, status="invalidated", reason="test invalidation", source="test", idempotency_key="listen-invalidated"))
        db.add(EvidenceValidityEvent(target_type="attempt", target_id=attempts[1].id, status="review_required", reason="test review", source="test", idempotency_key="listen-review-required"))
        db.commit()
    restored = client.get(path, params={"learner_id": learner}).json()
    assert restored["questions"][0]["evidence_validity"] == "invalidated"
    assert restored["questions"][1]["evidence_validity"] == "review_required"
    model = client.get("/api/v1/learner-model", params={"learner_id": learner}).json()
    listening = next(skill for skill in model["skills"] if skill["skill_id"] == "listening")
    evidence = {item["evidence_reference"]: item for item in listening["evidence"]}
    for attempt in attempts[:2]:
        assert evidence[attempt.id]["assessment_status"] == "not_assessed"


def test_incorrect_answers_are_retained_and_concurrent_same_submit_is_idempotent(client, session_factory):
    learner = "listen-assessment-concurrent"
    run = create_run(client, learner, "listen-concurrent-run")
    path = path_for(run)
    client.post(path + "/start", params={"learner_id": learner})
    body = submit_body(learner, wrong_answers(), "listen-concurrent-submit")
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: client.post(path + "/submit", json=body), range(2)))
    assert all(response.status_code == 200 for response in responses), [(response.status_code, response.text) for response in responses]
    assert responses[0].json() == responses[1].json()
    assert responses[0].json()["correct_count"] == 0
    assert all(not question["is_correct"] for question in responses[0].json()["questions"])
    model = client.get("/api/v1/learner-model", params={"learner_id": learner}).json()
    listening = next(skill for skill in model["skills"] if skill["skill_id"] == "listening")
    assert listening["assessment_status"] == "not_assessed"
    assert all(e["assessment_status"] == "not_assessed" for e in listening["evidence"])
    with session_factory() as db:
        task = db.scalar(select(AssessmentTask).where(AssessmentTask.run_id == run["run_id"], AssessmentTask.skill_id == "listening"))
        assert len(db.scalars(select(Attempt).where(Attempt.session_id == task.learning_session_id)).all()) == 6
