"""Run the Listening initial assessment against an isolated live Uvicorn server."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
import wave
from io import BytesIO

from app.content import listening_initial_assessment as listening
from app.content.reading_initial_assessment import V2_QUESTIONS

ROOT = Path(__file__).resolve().parents[2]
EVIDENCE_PATH = ROOT / "docs" / "development" / "listening-initial-assessment-b1" / "http-evidence.json"
BASE = "/api/v1/assessment-runs"


def main() -> int:
    learner = "b1-http-learner"
    event_log: list[dict] = []
    event_lock = threading.Lock()
    evidence: dict = {
        "status": "running",
        "environment": "real Uvicorn HTTP + isolated SQLite; no browser/API mocks",
        "learner_id": learner,
        "content_version": listening.CONTENT_VERSION,
        "rule_version": listening.RULE_VERSION,
        "audio_url": listening.AUDIO_URL,
        "audio_expected_sha256": listening.AUDIO_SHA256,
        "requests": event_log,
        "checks": {},
        "failed": [],
    }

    def request(base_url: str, method: str, path: str, body: dict | None = None) -> tuple[int, object, bytes]:
        data = json.dumps(body).encode() if body is not None else None
        req = Request(base_url + path, data=data, method=method,
                      headers={"Content-Type": "application/json"} if data is not None else {})
        try:
            with urlopen(req, timeout=15) as response:
                raw = response.read()
                status = response.status
                parsed: object = json.loads(raw) if response.headers.get_content_type() == "application/json" else None
        except HTTPError as error:
            raw = error.read()
            status = error.code
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                parsed = None
        with event_lock:
            event_log.append({"method": method, "path": path, "status": status})
        return status, parsed, raw

    def assert_check(name: str, condition: bool, details: object = None) -> None:
        evidence["checks"][name] = {"status": "pass" if condition else "fail", "details": details}
        if not condition:
            evidence["failed"].append(name)
            raise AssertionError(f"Failed HTTP acceptance check: {name}; details={details!r}")

    with tempfile.TemporaryDirectory(prefix="verbatrail-listening-b1-", ignore_cleanup_errors=True) as temp_name:
        temp = Path(temp_name)
        database = temp / "listening-b1.sqlite"
        log_path = temp / "uvicorn.log"
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        base_url = f"http://127.0.0.1:{port}"
        env = os.environ.copy()
        env.update({
            "APP_ENV": "development",
            "DATABASE_URL": "sqlite:///" + database.as_posix(),
            "BACKEND_HOST": "127.0.0.1",
            "BACKEND_PORT": str(port),
            "MODEL_PROVIDER": "fake",
            "MODEL_API_KEY": "",
            "ALLOW_REMOTE_MODEL_CALLS": "false",
        })
        log_file = log_path.open("w", encoding="utf-8")
        server: subprocess.Popen | None = None

        def start_server() -> subprocess.Popen:
            child = subprocess.Popen(
                [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port)],
                cwd=ROOT / "backend", env=env, stdout=log_file, stderr=subprocess.STDOUT,
            )
            for _ in range(150):
                if child.poll() is not None:
                    raise RuntimeError("Uvicorn exited during startup; see temp log")
                try:
                    status, _, _ = request(base_url, "GET", "/api/v1/health")
                    if status == 200:
                        return child
                except (URLError, TimeoutError, OSError):
                    pass
                time.sleep(0.2)
            child.terminate()
            raise TimeoutError("Uvicorn did not become ready")

        def stop_server(child: subprocess.Popen) -> None:
            child.terminate()
            try:
                child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=5)

        def send_answers(path: str, body: dict) -> tuple[int, object]:
            status, parsed, _ = request(base_url, "POST", path + "/submit", body)
            return status, parsed

        try:
            server = start_server()
            created_status, run, _ = request(base_url, "POST", BASE,
                {"learner_id": learner, "assessment_version": "initial-v2", "idempotency_key": "b1-listening-run-one"})
            assert_check("create_run", created_status == 200 and isinstance(run, dict), created_status)
            run_id = run["run_id"]
            path = f"{BASE}/{run_id}/listening"
            before_status, before, _ = request(base_url, "GET", path + f"?learner_id={learner}")
            assert_check("not_started_is_empty", before_status == 200 and before["task_status"] == "not_started"
                         and before["audio_url"] is None and before["questions"] == [], before_status)

            start_status, started, _ = request(base_url, "POST", path + f"/start?learner_id={learner}")
            assert_check("start_and_pin", start_status == 200 and started["task_version"] == listening.CONTENT_VERSION
                         and started["task_status"] == "in_progress", start_status)
            repeat_start_status, repeat_started, _ = request(base_url, "POST", path + f"/start?learner_id={learner}")
            assert_check("repeated_start_reuses_original_session", repeat_start_status == 200
                         and repeat_started["learning_session_id"] == started["learning_session_id"]
                         and repeat_started == started, repeat_start_status)
            assert_check("pre_submit_no_answer_leak", all("correct_answer" not in q and "explanation" not in q for q in started["questions"])
                         and listening.SCRIPT not in json.dumps(started)
                         and listening.DESIGN_RATIONALE not in json.dumps(started), True)

            audio_status, _, audio_bytes = request(base_url, "GET", listening.AUDIO_URL)
            audio_hash = hashlib.sha256(audio_bytes).hexdigest().upper()
            with wave.open(BytesIO(audio_bytes), "rb") as audio:
                audio_info = {"sample_rate": audio.getframerate(), "channels": audio.getnchannels(),
                              "sample_width_bytes": audio.getsampwidth(), "duration_seconds": audio.getnframes()/audio.getframerate(),
                              "non_silent": any(audio.readframes(audio.getnframes()))}
            evidence["audio"] = {**audio_info, "http_status": audio_status, "sha256": audio_hash}
            assert_check("audio_is_playable_resource", audio_status == 200 and audio_hash == listening.AUDIO_SHA256, evidence["audio"])

            answers = {item["id"]: item["answer"] for item in listening.QUESTIONS}
            payload = {"learner_id": learner, "answers": answers, "idempotency_key": "b1-listening-submit-one"}
            with ThreadPoolExecutor(max_workers=2) as pool:
                responses = list(pool.map(lambda _: send_answers(path, payload), range(2)))
            assert_check("concurrent_duplicate_submission", all(status == 200 for status, _ in responses)
                         and responses[0][1] == responses[1][1] and responses[0][1]["correct_count"] == 6,
                         [status for status, _ in responses])
            result = responses[0][1]
            evidence["first_run"] = {"run_id": run_id, "learning_session_id": result["learning_session_id"],
                                     "score": result["correct_count"], "repeated_exposure": result["repeated_exposure"],
                                     "attempt_ids": [item["attempt_id"] for item in result["attempt_references"]]}
            retry_status, retry, _ = request(base_url, "POST", path + "/submit", payload)
            assert_check("same_request_retry", retry_status == 200 and retry == result, retry_status)
            changed = dict(payload)
            changed["answers"] = {item["id"]: next(opt for opt in item["options"] if opt != item["answer"])
                                  for item in listening.QUESTIONS}
            conflict_status, _, _ = request(base_url, "POST", path + "/submit", changed)
            assert_check("different_answer_conflict", conflict_status == 409, conflict_status)
            owner_status, _, _ = request(base_url, "GET", path + "?learner_id=another-learner")
            start_other_status, _, _ = request(base_url, "POST", path + "/start?learner_id=another-learner")
            submit_other = dict(payload, learner_id="another-learner")
            submit_other_status, _, _ = request(base_url, "POST", path + "/submit", submit_other)
            assert_check("cross_learner_boundary", [owner_status, start_other_status, submit_other_status] == [404, 404, 404],
                         [owner_status, start_other_status, submit_other_status])

            # The separate Reading task still starts and scores on the same run.
            reading_path = f"{BASE}/{run_id}/reading"
            reading_start_status, reading_started, _ = request(base_url, "POST", reading_path + f"/start?learner_id={learner}")
            reading_answers = {item["id"]: item["answer"] for item in V2_QUESTIONS}
            reading_submit_status, reading_result, _ = request(base_url, "POST", reading_path + "/submit",
                {"learner_id": learner, "answers": reading_answers, "idempotency_key": "b1-reading-regression"})
            assert_check("reading_assessment_regression", reading_start_status == 200 and reading_submit_status == 200
                         and reading_result["correct_count"] == 4 and reading_result["task_version"].endswith("-v2"),
                         {"start": reading_start_status, "submit": reading_submit_status})
            assert_check("skill_tasks_remain_independent", result["task_status"] == "completed"
                         and run["status"] == "in_progress", {"reading_started": reading_started["task_status"]})

            stop_server(server)
            server = None
            server = start_server()
            restore_run_status, restored_run, _ = request(base_url, "GET", f"{BASE}/{run_id}?learner_id={learner}")
            restore_task_status, restored, _ = request(base_url, "GET", path + f"?learner_id={learner}")
            assert_check("restart_restores_result", restore_run_status == 200 and restore_task_status == 200
                         and restored["learning_session_id"] == result["learning_session_id"]
                         and restored["attempt_references"] == result["attempt_references"]
                         and restored["correct_count"] == 6, [restore_run_status, restore_task_status])

            second_status, second, _ = request(base_url, "POST", BASE,
                {"learner_id": learner, "assessment_version": "initial-v2", "idempotency_key": "b1-listening-run-two"})
            second_path = f"{BASE}/{second['run_id']}/listening"
            second_start_status, second_started, _ = request(base_url, "POST", second_path + f"/start?learner_id={learner}")
            assert_check("second_run_repeated_exposure", second_status == 200 and second_start_status == 200
                         and second_started["repeated_exposure"] is True, [second_status, second_start_status])
            wrong = {item["id"]: next(opt for opt in item["options"] if opt != item["answer"])
                     for item in listening.QUESTIONS}
            second_result_status, second_result, _ = request(base_url, "POST", second_path + "/submit",
                {"learner_id": learner, "answers": wrong, "idempotency_key": "b1-listening-submit-two"})
            assert_check("second_run_keeps_real_wrong_observations", second_result_status == 200
                         and second_result["correct_count"] == 0 and second_result["repeated_exposure"] is True,
                         second_result_status)
            history_status, history, _ = request(base_url, "GET", BASE + f"?learner_id={learner}")
            assert_check("history_keeps_both_runs", history_status == 200 and {run_id, second["run_id"]} <= {r["run_id"] for r in history},
                         [r["run_id"] for r in history])

            old_status, old, _ = request(base_url, "POST", BASE,
                {"learner_id": learner, "assessment_version": "initial-v1", "idempotency_key": "b1-listening-legacy-v1"})
            old_path = f"{BASE}/{old['run_id']}/listening"
            old_get_status, _, _ = request(base_url, "GET", old_path + f"?learner_id={learner}")
            old_start_status, _, _ = request(base_url, "POST", old_path + f"/start?learner_id={learner}")
            assert_check("legacy_unpinned_v1_conflicts_without_mutation", old_status == 200
                         and old_get_status == 409 and old_start_status == 409, [old_get_status, old_start_status])

            # Ordinary practice stays a separate session/version and still scores server-side.
            ordinary_status, ordinary, _ = request(base_url, "POST", "/api/v1/listening/sessions",
                {"learner_id": learner})
            ordinary_session = ordinary["session_id"]
            ordinary_answers = {item["id"]: item["options"][0] for item in ordinary["lesson"]["questions"]}
            ordinary_submit_status, ordinary_result, _ = request(base_url, "POST",
                f"/api/v1/listening/sessions/{ordinary_session}/submit?learner_id={learner}",
                {"answers": ordinary_answers, "idempotency_key": f"listen-{ordinary_session}"})
            assert_check("ordinary_listening_remains_separate", ordinary_status == 201 and ordinary_submit_status == 200
                         and ordinary_result["status"] == "completed"
                         and ordinary["lesson"]["content_version"] != listening.CONTENT_VERSION,
                         [ordinary_status, ordinary_submit_status])

            model_status, model, _ = request(base_url, "GET", f"/api/v1/learner-model?learner_id={learner}")
            listening_model = next(item for item in model["skills"] if item["skill_id"] == "listening")
            assert_check("learner_model_maps_assessment_and_practice", model_status == 200
                         and any(e["evidence_type"] == "initial_assessment" for e in listening_model["evidence"])
                         and any(e["evidence_type"] == "practice_performance" for e in listening_model["evidence"]),
                         {"evidence": len(listening_model["evidence"]), "levels": listening_model["evidence_levels"]})

            stop_server(server)
            server = None
            with sqlite3.connect(database) as db:
                counts = {table: db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                          for table in ("assessment_runs", "assessment_tasks", "learning_sessions", "attempts")}
                session_ids = [evidence["first_run"]["learning_session_id"], second_result["learning_session_id"], ordinary_session,
                               reading_result["learning_session_id"]]
                per_session_attempts = {sid: db.execute("SELECT COUNT(*) FROM attempts WHERE session_id=?", (sid,)).fetchone()[0]
                                        for sid in session_ids}
            db.close()
            evidence["database"] = {"counts": counts, "attempts_by_session": per_session_attempts,
                                    "temporary_database_location": "system temp directory; not inside the repository"}
            assert_check("database_persistence_counts", counts["assessment_runs"] == 3 and counts["assessment_tasks"] == 12
                         and per_session_attempts[evidence["first_run"]["learning_session_id"]] == 6
                         and per_session_attempts[second_result["learning_session_id"]] == 6
                         and per_session_attempts[ordinary_session] == 5
                         and per_session_attempts[reading_result["learning_session_id"]] == 4, counts)
            evidence["status"] = "pass" if not evidence["failed"] else "fail"
        except Exception as error:
            evidence["status"] = "fail"
            evidence["error"] = f"{type(error).__name__}: {error}"
            if not evidence["failed"]:
                evidence["failed"].append("http_workflow")
            if log_path.exists():
                evidence["server_log_tail"] = log_path.read_text(encoding="utf-8", errors="replace")[-5000:]
        finally:
            if server is not None:
                stop_server(server)
            log_file.close()

    temp_path = Path(temp_name)
    for _ in range(8):
        if not temp_path.exists():
            break
        try:
            for child in temp_path.iterdir():
                if child.is_file():
                    child.unlink()
            temp_path.rmdir()
        except OSError:
            time.sleep(0.25)
    deleted = not temp_path.exists()
    evidence.setdefault("database", {})["isolated_database_deleted_after_run"] = deleted
    if not deleted:
        evidence["database"]["temporary_directory"] = str(temp_path)
        evidence["failed"].append("isolated_database_cleanup")
        evidence["status"] = "fail"

    evidence["request_counts"] = {}
    for item in event_log:
        key = f"{item['method']} {item['path'].split('?')[0]} {item['status']}"
        evidence["request_counts"][key] = evidence["request_counts"].get(key, 0) + 1
    EVIDENCE_PATH.parent.mkdir(parents=True, exist_ok=True)
    EVIDENCE_PATH.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": evidence["status"], "failed": evidence["failed"],
                      "evidence": str(EVIDENCE_PATH), "request_count": len(event_log)}, ensure_ascii=False))
    return 0 if evidence["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
