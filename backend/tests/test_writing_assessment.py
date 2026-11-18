from hashlib import sha256

from sqlalchemy import func, select
from sqlalchemy import inspect as sqlalchemy_inspect
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext

from app.content.writing_initial_assessment import PROMPT, SUBMISSION_SOURCE, TASK_VERSION
from app.persistence.models import AssessmentRun, AssessmentTask, AssessmentWritingDraft, AssessmentWritingSubmission
from app.persistence.migrations_runner import upgrade_database
from app.persistence.database import create_db_engine
from app.persistence.database import Base

BASE = "/api/v1/assessment-runs"
ESSAY = ("Community gardens can bring neighbours together and provide fresh food. "
         "They also teach children where food comes from.\n\n"
         "Sports areas offer exercise and a place for teams to meet. This matters because "
         "regular activity can support health and give young people a positive shared interest.\n\n"
         "I would choose a garden where land is limited, since it can serve residents of different ages. "
         "A town could include a small open space nearby so that both needs receive attention.")


def make_run(client, learner="writer", key="run-write-0001", version="initial-v2"):
    response = client.post(BASE, json={"learner_id": learner, "assessment_version": version, "idempotency_key": key})
    assert response.status_code == 200, response.text
    return response.json()["run_id"]


def test_assessment_writing_draft_submit_replay_and_evidence(client, session_factory):
    run_id = make_run(client)
    path = f"{BASE}/{run_id}/writing"
    assert client.get(path, params={"learner_id": "writer"}).json()["task_status"] == "not_started"
    first = client.post(path + "/start", params={"learner_id": "writer"})
    assert first.status_code == 200
    started = first.json()
    assert started["task_version"] == TASK_VERSION
    assert started["prompt"]["prompt_id"] == PROMPT["prompt_id"]
    assert client.post(path + "/start", params={"learner_id": "writer"}).json() == started

    saved = client.put(path + "/draft", json={"learner_id": "writer", "version": 0, "body": "First draft."})
    assert saved.status_code == 200 and saved.json()["version"] == 1
    conflict = client.put(path + "/draft", json={"learner_id": "writer", "version": 0, "body": "stale overwrite"})
    assert conflict.status_code == 409

    payload = {"learner_id": "writer", "version": 1, "body": ESSAY,
               "task_version": TASK_VERSION, "prompt_id": PROMPT["prompt_id"],
               "prompt_version": PROMPT["prompt_version"], "idempotency_key": "essay-submit-0001"}
    submitted = client.post(path + "/submit", json=payload)
    assert submitted.status_code == 200, submitted.text
    evidence = submitted.json()["submission"]
    assert evidence["body"] == ESSAY
    assert evidence["body_sha256"] == sha256(ESSAY.encode()).hexdigest()
    assert evidence["semantic_quality"] == "not_assessed"
    assert evidence["ability_evidence_valid"] is False
    assert submitted.json()["task_status"] == "completed"
    assert client.post(path + "/submit", json=payload).json() == submitted.json()
    assert client.post(path + "/submit", json=payload | {"body": ESSAY + " Changed."}).status_code == 409
    assert client.post(path + "/submit", json=payload | {"version": 0}).status_code == 409
    assert client.post(path + "/submit", json=payload | {"idempotency_key": "essay-submit-other"}).status_code == 409
    assert client.put(path + "/draft", json={"learner_id": "writer", "version": 2, "body": "replacement"}).status_code == 409

    model = client.get("/api/v1/learner-model", params={"learner_id": "writer"}).json()
    writing = next(skill for skill in model["skills"] if skill["skill_id"] == "writing")
    record = next(e for e in writing["evidence"] if e["evidence_source"] == SUBMISSION_SOURCE)
    assert record["assessment_status"] == "not_assessed"
    assert record["evidence_type"] == "assessment_activity"
    assert record["evidence_validity"] == "not_evaluated"
    assert writing["assessment_status"] == "not_assessed"
    assert "band" not in record["observation"].lower()

    with session_factory() as db:
        task = db.scalar(select(AssessmentTask).where(AssessmentTask.run_id == run_id, AssessmentTask.skill_id == "writing"))
        assert task.status == "completed" and task.evidence_reference == evidence["submission_id"]
        assert db.scalar(select(func.count()).select_from(AssessmentWritingSubmission)) == 1


def test_writing_scope_separate_per_run_and_hidden_from_other_learners(client, migrated_engine):
    first_run = make_run(client, key="run-write-0002")
    second_run = make_run(client, key="run-write-0003")
    for run_id in (first_run, second_run):
        assert client.post(f"{BASE}/{run_id}/writing/start", params={"learner_id": "writer"}).status_code == 200
    first_path = f"{BASE}/{first_run}/writing"
    second_path = f"{BASE}/{second_run}/writing"
    client.put(first_path + "/draft", json={"learner_id": "writer", "version": 0, "body": "Run one only."})
    assert client.get(second_path, params={"learner_id": "writer"}).json()["draft"]["body"] == ""
    assert client.get(first_path, params={"learner_id": "someone-else"}).status_code == 404
    assert client.put(first_path + "/draft", json={"learner_id": "someone-else", "version": 0, "body": "intrude"}).status_code == 404


def test_writing_legacy_version_conflict_and_four_skill_independence(client, migrated_engine):
    run_id = make_run(client, learner="legacy-writer", key="run-write-old", version="initial-v1")
    path = f"{BASE}/{run_id}/writing"
    assert client.get(path, params={"learner_id": "legacy-writer"}).status_code == 409
    assert client.post(path + "/start", params={"learner_id": "legacy-writer"}).status_code == 409

    current = make_run(client, learner="independent", key="run-write-independent")
    for skill in ("reading", "listening", "speaking"):
        before = client.get(f"{BASE}/{current}/{skill}", params={"learner_id": "independent"})
        assert before.status_code == 200
    client.post(f"{BASE}/{current}/writing/start", params={"learner_id": "independent"})
    after = client.get(f"{BASE}/{current}", params={"learner_id": "independent"}).json()
    assert after["status"] == "in_progress"
    assert {task["skill_id"]: task["status"] for task in after["tasks"]} == {
        "listening": "not_started", "speaking": "not_started", "reading": "not_started", "writing": "in_progress",
    }


def test_assessment_writing_is_outside_ordinary_writing_sessions(client, session_factory):
    run_id = make_run(client, learner="ordinary-check", key="run-write-ordinary")
    writing_path = f"{BASE}/{run_id}/writing"
    client.post(writing_path + "/start", params={"learner_id": "ordinary-check"})
    ordinary = client.post("/api/v1/writing/sessions", json={"learner_id": "ordinary-check"})
    assert ordinary.status_code == 200
    assert client.get(writing_path, params={"learner_id": "ordinary-check"}).json()["draft"]["body"] == ""
    with session_factory() as db:
        assert db.scalar(select(func.count()).select_from(AssessmentWritingDraft)) == 1


def test_0012_to_0013_migration_and_orm_schema_alignment(test_settings):
    engine = create_db_engine(test_settings)
    try:
        upgrade_database(database_url=test_settings.database_url, engine=engine, revision="0012_speaking_assessment")
        assert "assessment_writing_submissions" not in sqlalchemy_inspect(engine).get_table_names()
        upgrade_database(database_url=test_settings.database_url, engine=engine)
        inspector = sqlalchemy_inspect(engine)
        assert {"assessment_writing_drafts", "assessment_writing_submissions"} <= set(inspector.get_table_names())
        assert {"assessment_task_id", "body", "version", "updated_at"} == {
            column["name"] for column in inspector.get_columns("assessment_writing_drafts")}
        assert any(index["name"] == "ix_assessment_writing_submissions_assessment_task_id"
                   for index in inspector.get_indexes("assessment_writing_submissions"))
        assert any(unique["name"] == "uq_assessment_writing_submission_task"
                   for unique in inspector.get_unique_constraints("assessment_writing_submissions"))
        with engine.connect() as connection:
            migration_context = MigrationContext.configure(connection, opts={
                "include_object": lambda obj, name, kind, reflected, compare_to: (
                    name.startswith("assessment_writing_") if kind == "table"
                    else getattr(getattr(obj, "table", None), "name", "").startswith("assessment_writing_")
                ),
            })
            assert compare_metadata(migration_context, Base.metadata) == []
    finally:
        engine.dispose()
