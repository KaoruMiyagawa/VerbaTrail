"""Verify C1 through real Uvicorn using an isolated, disposable SQLite database."""

import hashlib
import json
import os
import socket
import sqlite3
import subprocess
import sys
import time
from contextlib import closing, contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory

import httpx

from app.content.listening_initial_assessment import QUESTIONS as LISTENING_QUESTIONS
from app.content.reading_initial_assessment import V2_QUESTIONS
from app.content.speaking_initial_assessment import PROMPT, RECORDING_SOURCE, TASK_VERSION
from app.persistence.migrations_runner import upgrade_database

ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "docs/development/speaking-initial-assessment-c1/http-evidence.json"
BASE = "/api/v1/assessment-runs"
LEARNER = "c1-http-learner"
PARAMS = {"learner_id": LEARNER}


@contextmanager
def uvicorn_server(url, port, log_path):
    env = {
        **os.environ, "DATABASE_URL": url, "APP_ENV": "testing",
        "MODEL_PROVIDER": "fake", "MODEL_API_KEY": "", "ALLOW_REMOTE_MODEL_CALLS": "false",
    }
    with log_path.open("wb") as log:
        process = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1",
             "--port", str(port), "--log-level", "warning"],
            cwd=ROOT / "backend", env=env, stdout=log, stderr=subprocess.STDOUT,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        try:
            with httpx.Client(base_url=f"http://127.0.0.1:{port}", trust_env=False, timeout=15) as client:
                for _ in range(150):
                    if process.poll() is not None:
                        break
                    try:
                        if client.get("/api/v1/health", timeout=1).status_code == 200:
                            break
                    except httpx.TransportError:
                        pass
                    time.sleep(0.1)
                else:
                    raise RuntimeError("Uvicorn startup timed out.")
                if process.poll() is not None:
                    raise RuntimeError(log_path.read_text(encoding="utf-8", errors="replace"))
                yield client, process.pid
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)


def request(client, evidence, label, method, path, *, expected=200, **kwargs):
    response = client.request(method, path, **kwargs)
    data = response.json()
    evidence["requests"].append({
        "check": label, "method": method, "path": path, "request": kwargs,
        "status": response.status_code, "expected": expected, "response": data,
    })
    assert response.status_code == expected, (label, response.status_code, data)
    return data


def verify(database, base_url, port, log_path, evidence):
    payload = {
        **PARAMS, "recording_id": "c1-http-synthetic-metadata", "task_version": TASK_VERSION,
        "prompt_id": PROMPT["prompt_id"], "prompt_version": PROMPT["prompt_version"],
        "duration_seconds": 48.25, "recorded_at": "2026-11-17T12:05:00+08:00",
        "idempotency_key": "c1-http-registration",
    }
    with uvicorn_server(base_url, port, log_path) as (client, first_pid):
        def call(label, method, path, **kwargs):
            return request(client, evidence, label, method, path, **kwargs)

        run = call("create_v2", "POST", BASE, json={**PARAMS, "idempotency_key": "c1-run"})
        path = f"{BASE}/{run['run_id']}"
        task = f"{path}/speaking"
        assert call("read_run", "GET", path, params=PARAMS) == run

        other_results = {}
        for skill, questions in [("reading", V2_QUESTIONS), ("listening", LISTENING_QUESTIONS)]:
            other_path = f"{path}/{skill}"
            call(f"start_{skill}", "POST", f"{other_path}/start", params=PARAMS)
            result = call(f"submit_{skill}", "POST", f"{other_path}/submit", json={
                **PARAMS, "answers": {q["id"]: q["answer"] for q in questions},
                "idempotency_key": f"c1-regression-{skill}",
            })
            assert result["task_status"] == "completed" and result["correct_count"] == len(questions)
            other_results[skill] = result

        practice_prompt = call("practice_prompts", "GET", "/api/v1/speaking/prompts")[0]
        practice = call("ordinary_practice", "POST", "/api/v1/speaking/practices", json={
            **PARAMS, "recording_id": "c1-ordinary-practice", "prompt_id": practice_prompt["id"],
            "prompt_version": practice_prompt["version"], "duration_seconds": 20,
            # Match the practice browser's Date.toISOString() payload.
            "recorded_at": "2026-11-17T04:05:00Z", "self_check": ["direct"],
        })
        assert practice["evaluation_status"] == "not_assessed"
        unstarted = call("ordinary_practice_does_not_complete_assessment", "GET", task, params=PARAMS)
        assert unstarted["task_status"] == "not_started" and unstarted["recording"] is None
        assert unstarted["prompt"] is None and unstarted["task_version"] is None
        started = call("pin_original_prompt", "POST", f"{task}/start", params=PARAMS)
        assert started["task_status"] == "in_progress" and started["prompt"] == PROMPT
        assert started["task_version"] == TASK_VERSION and not started["repeated_exposure"]
        assert call("repeat_start", "POST", f"{task}/start", params=PARAMS) == started
        call("reject_audio_field", "POST", f"{task}/complete", expected=422, json=payload | {"audio": "test-sentinel"})
        completed = call("register_metadata", "POST", f"{task}/complete", json=payload)
        assert completed["task_status"] == "completed" and completed["metadata_status"] == "registered"
        assert completed["evidence_source"] == RECORDING_SOURCE and completed["evidence_reference"] == payload["recording_id"]
        assert completed["recording"]["provenance"] == "client_reported"
        assert completed["recording"]["recorded_at"] == "2026-11-17T04:05:00Z"
        assert completed["audio_available_on_server"] is False and completed["local_audio_availability"] == "unknown"
        assert completed["evaluation_status"] == "not_assessed" and completed["evidence_validity"] == "not_evaluated"
        assert call("replay_completion", "POST", f"{task}/complete", json=payload) == completed
        call("conflicting_retry", "POST", f"{task}/complete", expected=409, json=payload | {"duration_seconds": 49})
        for method, suffix in [("GET", ""), ("POST", "/start"), ("POST", "/complete")]:
            other = {"learner_id": "c1-other-learner"}
            options = {"json": payload | other} if suffix == "/complete" else {"params": other}
            call(f"foreign_learner_{method}{suffix}", method, task + suffix, expected=404, **options)
        legacy = call("create_legacy", "POST", BASE, json={
            **PARAMS, "assessment_version": "initial-v1", "idempotency_key": "c1-legacy-run",
        })
        legacy_task = f"{BASE}/{legacy['run_id']}/speaking"
        call("legacy_read_conflict", "GET", legacy_task, expected=409, params=PARAMS)
        call("legacy_start_conflict", "POST", f"{legacy_task}/start", expected=409, params=PARAMS)
        assert call("legacy_run_unchanged", "GET", f"{BASE}/{legacy['run_id']}", params=PARAMS) == legacy
        next_run = call("create_repeat", "POST", BASE, json={**PARAMS, "idempotency_key": "c1-repeat-run"})
        next_task = f"{BASE}/{next_run['run_id']}/speaking"
        assert call("record_prior_prompt_exposure", "POST", f"{next_task}/start", params=PARAMS)["repeated_exposure"]
        call("recording_id_cannot_bind_another_task", "POST", f"{next_task}/complete", expected=409, json=payload)

    with uvicorn_server(base_url, port, log_path) as (client, second_pid):
        assert first_pid != second_pid
        evidence["uvicorn_restarted"] = True
        assert call("restore_after_restart", "GET", task, params=PARAMS) == completed
        assert call("retry_after_restart", "POST", f"{task}/complete", json=payload) == completed
        assert call("practice_history_is_separate", "GET", "/api/v1/speaking/practices", params=PARAMS) == [practice]
        for skill, previous in other_results.items():
            assert call(f"preserve_{skill}_result", "GET", f"{path}/{skill}", params=PARAMS) == previous
        run = call("check_run_and_task_statuses", "GET", path, params=PARAMS)
        expected = {"speaking": "completed", "reading": "completed", "listening": "completed", "writing": "not_started"}
        assert {item["skill_id"]: item["status"] for item in run["tasks"]} == expected
        assert run["status"] == "in_progress" and run["completed_at"] is None
        model = call("learner_model_stays_unassessed", "GET", "/api/v1/learner-model", params=PARAMS)
        skill = next(item for item in model["skills"] if item["skill_id"] == "speaking")
        assert skill["assessment_status"] == "not_assessed" and skill["evidence_levels"] == ["practice_activity"]
        registered = [item for item in skill["evidence"] if item["evidence_source"] == RECORDING_SOURCE]
        assert len(registered) == 1 and registered[0]["evidence_type"] == "assessment_activity"
        assert registered[0]["assessment_status"] == "not_assessed"
        assert registered[0]["evidence_validity"] == "not_evaluated"
        assert registered[0]["evidence_reference"] == payload["recording_id"]

    with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as db:
        row = db.execute("""
            SELECT r.recording_id, t.run_id, t.status, t.evidence_reference
            FROM speaking_assessment_recordings r
            JOIN assessment_tasks t ON t.id = r.assessment_task_id
        """).fetchall()
        assert row == [(payload["recording_id"], run["run_id"], "completed", payload["recording_id"])]
        counts = {"registrations": len(row)}
        for table in ("speaking_practices", "learning_sessions", "attempts"):
            counts[table] = db.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        assert counts == {"registrations": 1, "speaking_practices": 1, "learning_sessions": 3, "attempts": 10}
        evidence["isolated_database"] = {"counts": counts, "linked_metadata": row[0]}


def main():
    evidence = {
        "status": "running", "requests": [],
        "scope": "Real HTTP with synthetic recording metadata. No audio was sent or captured from a microphone.",
        "practice_compatibility": (
            "The ordinary practice fixture matches the existing browser's UTC timestamp. "
            "The first HTTP run exposed pre-existing loss of non-UTC offsets in ordinary practice "
            "after SQLite reload. The C1 registration separately verifies offset normalization."
        ),
    }
    formal = ROOT / "backend/unlearnloop.db"
    before = hashlib.sha256(formal.read_bytes()).hexdigest().upper() if formal.exists() else None
    try:
        with TemporaryDirectory(prefix="verbatrail-c1-http-") as directory:
            database = Path(directory) / "assessment.sqlite"
            url = f"sqlite:///{database.as_posix()}"
            upgrade_database(database_url=url, revision="0011_assessment_reading_task")
            upgrade_database(database_url=url)
            evidence["migration"] = ["0011_assessment_reading_task", "0012_speaking_assessment"]
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", 0))
                port = sock.getsockname()[1]
            verify(database, url, port, Path(directory) / "uvicorn.log", evidence)
        after = hashlib.sha256(formal.read_bytes()).hexdigest().upper() if formal.exists() else None
        evidence["formal_database_sha256"] = {"before": before, "after": after}
        assert before == after
        evidence["status"] = "pass"
    except Exception as error:
        evidence.update(status="fail", error=str(error))
        raise
    finally:
        OUTPUT.parent.mkdir(parents=True, exist_ok=True)
        OUTPUT.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"HTTP acceptance passed ({len(evidence['requests'])} requests): {OUTPUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
