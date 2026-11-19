"""Exercise the full chain against a real Uvicorn process and isolated SQLite."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import update

from app.persistence.database import create_db_engine
from app.persistence.models import (
    AssessmentWritingPractice, AssessmentWritingPracticeRevision, AssessmentWritingSubmission,
    AssessmentWritingTransferResponse,
)
from scripts.writing_d4_harness import (
    ANSWER, ESSAY, REVISION, TRANSFER, WritingRun, database_url, digest, prepare_database, scratch, server, wait_until,
)


@pytest.fixture
def d4_database():
    with scratch() as directory:
        root = Path(directory)
        prepare_database(root)
        yield root


def race(*actions):
    barrier = Barrier(len(actions), timeout=10)

    def run(action):
        barrier.wait()
        return action()

    with ThreadPoolExecutor(max_workers=len(actions)) as pool:
        return list(pool.map(run, actions))


def final_payload(view, body):
    return {"body": body, "version": view["draft"]["version"], "idempotency_key": uuid4().hex}


def save_then_submit(run, stage, view, body, result_field):
    start_version = view["draft"]["version"]
    responses = race(*[
        lambda suffix=suffix: run.request("PUT", f"/{stage}/draft",
                                         {"body": body + suffix, "version": start_version})
        for suffix in (" A", " B")
    ])
    assert sorted(response.status_code for response in responses) == [200, 409]
    winner = next(response.json()["draft"] for response in responses if response.is_success)
    assert run.call("GET", f"/{stage}")["draft"] == winner

    payload = final_payload({"draft": winner}, body)
    replies = race(*[lambda: run.call("POST", f"/{stage}/submit", payload)] * 2)
    saved = replies[0][result_field]
    assert replies[1][result_field] == saved
    assert saved["body"] == body and saved["body_sha256"] == digest(body)
    for change in ({"body": body + " altered"}, {"version": start_version}, {"idempotency_key": uuid4().hex}):
        run.call("POST", f"/{stage}/submit", payload | change, status=409)
    run.call("PUT", f"/{stage}/draft", {"body": "Cannot overwrite", "version": winner["version"]}, status=409)
    assert run.call("GET", f"/{stage}")[result_field] == saved
    return saved


def test_full_chain_concurrency_scope_and_reopen(d4_database):
    with server(d4_database) as (client, _):
        run = WritingRun.create(client)
        parallel = WritingRun.create(client, run.learner)
        other = WritingRun.create(client, "bob")
        _, foreign = other.submit_essay("Another learner's essay.")
        run.call("GET", "/diagnostic", status=409)
        task = run.call("POST", "/start")
        first = run.call("PUT", "/draft", {"learner_id": run.learner, "body": ESSAY, "version": task["draft"]["version"]})
        run.call("PUT", "/draft", {"learner_id": run.learner, "body": "Lost update", "version": task["draft"]["version"]}, status=409)
        assert run.call("GET")["draft"]["body"] == ESSAY and first["version"] > task["draft"]["version"]
        original_request, original = run.submit_essay()
        assert run.call("POST", "/submit", original_request)["submission"] == original
        run.call("POST", "/submit", original_request | {"body": "Replacement"}, status=409)
        run.call("POST", "/submit", original_request | {"prompt_version": "other"}, status=409)
        run.call("POST", "/submit", original_request | {"learner_id": other.learner}, status=404)
        assert original["body_sha256"] == digest(ESSAY)
        assert original["semantic_quality"] == "not_assessed" and not original["ability_evidence_valid"]
        for path in ("", "/diagnostic", "/diagnostic/session", "/training", "/transfer"):
            run.call("GET", path, learner=other.learner, status=404)
        for path in ("/training/start", "/transfer/start"):
            run.call("POST", path, learner=other.learner, status=404)

        evidence = run.call("GET", "/diagnostic")
        assert evidence["source"]["submission_id"] == original["submission_id"] != foreign["submission_id"]
        assert evidence["source"]["body_sha256"] == digest(ESSAY)
        assert len(evidence["evidence"]) == 2
        for item in evidence["evidence"]:
            ref = item["source_reference"]
            assert ref["coordinate_system"] == "utf16_code_units"
            assert ref["submission_id"] == original["submission_id"]
            assert ref["task_version"] == original["task_version"]
            assert ESSAY.encode("utf-16-le")[2 * ref["start"]:2 * ref["end"]].decode("utf-16-le") == ref["quote"]
        run.call("POST", "/diagnostic/start", {"idempotency_key": uuid4().hex, "evidence": ["invented"]}, status=422)

        request = {"idempotency_key": uuid4().hex, "allow_model": True}
        run.call("POST", "/diagnostic/start", request, learner=other.learner, status=404)
        first, second = race(*[lambda: run.call("POST", "/diagnostic/start", request)] * 2)
        assert first == second and first["state"] == "awaiting_answer"
        assert first["initial"]["outcome"]["fallback_reason"] == "remote_disabled"
        assert first["initial"]["model_call"] is None and first["tokens_charged"] == 0
        assert not (d4_database / "calls.log").exists()
        answer_request = {"question_id": first["initial"]["id"], "idempotency_key": uuid4().hex, "body": ANSWER}
        run.call("POST", "/diagnostic/answer", answer_request | {"question_id": foreign["submission_id"]}, status=409)
        answer = run.call("POST", "/diagnostic/answer", answer_request)
        assert run.call("POST", "/diagnostic/answer", answer_request) == answer
        run.call("POST", "/diagnostic/answer", answer_request | {"idempotency_key": uuid4().hex}, status=409)
        run.call("POST", "/diagnostic/answer", answer_request | {"body": "Changed answer"}, status=409)
        assert answer["state"] == "stopped" and answer["verified_conclusion"] is None
        followup = answer["followup"]
        assert followup["answer"]["body"] == ANSWER and followup["answer"]["body_sha256"] == digest(ANSWER)
        assert not followup["outcome"]["ability_evidence_valid"]

        run.call("POST", "/transfer/start", status=409)
        p1, p2 = race(*[lambda: run.call("POST", "/training/start")] * 2)
        assert p1["id"] == p2["id"]
        assert p1["plan"]["anchor"]["source_reference"] in [item["source_reference"] for item in evidence["evidence"]]
        assert p1["plan"]["decision_id"] == followup["id"]
        revision = save_then_submit(run, "training", p1, REVISION, "revision")
        assert not revision["feedback"]["capability_evidence_valid"]
        parallel.call("GET", "/training", status=409)
        parallel.call("GET", "/transfer", status=409)

        t1, t2 = race(*[lambda: run.call("POST", "/transfer/start")] * 2)
        assert t1["id"] == t2["id"]
        assert t1["task"]["aspect"] == p1["plan"]["aspect"]
        assert not t1["repeated_exposure"]
        assert all(body not in str(t1) for body in (ESSAY.split("\n")[0], ANSWER, REVISION))
        transfer = save_then_submit(run, "transfer", t1, TRANSFER, "response")
        assert transfer["lineage"] == {
            "original_submission_id": original["submission_id"],
            "initial_diagnostic_id": first["initial"]["id"], "followup_diagnostic_id": followup["id"],
            "practice_id": p1["id"], "practice_revision_id": revision["id"],
        }
        observation = transfer["observation"]
        assert observation["evaluation_status"] == "not_assessed" and observation["ability_conclusion"] is None
        assert not observation["assessment_sufficient"] and not observation["ability_evidence_valid"]
        assert observation["word_count"] > 0 and observation["paragraph_count"] == 1

    with server(d4_database) as (client, _):
        restored = WritingRun(client, run.run_id, run.learner)
        assert restored.call("GET")["submission"] == original
        assert restored.call("GET", "/diagnostic/session") == answer
        assert restored.call("GET", "/training")["revision"] == revision
        assert restored.call("GET", "/transfer")["response"] == transfer


def test_exposure_race_and_repeat_are_learner_scoped(d4_database):
    with server(d4_database) as (client, _):
        a, b, c, other = [WritingRun.create(client, learner) for learner in ("alice", "alice", "alice", "bob")]
        for run in (a, b, c, other):
            run.finish_training()
        first, second = race(lambda: a.call("POST", "/transfer/start"), lambda: b.call("POST", "/transfer/start"))
        assert not first["repeated_exposure"] and not second["repeated_exposure"]
        materials = {assignment["task"]["material_id"] for assignment in (first, second)}
        assert len(materials) == 2
        repeated = c.call("POST", "/transfer/start")
        assert repeated["repeated_exposure"] and repeated["task"]["material_id"] in materials
        final = c.call("POST", "/transfer/submit", final_payload(repeated, TRANSFER))["response"]
        assert final["observation"]["evidence_validity"] == "repeated_exposure"
        assert not final["observation"]["ability_evidence_valid"]
        assert not other.call("POST", "/transfer/start")["repeated_exposure"]


@pytest.mark.parametrize("corrupt", ["essay_hash", "anchor_quote", "revision_hash", "transfer_hash"])
def test_corrupt_stored_evidence_rejected_over_http(d4_database, corrupt):
    with server(d4_database) as (client, _):
        run = WritingRun.create(client)
        original, _, _, practice = run.finish_training()
        task = run.call("POST", "/transfer/start")
        response = run.call("POST", "/transfer/submit", final_payload(task, TRANSFER))["response"]
        wrong_hash = {"body_sha256": "0" * 64}
        if corrupt == "anchor_quote":
            plan = practice["plan"]
            plan["anchor"]["source_reference"]["quote"] = "A fabricated excerpt."
        corruptions = {
            "essay_hash": (AssessmentWritingSubmission, original["submission_id"], wrong_hash,
                           ["/diagnostic", "/diagnostic/session", "/training", "/transfer"]),
            "anchor_quote": (AssessmentWritingPractice, practice["id"], {"plan": practice["plan"]},
                             ["/training", "/transfer"]),
            "revision_hash": (AssessmentWritingPracticeRevision, practice["revision"]["id"], wrong_hash,
                              ["/training", "/transfer"]),
            "transfer_hash": (AssessmentWritingTransferResponse, response["id"], wrong_hash, ["/transfer"]),
        }
        model, record_id, changes, paths = corruptions[corrupt]
        engine = create_db_engine(database_url=database_url(d4_database))
        try:
            with engine.begin() as db:
                db.execute(update(model.__table__).where(model.__table__.c.id == record_id).values(**changes))
        finally:
            engine.dispose()
        for path in paths:
            run.call("GET", path, status=409)


def model_intent():
    return {"idempotency_key": uuid4().hex, "allow_model": True}


def invocations(root):
    ledger = root / "calls.log"
    return ledger.read_text(encoding="utf-8").splitlines() if ledger.exists() else []


@pytest.mark.parametrize(("mode", "budget", "reason", "audit_status", "reported"), [
    ("timeout", 4096, "model_error", "timeout", 0),
    ("json", 4096, "invalid_model_output", "invalid_json", 0),
    ("provider-budget", 4096, "budget_exhausted", "budget_exceeded", 0),
    ("valid", 1, "budget_exhausted", None, 0),
    ("evidence", 4096, "invalid_model_output", "schema_error", 12),
    ("excerpt", 4096, "invalid_model_output", "schema_error", 12),
    ("valid", 4096, None, "succeeded", 12),
])
def test_agent_faults_preserve_audit_and_budget(d4_database, mode, budget, reason, audit_status, reported):
    with server(d4_database, mode=mode, budget=budget) as (client, _):
        run = WritingRun.create(client)
        run.submit_essay()
        if mode == "evidence":
            foreign = WritingRun.create(client, run.learner)
            foreign.submit_essay()
            item = foreign.call("GET", "/diagnostic")["evidence"][0]
            (d4_database / "foreign-evidence").write_text(item["id"], encoding="utf-8")
        payload = model_intent()
        session = run.call("POST", "/diagnostic/start", payload)
        decision = session["initial"]
        outcome, audit = decision["outcome"], decision["model_call"]
        assert outcome["fallback_reason"] == reason
        assert outcome["method"] == ("structured_model" if reason is None else "deterministic_rules")
        assert not outcome["ability_evidence_valid"] and session["verified_conclusion"] is None
        assert session["tokens_pending_reservation"] == 0 and session["tokens_reported"] == reported
        assert session["token_budget"] == budget
        if audit_status is None:
            assert audit is None and decision["planned_model"] is None
            assert session["tokens_charged"] == 0 and invocations(d4_database) == []
        else:
            assert audit["status"] == audit_status and audit["provider_name"] == "writing-d4-test-double"
            assert audit["attempts"] == 1 and audit["reported_tokens"] == reported
            assert audit["prompt_version"] == decision["planned_model"]["prompt_version"]
            assert 0 < session["tokens_charged"] <= budget
            if reported:
                assert session["tokens_charged"] == reported
            else:
                assert audit["error_code"] and session["tokens_charged"] >= 256
            assert invocations(d4_database) == ["1"]
        assert run.call("POST", "/diagnostic/start", payload) == session
        assert invocations(d4_database) == (["1"] if audit_status else [])


def test_agent_cannot_promote_answer_only_to_essay_support(d4_database):
    with server(d4_database, mode="answer-only") as (client, _):
        run = WritingRun.create(client)
        run.submit_essay()
        initial = run.call("POST", "/diagnostic/start", model_intent())["initial"]
        assert initial["outcome"]["method"] == "structured_model"
        session = run.call("POST", "/diagnostic/answer", model_intent() | {
            "question_id": initial["id"], "body": ANSWER,
        })
        assert session["state"] == "stopped" and session["verified_conclusion"] is None
        decision = session["followup"]
        assert decision["answer"]["source"] == "learner_followup_response"
        assert decision["answer"]["body_sha256"] == digest(ANSWER)
        assert decision["outcome"]["fallback_reason"] == "invalid_model_output"
        assert decision["model_call"]["error_code"] == "WRITING_PROPOSAL_INVALID"
        assert session["tokens_reported"] == session["tokens_charged"] == 24
        assert invocations(d4_database) == ["1", "1"]


def test_agent_reported_overrun_is_audited_and_blocks_followup_call(d4_database):
    with server(d4_database, mode="overrun") as (client, _):
        run = WritingRun.create(client)
        run.submit_essay()
        session = run.call("POST", "/diagnostic/start", model_intent())
        initial = session["initial"]
        assert initial["model_call"]["status"] == "succeeded"
        assert session["tokens_charged"] == session["tokens_reported"] > session["token_budget"]
        reviewed = run.call("POST", "/diagnostic/answer", model_intent() | {
            "question_id": initial["id"], "body": ANSWER,
        })
        assert reviewed["followup"]["outcome"]["fallback_reason"] == "budget_exhausted"
        assert reviewed["followup"]["model_call"] is None
        assert reviewed["tokens_charged"] == reviewed["tokens_reported"] == session["tokens_reported"]
        assert reviewed["tokens_pending_reservation"] == 0
        assert invocations(d4_database) == ["1"]


@pytest.mark.parametrize("interruption", ["kill_process", "late_reply"])
def test_agent_single_reservation_and_recovery(d4_database, interruption):
    payload = model_intent()
    with server(d4_database, mode="hold") as (client, process), ThreadPoolExecutor(max_workers=1) as pool:
        run = WritingRun.create(client)
        run.submit_essay()
        request = pool.submit(run.call, "POST", "/diagnostic/start", payload)
        try:
            wait_until(lambda: invocations(d4_database) == ["1"], label="the held provider invocation")
            pending = run.call("POST", "/diagnostic/start", payload)
            saved = pending["initial"]
            assert pending["state"] == "processing" and saved["outcome"] is None
            assert saved["planned_model"]["provider_name"] == "writing-d4-test-double"
            assert pending["tokens_charged"] == pending["tokens_pending_reservation"] > 0
            assert pending["tokens_reported"] == 0 and saved["model_call"] is None
            run.call("POST", "/diagnostic/start", payload | {"recover_decision_id": "different"}, status=409)
            recovery = payload | {"recover_decision_id": saved["id"]}
            if interruption == "kill_process":
                process.kill()
                process.wait(timeout=10)
                with pytest.raises(httpx.TransportError):
                    request.result(timeout=10)
            else:
                (d4_database / "expire").touch()
                recovered = run.call("POST", "/diagnostic/start", recovery)
                assert recovered["initial"]["outcome"]["fallback_reason"] == "interrupted"
                assert recovered["initial"]["model_call"] is None
                (d4_database / "release").touch()
                late = request.result(timeout=10)
                assert late["initial"]["id"] == saved["id"]
                assert late["initial"]["outcome"] == recovered["initial"]["outcome"]
                assert late["initial"]["model_call"]["status"] == "succeeded"
                assert late["tokens_pending_reservation"] == 0 and late["tokens_charged"] == late["tokens_reported"] == 12
                assert run.call("POST", "/diagnostic/start", recovery) == late
        finally:
            (d4_database / "release").touch()

    if interruption == "kill_process":
        (d4_database / "expire").touch()
        with server(d4_database, mode="hold") as (client, _):
            restored = WritingRun(client, run.run_id, run.learner)
            assert restored.call("GET", "/diagnostic/session")["initial"]["recoverable"]
            recovered = restored.call("POST", "/diagnostic/start", recovery)
            assert recovered["initial"]["id"] == saved["id"]
            assert recovered["initial"]["outcome"]["fallback_reason"] == "interrupted"
            assert recovered["initial"]["model_call"] is None
            assert recovered["tokens_charged"] == pending["tokens_charged"]
            assert recovered["tokens_pending_reservation"] == recovered["tokens_reported"] == 0
            assert restored.call("POST", "/diagnostic/start", recovery) == recovered
    assert invocations(d4_database) == ["1"]
