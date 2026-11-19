"""SQLite migration, drift and destructive downgrade acceptance for Writing D4."""

from pathlib import Path

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
import pytest
from sqlalchemy import CheckConstraint, func, inspect, select
from sqlalchemy.exc import IntegrityError

from app.content.writing_initial_assessment import TASK_VERSION, PROMPT_ID, PROMPT_VERSION
from app.persistence.database import Base, create_db_engine
from app.persistence.migrations_runner import get_alembic_config, upgrade_database
from app.persistence.models import (
    AssessmentRun, AssessmentTask, AssessmentWritingDiagnosticDecision, AssessmentWritingPractice,
    AssessmentWritingPracticeRevision, AssessmentWritingSubmission, AssessmentWritingTransfer,
    AssessmentWritingTransferResponse, ModelCall,
)
from scripts.writing_d4_harness import ESSAY, TRANSFER, WritingRun, database_url, digest, scratch, server

D1, DIAGNOSTIC, PRACTICE, TRANSFER_REV = (
    "0013_writing_assessment", "0014_writing_diagnostic", "0015_writing_practice", "0016_writing_transfer",
)
DECISION = AssessmentWritingDiagnosticDecision.__table__
PLAN = AssessmentWritingPractice.__table__
REVISION = AssessmentWritingPracticeRevision.__table__
ASSIGNMENT = AssessmentWritingTransfer.__table__
RESPONSE = AssessmentWritingTransferResponse.__table__
ESSAYS = AssessmentWritingSubmission.__table__
AUDIT = ModelCall.__table__
STAGES = {
    DIAGNOSTIC: (DECISION,),
    PRACTICE: (PLAN, REVISION),
    TRANSFER_REV: (ASSIGNMENT, RESPONSE),
}


@pytest.fixture
def isolated_engine():
    with scratch() as directory:
        root = Path(directory)
        url = database_url(root)
        engine = create_db_engine(database_url=url)
        try:
            yield root, engine, url
        finally:
            engine.dispose()


def current(connection):
    return MigrationContext.configure(connection).get_current_revision()


def schema_differences(connection, tables):
    """Scope autogenerate to D4 and explicitly check SQLite details it may omit."""
    names = {table.name for table in tables}

    def include_object(obj, name, kind, reflected, compare_to):
        return (name if kind == "table" else obj.table.name) in names

    context = MigrationContext.configure(connection, opts={
        "include_object": include_object, "compare_type": True, "compare_server_default": True,
    })
    differences = [str(diff) for diff in compare_metadata(context, Base.metadata)]
    inspector = inspect(connection)
    for table in tables:
        if not inspector.has_table(table.name):
            continue
        checks = {item["name"]: " ".join(item["sqltext"].split())
                  for item in inspector.get_check_constraints(table.name)}
        expected_checks = {item.name: " ".join(str(item.sqltext).split())
                           for item in table.constraints if isinstance(item, CheckConstraint)}
        if checks != expected_checks:
            differences.append(f"{table.name} CHECK constraints differ: {checks} vs {expected_checks}")
        indexes = {item["name"]: item for item in inspector.get_indexes(table.name)}
        for index in table.indexes:
            expected = index.dialect_options["sqlite"].get("where")
            options = indexes.get(index.name, {}).get("dialect_options", {})
            actual = options.get("sqlite_where")
            if str(actual).lower() != str(expected).lower():
                differences.append(f"{table.name} {index.name} predicate: {actual} vs {expected}")
    return differences


def row_count(connection, table):
    return connection.scalar(select(func.count()).select_from(table))


def test_linear_upgrade_and_orm_parity(isolated_engine):
    _, engine, url = isolated_engine
    revisions = ScriptDirectory.from_config(get_alembic_config(url))
    assert revisions.get_heads() == [TRANSFER_REV]
    chain = (D1, *STAGES)
    for prior, next_revision in zip(chain, chain[1:]):
        assert revisions.get_revision(next_revision).down_revision == prior

    upgrade_database(database_url=url, engine=engine, revision=D1)
    with engine.begin() as connection:
        assert current(connection) == D1
        connection.execute(AssessmentRun.__table__.insert(), {
            "id": "historical-run", "learner_id": "historical-learner", "assessment_version": "initial-v2",
            "idempotency_key": "historical-run-key",
        })
        connection.execute(AssessmentTask.__table__.insert(), {
            "id": "historical-task", "run_id": "historical-run", "skill_id": "writing",
            "status": "completed", "content_version": TASK_VERSION,
            "evidence_source": "assessment_writing_submission", "evidence_reference": "historical-essay",
        })
        connection.execute(ESSAYS.insert(), {
            "id": "historical-essay", "assessment_task_id": "historical-task", "task_version": TASK_VERSION,
            "prompt_id": PROMPT_ID, "prompt_version": PROMPT_VERSION, "draft_version": 0,
            "body": ESSAY, "body_sha256": digest(ESSAY), "word_count": 17, "paragraph_count": 2,
            "idempotency_key": "historical-essay-key",
        })
    applied = []
    for index, (revision_id, tables) in enumerate(STAGES.items()):
        upgrade_database(database_url=url, engine=engine, revision=revision_id)
        applied.extend(tables)
        with engine.connect() as connection, engine.connect() as second:
            assert current(connection) == revision_id
            assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 1
            assert second.exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 1
            assert not connection.exec_driver_sql("PRAGMA foreign_key_check").all()
            differences = schema_differences(connection, applied)
            assert not differences, "\n".join(differences)
            assert connection.execute(select(ESSAYS.c.body, ESSAYS.c.body_sha256)).one() == (ESSAY, digest(ESSAY))
            for future in tuple(STAGES)[index + 1:]:
                assert all(not inspect(connection).has_table(table.name) for table in STAGES[future])


@pytest.mark.parametrize(("revision", "drift", "marker"), [
    (DIAGNOSTIC, "ALTER TABLE assessment_writing_diagnostic_decisions RENAME COLUMN required_initial_phase TO prior_phase",
     "required_initial_phase"),
    (PRACTICE, "DROP INDEX ix_writing_practice_decision_submission", "ix_writing_practice_decision_submission"),
    (TRANSFER_REV, "DROP INDEX uq_writing_transfer_first_exposure", "uq_writing_transfer_first_exposure"),
])
def test_same_version_stamp_does_not_prove_current_schema(isolated_engine, revision, drift, marker):
    _, engine, url = isolated_engine
    upgrade_database(database_url=url, engine=engine, revision=revision)
    with engine.begin() as connection:
        # Synthetic old-schema drift. The stamp is intentionally left intact.
        connection.exec_driver_sql(drift)
    upgrade_database(database_url=url, engine=engine, revision=revision)
    with engine.connect() as connection:
        assert current(connection) == revision
        differences = schema_differences(connection, STAGES[revision])
        assert any(marker in difference for difference in differences), differences
        print(f"{revision}: unchanged revision stamp, detected incompatible schema around {marker}")


def clone(connection, table, row_id, **changes):
    row = connection.execute(select(table).where(table.c.id == row_id)).mappings().one()
    connection.execute(table.insert(), dict(row) | changes)


def rejects(connection, table, row_id, kind, **changes):
    with pytest.raises(IntegrityError, match=kind), connection.begin_nested():
        clone(connection, table, row_id, **changes)


def test_foreign_keys_and_unique_constraints_reject_invalid_lineage(isolated_engine):
    root, engine, url = isolated_engine
    upgrade_database(database_url=url, engine=engine)
    with server(root) as (client, _):
        run = WritingRun.create(client)
        _, initial, answer, practice = run.finish_training()
        second_run = WritingRun.create(client, run.learner)
        _, _, _, other_practice = second_run.finish_training()
        third_run = WritingRun.create(client, run.learner)
        _, another_essay = third_run.submit_essay()
        transfer = run.call("POST", "/transfer/start")
        final = run.call("POST", "/transfer/submit", {
            "body": TRANSFER, "version": 0, "idempotency_key": "schema-transfer-response",
        })["response"]

    with engine.begin() as connection:
        inspector = inspect(connection)
        for table, name, columns, target in (
            (DECISION, "fk_writing_diagnostic_initial", ["submission_id", "required_initial_phase"], ["submission_id", "phase"]),
            (PLAN, "fk_writing_practice_decision_submission", ["decision_id", "submission_id"], ["id", "submission_id"]),
        ):
            fk = next(item for item in inspector.get_foreign_keys(table.name) if item["name"] == name)
            assert fk["constrained_columns"] == columns and fk["referred_columns"] == target
            assert fk["referred_table"] == DECISION.name and fk["options"]["ondelete"] == "CASCADE"
        # All referenced rows exist except for an initial on the third submission.
        rejects(connection, DECISION, answer["id"], "FOREIGN KEY", id="cross-answer",
                submission_id=another_essay["submission_id"])
        rejects(connection, PLAN, practice["id"], "FOREIGN KEY", id="cross-plan",
                submission_id=another_essay["submission_id"])
        rejects(connection, DECISION, initial["id"], "UNIQUE", id="duplicate-initial", idempotency_key="new-key")
        rejects(connection, PLAN, practice["id"], "UNIQUE", id="duplicate-practice")
        rejects(connection, REVISION, practice["revision"]["id"], "UNIQUE", id="duplicate-revision")
        rejects(connection, ASSIGNMENT, transfer["id"], "UNIQUE", id="duplicate-assignment", material_id="different")
        rejects(connection, RESPONSE, final["id"], "UNIQUE", id="duplicate-response")
        # Different revision and same material. Only repeated exposure may share it.
        other_revision_id = other_practice["revision"]["id"]
        rejects(connection, ASSIGNMENT, transfer["id"], "UNIQUE", id="duplicate-exposure",
                practice_revision_id=other_revision_id)
        clone(connection, ASSIGNMENT, transfer["id"], id="repeat-allowed",
              practice_revision_id=other_revision_id, repeated_exposure=True)
        assert not connection.exec_driver_sql("PRAGMA foreign_key_check").all()


def test_downgrades_report_exact_losses_and_preserve_d1(isolated_engine):
    root, engine, url = isolated_engine
    upgrade_database(database_url=url, engine=engine)
    with server(root, mode="valid") as (client, _):
        run = WritingRun.create(client)
        original, _, _, _ = run.finish_training()
        run.call("POST", "/transfer/start")
        run.call("POST", "/transfer/submit", {"body": TRANSFER, "version": 0, "idempotency_key": "downgrade-transfer"})
        audit_run = WritingRun.create(client)
        audit_run.submit_essay()
        audit = audit_run.call("POST", "/diagnostic/start", {
            "allow_model": True, "idempotency_key": "downgrade-model-call",
        })["initial"]["model_call"]
        assert audit and audit["status"] == "succeeded"

    expected_losses = {TRANSFER_REV: [1, 1], PRACTICE: [1, 1], DIAGNOSTIC: [3]}
    for previous, target in ((TRANSFER_REV, PRACTICE), (PRACTICE, DIAGNOSTIC), (DIAGNOSTIC, D1)):
        tables = STAGES[previous]
        with engine.begin() as connection:
            losses = [row_count(connection, table) for table in tables]
            assert losses == expected_losses[previous]
            config = get_alembic_config(url)
            config.attributes["connection"] = connection
            command.downgrade(config, target)
            assert current(connection) == target
            assert all(not inspect(connection).has_table(table.name) for table in tables)
            assert connection.scalar(select(AUDIT.c.id)) == audit["id"]
            assert connection.scalar(select(ESSAYS.c.body).where(ESSAYS.c.id == original["submission_id"])) == ESSAY
            assert row_count(connection, ESSAYS) == 2
            assert not connection.exec_driver_sql("PRAGMA foreign_key_check").all()
            print(f"{previous} -> {target} removed {dict(zip((table.name for table in tables), losses))}; D1 and audit retained")
