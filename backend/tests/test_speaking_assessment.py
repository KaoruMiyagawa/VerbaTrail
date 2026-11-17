from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from sqlalchemy import func, inspect, select, text

from app.content.speaking_initial_assessment import PROMPT, RECORDING_SOURCE, TASK_VERSION
from app.persistence.database import create_db_engine
from app.persistence.migrations_runner import upgrade_database
from app.persistence.models import AssessmentTask, LearningSession, SpeakingAssessmentRecording

BASE = "/api/v1/assessment-runs"
LEARNER = "speaking-assessment-test"


def create_run(client, key, version="initial-v2", learner=LEARNER):
    response = client.post(BASE, json={
        "learner_id": learner, "assessment_version": version, "idempotency_key": key,
    })
    assert response.status_code == 200, response.text
    return f"{BASE}/{response.json()['run_id']}"


def metadata():
    return {
        "learner_id": LEARNER, "recording_id": "c1-test-recording", "task_version": TASK_VERSION,
        "prompt_id": PROMPT["prompt_id"], "prompt_version": PROMPT["prompt_version"],
        "duration_seconds": 42.5, "recorded_at": "2026-11-17T12:00:00+08:00",
        "idempotency_key": "c1-registration",
    }


def speaking_skill(client, learner=LEARNER):
    data = client.get("/api/v1/learner-model", params={"learner_id": learner}).json()
    return next(skill for skill in data["skills"] if skill["skill_id"] == "speaking")


def test_registration_and_learner_model_are_metadata_only(client, session_factory):
    params = {"learner_id": LEARNER}
    run = create_run(client, "first")
    task = run + "/speaking"
    initial_run = client.get(run, params=params).json()
    initial = client.get(task, params=params).json()
    assert initial["task_status"] == "not_started"
    assert initial["task_version"] is initial["prompt"] is initial["recording"] is None
    assert client.post(task + "/complete", json=metadata()).status_code == 409

    started = client.post(task + "/start", params=params).json()
    assert started["task_status"] == "in_progress"
    assert started["task_version"] == TASK_VERSION and started["prompt"] == PROMPT
    assert not started["repeated_exposure"] and started["recording"] is None
    assert client.post(task + "/start", params=params).json() == started
    assert speaking_skill(client)["evidence"] == []

    payload = metadata()
    response = client.post(task + "/complete", json=payload)
    assert response.status_code == 200, response.text
    completed = response.json()
    assert completed["task_status"] == "completed"
    assert completed["metadata_status"] == "registered"
    assert completed["recording"]["provenance"] == "client_reported"
    assert completed["recording"]["recorded_at"] == "2026-11-17T04:00:00Z"
    assert completed["evaluation_status"] == "not_assessed"
    assert completed["evidence_validity"] == "not_evaluated"
    assert completed["audio_available_on_server"] is False and completed["local_audio_availability"] == "unknown"
    assert completed["evidence_reference"] == payload["recording_id"]
    assert client.get(task, params=params).json() == completed
    assert client.post(task + "/start", params=params).json() == completed
    assert client.post(task + "/complete", json=payload).json() == completed
    assert client.post(task + "/complete", json=payload | {"duration_seconds": 12}).status_code == 409

    current_run = client.get(run, params=params).json()
    assert current_run["status"] == "in_progress" and current_run["completed_at"] is None
    assert [t for t in current_run["tasks"] if t["skill_id"] != "speaking"] == [
        t for t in initial_run["tasks"] if t["skill_id"] != "speaking"
    ]
    assert next(t for t in current_run["tasks"] if t["skill_id"] == "speaking") == {
        "skill_id": "speaking", "status": "completed", "evidence_source": RECORDING_SOURCE,
        "evidence_reference": payload["recording_id"],
    }
    assert client.get("/api/v1/speaking/practices", params=params).json() == []
    skill = speaking_skill(client)
    assert skill["has_learning_record"] and skill["assessment_status"] == "not_assessed"
    assert skill["evidence_levels"] == []
    assert len(skill["evidence"]) == 1
    evidence = skill["evidence"][0]
    assert evidence["evidence_type"] == "assessment_activity"
    assert evidence["evidence_reference"] == payload["recording_id"]
    assert evidence["assessment_status"] == "not_assessed" and evidence["evidence_validity"] == "not_evaluated"
    with session_factory() as db:
        assert db.scalar(select(func.count()).select_from(SpeakingAssessmentRecording)) == 1
        assert db.scalar(select(func.count()).select_from(LearningSession)) == 0


def test_scope_versions_and_recording_collision(client, migrated_engine):
    params = {"learner_id": LEARNER}
    task = create_run(client, "owner") + "/speaking"
    other = {"learner_id": "another"}
    assert client.get(task, params=other).status_code == 404
    assert client.post(task + "/start", params=other).status_code == 404
    assert client.post(task + "/complete", json=metadata() | other).status_code == 404
    assert client.post(task + "/start", params=params).status_code == 200
    for changes, status in [
        ({"audio": "unwanted-audio-data"}, 422), ({"transcript": "text"}, 422),
        ({"duration_seconds": 0}, 422), ({"duration_seconds": 601}, 422),
        ({"recorded_at": "2026-11-17T12:00:00"}, 422),
        ({"prompt_version": "different"}, 409), ({"task_version": "different"}, 409),
    ]:
        result = client.post(task + "/complete", json=metadata() | changes)
        assert result.status_code == status, result.text
        assert "unwanted-audio-data" not in result.text
    assert client.post(task + "/complete", json=metadata()).status_code == 200
    assert speaking_skill(client, "another")["evidence"] == []
    for learner in (LEARNER, "another"):
        next_task = create_run(client, "next", learner=learner) + "/speaking"
        result = client.post(next_task + "/start", params={"learner_id": learner})
        assert result.json()["repeated_exposure"] == (learner == LEARNER)
        assert client.post(next_task + "/complete", json=metadata() | {"learner_id": learner}).status_code == 409

    for version in ("initial-v1", "unknown-version"):
        run = create_run(client, version, version)
        for method, suffix in [("GET", ""), ("POST", "/start"), ("POST", "/complete")]:
            options = {"json": metadata()} if suffix == "/complete" else {"params": params}
            assert client.request(method, run + "/speaking" + suffix, **options).status_code == 409
        assert next(t for t in client.get(run, params=params).json()["tasks"] if t["skill_id"] == "speaking")["status"] == "not_started"


def test_missing_metadata_and_unknown_pinned_version_are_explicit(client, session_factory):
    params = {"learner_id": LEARNER}
    task = create_run(client, "missing-evidence") + "/speaking"
    client.post(task + "/start", params=params)
    assert client.post(task + "/complete", json=metadata()).status_code == 200
    with session_factory.begin() as db:
        db.delete(db.get(SpeakingAssessmentRecording, metadata()["recording_id"]))
    missing = client.get(task, params=params)
    assert missing.status_code == 409 and "metadata is missing" in missing.text
    assert speaking_skill(client)["evidence"] == []
    with session_factory.begin() as db:
        db.scalar(select(AssessmentTask).where(AssessmentTask.skill_id == "speaking")).content_version = "unavailable"
    conflict = client.post(task + "/start", params=params)
    assert conflict.status_code == 409 and "version is unavailable" in conflict.text


def test_concurrent_start_and_complete_register_once(client, session_factory):
    task = create_run(client, "concurrent") + "/speaking"
    for suffix, options in [
        ("/start", {"params": {"learner_id": LEARNER}}),
        ("/complete", {"json": metadata()}),
    ]:
        with ThreadPoolExecutor(max_workers=4) as pool:
            responses = list(pool.map(lambda _: client.post(task + suffix, **options), range(4)))
        assert all(response.status_code == 200 for response in responses)
        assert all(response.json() == responses[0].json() for response in responses)
    with session_factory() as db:
        assert db.scalar(select(func.count()).select_from(SpeakingAssessmentRecording)) == 1


def test_migration_from_0011_preserves_existing_task(test_settings):
    engine = create_db_engine(test_settings)
    try:
        upgrade_database(engine=engine, revision="0011_assessment_reading_task")
        assert "speaking_assessment_recordings" not in inspect(engine).get_table_names()
        now = datetime.now(timezone.utc)
        with engine.begin() as conn:
            conn.execute(text(
                "INSERT INTO assessment_runs VALUES "
                "('old-run', 'old-learner', 'initial-v1', 'old-key', 'in_progress', :now, :now, NULL)"
            ), {"now": now})
            conn.execute(text(
                "INSERT INTO assessment_tasks (id, run_id, skill_id, status) "
                "VALUES ('old-task', 'old-run', 'speaking', 'not_started')"
            ))
        upgrade_database(engine=engine)
        assert "speaking_assessment_recordings" in inspect(engine).get_table_names()
        with engine.connect() as conn:
            assert conn.execute(text("SELECT status, content_version FROM assessment_tasks")).one() == ("not_started", None)
    finally:
        engine.dispose()
