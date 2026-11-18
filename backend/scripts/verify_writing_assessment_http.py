"""Run real-Uvicorn HTTP acceptance against a disposable SQLite database."""

from __future__ import annotations

import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import uuid4

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
from app.config import Settings
from app.persistence.database import create_db_engine
from app.persistence.migrations_runner import upgrade_database
from app.content.writing_initial_assessment import PROMPT, TASK_VERSION

ESSAY = ("Community gardens can bring neighbours together and provide fresh food. "
         "They also teach children where food comes from.\n\n"
         "Sports areas offer exercise and a place for teams to meet. This matters because "
         "regular activity can support health and give young people a positive shared interest.\n\n"
         "I would choose a garden where land is limited, since it can serve residents of different ages. "
         "A town could include a small open space nearby so that both needs receive attention.")


def request(base: str, method: str, path: str, payload: dict | None = None) -> tuple[int, dict]:
    data = json.dumps(payload).encode() if payload is not None else None
    req = Request(base + path, data=data, method=method,
                  headers={"Content-Type": "application/json"} if data is not None else {})
    try:
        with urlopen(req, timeout=10) as response:
            raw = response.read()
            return response.status, json.loads(raw) if raw else {}
    except HTTPError as exc:
        raw = exc.read()
        return exc.code, json.loads(raw) if raw else {}


def wait_for(base: str, process: subprocess.Popen, log_path: Path) -> None:
    until = time.time() + 20
    while time.time() < until:
        if process.poll() is not None:
            raise RuntimeError(f"Uvicorn exited early; see {log_path}")
        try:
            if request(base, "GET", "/api/v1/health")[0] == 200:
                return
        except (URLError, TimeoutError, OSError):
            pass
        time.sleep(0.2)
    raise TimeoutError(f"Uvicorn did not become ready; see {log_path}")


def stop(process: subprocess.Popen) -> None:
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)


def run() -> dict:
    with tempfile.TemporaryDirectory(prefix="verbatrail-writing-http-", ignore_cleanup_errors=True) as temp:
        root = Path(temp)
        db_path = root / "isolated.sqlite3"
        log_path = root / "uvicorn.log"
        db_url = f"sqlite:///{db_path.as_posix()}"
        settings = Settings(APP_ENV="testing", DATABASE_URL=db_url, MODEL_PROVIDER="fake",
                            ALLOW_REMOTE_MODEL_CALLS=False)
        engine = create_db_engine(settings)
        upgrade_database(database_url=db_url, engine=engine)
        engine.dispose()
        version_socket = socket.socket()
        version_socket.bind(("127.0.0.1", 0))
        port = version_socket.getsockname()[1]
        version_socket.close()
        base = f"http://127.0.0.1:{port}"
        env = os.environ.copy()
        env.update({"APP_ENV": "testing", "DATABASE_URL": db_url, "MODEL_PROVIDER": "fake",
                    "ALLOW_REMOTE_MODEL_CALLS": "false", "PYTHONPATH": str(BACKEND)})
        log_handle = log_path.open("w", encoding="utf-8")

        def start() -> subprocess.Popen:
            child = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1",
                                      "--port", str(port), "--log-level", "warning"], cwd=BACKEND,
                                     env=env, stdout=log_handle, stderr=subprocess.STDOUT)
            wait_for(base, child, log_path)
            return child

        process = start()
        learner = "http-writer-" + uuid4().hex[:8]
        run_payload = {"learner_id": learner, "assessment_version": "initial-v2",
                       "idempotency_key": "run-" + uuid4().hex}
        code, created = request(base, "POST", "/api/v1/assessment-runs", run_payload)
        assert code == 200
        run_id = created["run_id"]
        writing = f"/api/v1/assessment-runs/{run_id}/writing?learner_id={learner}"
        essay_path = f"/api/v1/assessment-runs/{run_id}/writing/submit"
        code, started = request(base, "POST", writing.split("?", 1)[0] + "/start?learner_id=" + learner)
        assert code == 200 and started["task_version"] == TASK_VERSION
        code, failed = request(base, "POST", essay_path, {"learner_id": learner, "version": 0, "body": "",
            "task_version": TASK_VERSION, "prompt_id": PROMPT["prompt_id"],
            "prompt_version": PROMPT["prompt_version"], "idempotency_key": "bad-request-01"})
        assert code == 422 and "message" in failed

        # Make an actual HTTP request and close without reading its response. Confirm DB commit,
        # restart Uvicorn, then recover the formal text through GET and same-key retry.
        submit_payload = {"learner_id": learner, "version": 0, "body": ESSAY,
            "task_version": TASK_VERSION, "prompt_id": PROMPT["prompt_id"],
            "prompt_version": PROMPT["prompt_version"], "idempotency_key": "lost-response-0001"}
        from http.client import HTTPConnection
        connection = HTTPConnection("127.0.0.1", port, timeout=3)
        connection.request("POST", essay_path, body=json.dumps(submit_payload), headers={"Content-Type": "application/json"})
        connection.close()  # Simulated lost response after the request body was sent.
        deadline = time.time() + 10
        committed = False
        while time.time() < deadline:
            with sqlite3.connect(db_path) as db:
                committed = db.execute("select count(*) from assessment_writing_submissions").fetchone()[0] == 1
            if committed:
                break
            time.sleep(0.1)
        assert committed, "The disconnected HTTP request did not commit the submission."
        stop(process)
        process = start()
        code, restored = request(base, "GET", writing)
        assert code == 200 and restored["submission"]["body"] == ESSAY
        assert restored["task_status"] == "completed"
        code, replayed = request(base, "POST", essay_path, submit_payload)
        assert code == 200 and replayed == restored
        code, conflict = request(base, "POST", essay_path, submit_payload | {"body": ESSAY + " Changed."})
        assert code == 409
        code, conflict_version = request(base, "POST", essay_path, submit_payload | {"version": 1})
        assert code == 409
        code, conflict_key = request(base, "POST", essay_path, submit_payload | {"idempotency_key": "different-key-0002"})
        assert code == 409
        code, wrong_version = request(base, "POST", essay_path, submit_payload | {"task_version": "old"})
        assert code == 409
        code, hidden = request(base, "GET", writing.replace(f"learner_id={learner}", "learner_id=other"))
        assert code == 404

        # Same learner, different run has independent empty draft and separate evidence scope.
        second_payload = run_payload | {"idempotency_key": "run-" + uuid4().hex}
        _, second = request(base, "POST", "/api/v1/assessment-runs", second_payload)
        second_path = f"/api/v1/assessment-runs/{second['run_id']}/writing"
        code, second_started = request(base, "POST", second_path + "/start?learner_id=" + learner)
        assert code == 200 and second_started["draft"]["body"] == ""
        code, wrong_learner_write = request(base, "PUT", second_path + "/draft",
                                            {"learner_id": "other", "version": 0, "body": "intrusion"})
        assert code == 404
        code, ordinary = request(base, "POST", "/api/v1/writing/sessions", {"learner_id": learner})
        assert code == 200
        code, model = request(base, "GET", "/api/v1/learner-model?learner_id=" + learner)
        writing_skill = next(skill for skill in model["skills"] if skill["skill_id"] == "writing")
        assert writing_skill["assessment_status"] == "not_assessed"
        assert all(e["assessment_status"] == "not_assessed" for e in writing_skill["evidence"])
        assert not any("band" in e["observation"].lower() and "not assessed" not in e["observation"].lower()
                       for e in writing_skill["evidence"])

        # Verify other skill tasks remain independent and the run has no inferred close-out.
        _, run_after = request(base, "GET", f"/api/v1/assessment-runs/{run_id}?learner_id={learner}")
        task_statuses = {task["skill_id"]: task["status"] for task in run_after["tasks"]}
        assert task_statuses == {"listening": "not_started", "speaking": "not_started",
                                 "reading": "not_started", "writing": "completed"}
        counts = {}
        with sqlite3.connect(db_path) as db:
            counts = {table: db.execute(f"select count(*) from {table}").fetchone()[0]
                      for table in ("assessment_runs", "assessment_tasks", "assessment_writing_drafts",
                                    "assessment_writing_submissions", "writing_drafts", "writing_revisions")}
            head = db.execute("select version_num from alembic_version").fetchone()[0]
        assert head == "0013_writing_assessment"
        assert counts["assessment_writing_submissions"] == 1 and counts["writing_revisions"] == 0
        stop(process)
        log_handle.close()
        return {"result": "PASS", "base_url": "real Uvicorn / HTTP", "assessment_run_id": run_id,
                "submission_id": restored["submission"]["submission_id"], "recovery": "request response discarded; process restarted; GET and idempotent replay restored same evidence",
                "checks": ["isolated SQLite migration", "bad request leaves no submission", "start pins task version",
                           "lost response recovery after restart", "same-key idempotent replay", "body/version/task-version conflicts",
                           "run and learner isolation", "ordinary Writing remains independent", "Learner Model boundary",
                           "Reading/Listening/Speaking task independence"], "counts": counts, "alembic_head": head}


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=2))
