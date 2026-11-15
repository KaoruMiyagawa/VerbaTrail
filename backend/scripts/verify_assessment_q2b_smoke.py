"""Four-skill browser smoke against current migrations and an isolated SQLite database."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread

import httpx
import uvicorn
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.api.v1.dependencies import clear_engine_cache, get_settings
from app.config import Settings
from app.main import create_app
from app.persistence.database import create_db_engine, create_session_factory
from app.persistence.migrations_runner import upgrade_database
from app.persistence.models import (
    AssessmentRun, AssessmentTask, Attempt, KnowledgeTopic, LearningSession,
    SpeakingPractice, WritingDraft, WritingRevision,
)
from scripts.verify_listening_browser import port, wait_http
from scripts.verify_matching_headings_transfer_review_http import _seed

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "docs/development/assessment-a3-browser-evidence/q2b"
FORMAL_DB = ROOT / "backend/unlearnloop.db"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    formal_hash = sha256(FORMAL_DB)
    with TemporaryDirectory(prefix="verbatrail-q2b-") as temp:
        base = Path(temp)
        api_port, ui_port, cdp_port = port(), port(), port()
        api, ui, cdp = (f"http://127.0.0.1:{value}" for value in (api_port, ui_port, cdp_port))
        db_url = f"sqlite:///{(base / 'isolated-q2b.sqlite3').as_posix()}"
        settings = Settings(
            APP_ENV="testing", DATABASE_URL=db_url, CORS_ORIGINS=ui,
            MODEL_PROVIDER="fake", MODEL_API_KEY="", ALLOW_REMOTE_MODEL_CALLS=False,
        )
        engine = create_db_engine(settings)
        upgrade_database(database_url=db_url, engine=engine)
        factory = create_session_factory(engine)
        _seed(factory)
        app = create_app(settings)
        app.dependency_overrides[get_settings] = lambda: settings
        server_requests: list[dict[str, object]] = []

        @app.middleware("http")
        async def record_api_responses(request, call_next):
            response = await call_next(request)
            if request.url.path.startswith("/api/v1/"):
                server_requests.append({"method": request.method, "path": request.url.path, "status": response.status_code})
            return response

        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=api_port, log_level="warning"))
        server_thread = Thread(target=server.run, daemon=True)
        server_thread.start()
        chrome = os.environ.get("CHROME_BIN", r"C:\Program Files\Google\Chrome\Application\chrome.exe")
        env = {
            **os.environ, "VITE_API_BASE_URL": api, "SMOKE_API_URL": api,
            "SMOKE_UI_URL": ui, "SMOKE_CDP_URL": cdp, "SMOKE_OUTPUT_DIR": str(OUT),
        }
        processes = []
        browser_exit = 1
        setup_http: dict[str, object] = {}
        try:
            wait_http(api + "/openapi.json")
            with httpx.Client(base_url=api, trust_env=False, timeout=15) as client:
                response = client.put("/api/v1/profile/exam-preparation", params={"learner_id": "default_learner"}, json={
                    "exam_variant": "academic", "target_overall_band": 8.0,
                    "target_skill_bands": {"listening": 8.0, "reading": 8.0, "writing": 8.0, "speaking": 8.0},
                })
                assert response.status_code == 200, response.text
                setup_http["save_academic_exam_profile"] = response.status_code
                assert response.json()["actual_level_status"] == "not_assessed"
            for command, cwd, url in [
                (["node", "node_modules/vite/bin/vite.js", "--host", "127.0.0.1", "--port", str(ui_port), "--strictPort"], ROOT / "frontend", ui),
                ([chrome, "--headless=new", f"--user-data-dir={base / 'chrome-q2b-profile'}", f"--remote-debugging-port={cdp_port}", "--disable-gpu", "--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream", "--autoplay-policy=no-user-gesture-required", "about:blank"], ROOT, cdp + "/json/list"),
            ]:
                process = subprocess.Popen(command, cwd=cwd, env=env, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                processes.append(process)
                wait_http(url, process)
            completed = subprocess.run(["node", "scripts/verify-assessment-q2b-smoke.mjs"], cwd=ROOT / "frontend", env=env, check=False, timeout=300)
            browser_exit = completed.returncode
        finally:
            for process in reversed(processes):
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            server.should_exit = True
            server_thread.join(timeout=10)

        with factory() as db:
            attempts = db.scalars(select(Attempt).order_by(Attempt.submitted_at)).all()
            sessions = db.scalars(select(LearningSession)).all()
            topics = {topic.id: topic.domain_code for topic in db.scalars(select(KnowledgeTopic)).all()}
            revisions = db.scalars(select(WritingRevision).order_by(WritingRevision.number)).all()
            speaking = db.scalars(select(SpeakingPractice)).all()
            counts = {model.__name__: db.scalar(select(func.count()).select_from(model)) for model in (LearningSession, Attempt, SpeakingPractice, WritingDraft, WritingRevision, AssessmentRun, AssessmentTask)}
            reading_attempts = [item for item in attempts if topics.get(db.get(LearningSession, item.session_id).topic_id) == "ielts_academic"]
            listening_attempts = [item for item in attempts if topics.get(db.get(LearningSession, item.session_id).topic_id) == "ielts_listening"]
            db_report = {
                "counts": counts,
                "reading": {"attempt_ids": [item.id for item in reading_attempts], "session_ids": sorted({item.session_id for item in reading_attempts}), "evaluation_statuses": sorted({item.evaluation_status for item in reading_attempts})},
                "listening": {"attempt_ids": [item.id for item in listening_attempts], "session_ids": sorted({item.session_id for item in listening_attempts}), "evaluation_sources": sorted({(item.item_evaluation_json or {}).get("evaluation_source") for item in listening_attempts})},
                "speaking": [{"recording_id": item.recording_id, "session_id": item.session_id, "evaluation_status": item.evaluation_status, "evidence_type": item.evidence_type} for item in speaking],
                "writing": {"revision_ids": [item.id for item in revisions], "session_ids": sorted({item.session_id for item in revisions}), "revision_numbers": [item.number for item in revisions], "quality_evaluation": "not_assessed"},
                "assessment_runs": db.scalar(select(func.count()).select_from(AssessmentRun)),
                "assessment_tasks": db.scalar(select(func.count()).select_from(AssessmentTask)),
                "temporary_database": "isolated SQLite at current Alembic head; deleted after runner exits",
            }
        (OUT / "database-evidence.json").write_text(json.dumps(db_report, indent=2), encoding="utf-8")
        (OUT / "server-http-requests.json").write_text(json.dumps({"setup_requests": setup_http, "api_requests": server_requests}, indent=2), encoding="utf-8")
        browser_path = OUT / "browser-report.json"
        browser_report = json.loads(browser_path.read_text(encoding="utf-8")) if browser_path.exists() else {"status": "blocked", "failures": [{"message": "browser script did not write a report"}], "skills": {skill: {"status": "not_run"} for skill in ("reading", "listening", "speaking", "writing")}}
        summary = {
            "status": "pass" if browser_exit == 0 and browser_report.get("status") == "pass" else "blocked",
            "skills": browser_report.get("skills", {}),
            "navigation": browser_report.get("navigation", {}),
            "browser_exit_code": browser_exit,
            "database": db_report,
            "browser_report": "browser-report.json",
            "http_evidence": "server-http-requests.json",
            "database_evidence": "database-evidence.json",
        }
        (OUT / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
        (OUT / "server-http-requests.json").write_text(json.dumps({"setup_requests": setup_http, "api_requests": server_requests, "browser_requests": browser_report.get("requests", [])}, indent=2), encoding="utf-8")
        if browser_exit == 0:
            assert len(reading_attempts) >= 1, "No real ordinary Reading Attempt was persisted"
            assert len(listening_attempts) == 5, "Listening did not persist its five objectively scored answers"
            assert len(speaking) == 1 and speaking[0].evaluation_status == "not_assessed"
            assert len(revisions) == 2 and [item.number for item in revisions] == [1, 2]
            assert db_report["assessment_runs"] == db_report["assessment_tasks"] == 0
        clear_engine_cache()
        engine.dispose()
    after_hash = sha256(FORMAL_DB)
    assert after_hash == formal_hash, "Formal SQLite changed during Q2B smoke acceptance"
    (OUT / "formal-database-protection.json").write_text(json.dumps({"path": "backend/unlearnloop.db", "sha256_before": formal_hash, "sha256_after": after_hash, "unchanged": True}, indent=2), encoding="utf-8")
    if browser_exit != 0:
        raise SystemExit(f"Q2B browser smoke blocked; see {OUT / 'summary.json'}")


if __name__ == "__main__":
    main()
