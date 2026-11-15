"""Visible keyboard-focus acceptance against isolated real services and Chrome."""
import hashlib
import json
import os
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread

import uvicorn
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.api.v1.dependencies import clear_engine_cache, get_settings
from app.config import Settings
from app.main import create_app
from app.persistence.database import create_db_engine, create_session_factory
from app.persistence.migrations_runner import upgrade_database
from app.persistence.models import AssessmentRun, AssessmentTask, Attempt, LearningSession
from scripts.verify_listening_browser import port, wait_http

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "docs/development/assessment-a3-browser-evidence/q2a-focus"
FORMAL_DB = ROOT / "backend/unlearnloop.db"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    protected_hash = sha256(FORMAL_DB)
    with TemporaryDirectory(prefix="assessment-q2a-focus-") as temp:
        temp_path = Path(temp)
        api_port, ui_port, cdp_port = port(), port(), port()
        api, ui, cdp = (f"http://127.0.0.1:{value}" for value in (api_port, ui_port, cdp_port))
        db_path = temp_path / "assessment-focus.sqlite3"
        settings = Settings(
            APP_ENV="testing",
            DATABASE_URL=f"sqlite:///{db_path.as_posix()}",
            CORS_ORIGINS=ui,
            MODEL_PROVIDER="fake",
            MODEL_API_KEY="",
            ALLOW_REMOTE_MODEL_CALLS=False,
        )
        engine = create_db_engine(settings)
        upgrade_database(database_url=settings.database_url, engine=engine)
        app = create_app(settings)
        app.dependency_overrides[get_settings] = lambda: settings
        received_posts = []

        @app.middleware("http")
        async def audit_assessment_posts(request, call_next):
            response = await call_next(request)
            if request.method == "POST" and request.url.path.startswith("/api/v1/assessment-runs"):
                received_posts.append({"path": request.url.path, "status": response.status_code})
            return response

        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=api_port, log_level="warning"))
        server_thread = Thread(target=server.run, daemon=True)
        server_thread.start()
        env = {
            **os.environ,
            "VITE_API_BASE_URL": api,
            "SMOKE_UI_URL": ui,
            "SMOKE_CDP_URL": cdp,
            "SMOKE_OUTPUT_DIR": str(OUT),
        }
        chrome = os.environ.get("CHROME_BIN", r"C:\Program Files\Google\Chrome\Application\chrome.exe")
        processes = []
        try:
            wait_http(api + "/openapi.json")
            commands = [
                (["node", "node_modules/vite/bin/vite.js", "--host", "127.0.0.1", "--port", str(ui_port), "--strictPort"], ROOT / "frontend", ui),
                ([chrome, "--headless=new", f"--user-data-dir={temp_path / 'chrome-profile'}", f"--remote-debugging-port={cdp_port}", "--disable-gpu", "about:blank"], ROOT, cdp + "/json/list"),
            ]
            for command, cwd, health_url in commands:
                process = subprocess.Popen(command, cwd=cwd, env=env, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                processes.append(process)
                wait_http(health_url, process)
            subprocess.run(["node", "scripts/verify-assessment-q2a-focus.mjs"], cwd=ROOT / "frontend", env=env, check=True)

            with create_session_factory(engine)() as db:
                counts = {model.__name__: db.scalar(select(func.count()).select_from(model)) for model in (AssessmentRun, LearningSession, Attempt)}
                runs = db.scalars(select(AssessmentRun).options(selectinload(AssessmentRun.tasks)).order_by(AssessmentRun.created_at)).all()
                sessions = db.scalars(select(LearningSession).order_by(LearningSession.started_at)).all()
                attempts = db.scalars(select(Attempt).order_by(Attempt.submitted_at)).all()
                records = {
                    "counts": counts,
                    "runs": [{"run_id": run.id, "learner_id": run.learner_id, "assessment_version": run.assessment_version, "status": run.status} for run in runs],
                    "tasks": [{"run_id": task.run_id, "skill_id": task.skill_id, "status": task.status, "learning_session_id": task.learning_session_id, "evidence_reference": task.evidence_reference} for run in runs for task in run.tasks],
                    "sessions": [{"session_id": session.id, "learner_id": session.learner_id, "status": session.status} for session in sessions],
                    "attempts": [{"attempt_id": attempt.id, "session_id": attempt.session_id, "question_id": attempt.question_id, "submitted_at": attempt.submitted_at.isoformat(), "is_correct": attempt.is_correct} for attempt in attempts],
                    "server_received_assessment_posts": received_posts,
                    "temporary_database": "isolated SQLite (deleted after this runner exits)",
                }
                assert counts == {"AssessmentRun": 2, "LearningSession": 2, "Attempt": 8}, counts
                assert len(received_posts) == 6, received_posts
                assert all(task.status == "completed" for run in runs for task in run.tasks if task.skill_id == "reading")
                assert all(task.status == "not_started" for run in runs for task in run.tasks if task.skill_id != "reading")
                (OUT / "database-and-post-audit.json").write_text(json.dumps(records, indent=2), encoding="utf-8")
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
            clear_engine_cache()
            engine.dispose()
    assert sha256(FORMAL_DB) == protected_hash, "Formal SQLite hash changed during isolated browser acceptance"
    (OUT / "formal-database-protection.json").write_text(json.dumps({"path": "backend/unlearnloop.db", "sha256_before": protected_hash, "sha256_after": sha256(FORMAL_DB), "unchanged": True}, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
