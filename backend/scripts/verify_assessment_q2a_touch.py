"""Run the complete touch-only Reading assessment flow against isolated real services."""
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
from app.persistence.models import AssessmentRun, Attempt, LearningSession
from scripts.verify_listening_browser import port, wait_http

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "docs/development/assessment-a3-browser-evidence/q2a-touch"
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
    with TemporaryDirectory(prefix="assessment-q2a-touch-") as temp:
        temp_path = Path(temp)
        api_port, ui_port, cdp_port = port(), port(), port()
        api, ui, cdp = (f"http://127.0.0.1:{value}" for value in (api_port, ui_port, cdp_port))
        settings = Settings(
            APP_ENV="testing",
            DATABASE_URL=f"sqlite:///{(temp_path / 'isolated-assessment.sqlite3').as_posix()}",
            CORS_ORIGINS=ui,
            MODEL_PROVIDER="fake",
            MODEL_API_KEY="",
            ALLOW_REMOTE_MODEL_CALLS=False,
        )
        engine = create_db_engine(settings)
        upgrade_database(database_url=settings.database_url, engine=engine)
        app = create_app(settings)
        app.dependency_overrides[get_settings] = lambda: settings
        server_received = []

        @app.middleware("http")
        async def record_real_assessment_posts(request, call_next):
            response = await call_next(request)
            if request.url.path.startswith("/api/v1/assessment-runs"):
                entry = {"method": request.method, "path": request.url.path, "status": response.status_code}
                if request.method == "POST":
                    server_received.append(entry)
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
                ([chrome, "--headless=new", f"--user-data-dir={temp_path / 'chrome-touch-profile'}", f"--remote-debugging-port={cdp_port}", "--disable-gpu", "--touch-events=enabled", "about:blank"], ROOT, cdp + "/json/list"),
            ]
            for command, cwd, health_url in commands:
                process = subprocess.Popen(command, cwd=cwd, env=env, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                processes.append(process)
                wait_http(health_url, process)
            subprocess.run(["node", "scripts/verify-assessment-q2a-touch.mjs"], cwd=ROOT / "frontend", env=env, check=True)

            with create_session_factory(engine)() as db:
                counts = {model.__name__: db.scalar(select(func.count()).select_from(model)) for model in (AssessmentRun, LearningSession, Attempt)}
                runs = db.scalars(select(AssessmentRun).options(selectinload(AssessmentRun.tasks))).all()
                sessions = db.scalars(select(LearningSession)).all()
                attempts = db.scalars(select(Attempt).order_by(Attempt.submitted_at)).all()
                assert counts == {"AssessmentRun": 1, "LearningSession": 1, "Attempt": 4}, counts
                assert len(runs) == len(sessions) == 1
                assert all(task.status == "completed" for task in runs[0].tasks if task.skill_id == "reading")
                assert all(task.status == "not_started" for task in runs[0].tasks if task.skill_id != "reading")
                assert len(server_received) == 3 and all(item["status"] == 200 for item in server_received), server_received
                assert len(attempts) == 4 and len({item.session_id for item in attempts}) == 1
                details = {
                    "counts": counts,
                    "run_id": runs[0].id,
                    "assessment_version": runs[0].assessment_version,
                    "run_status": runs[0].status,
                    "tasks": [{"skill_id": task.skill_id, "status": task.status, "session_id": task.learning_session_id, "evidence_reference": task.evidence_reference, "content_version": task.content_version} for task in runs[0].tasks],
                    "sessions": [{"session_id": session.id, "learner_id": session.learner_id, "status": session.status} for session in sessions],
                    "attempts": [{"attempt_id": item.id, "session_id": item.session_id, "question_id": item.question_id, "submitted_answer": item.submitted_answer, "is_correct": item.is_correct} for item in attempts],
                    "server_received_posts": server_received,
                    "temporary_database": "isolated SQLite; temporary directory deleted after runner exits",
                }
                (OUT / "database-and-http.json").write_text(json.dumps(details, indent=2), encoding="utf-8")
                browser_report_path = OUT / "touch-report.json"
                browser_report = json.loads(browser_report_path.read_text(encoding="utf-8"))
                browser_report["session_id"] = sessions[0].id
                browser_report["database_counts"] = counts
                browser_report["server_http_posts"] = server_received
                browser_report["server_score_correct_count"] = sum(bool(item.is_correct) for item in attempts)
                browser_report_path.write_text(json.dumps(browser_report, indent=2), encoding="utf-8")
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
    after_hash = sha256(FORMAL_DB)
    assert after_hash == formal_hash, "Formal SQLite changed during isolated touch acceptance"
    (OUT / "formal-database-protection.json").write_text(json.dumps({"path": "backend/unlearnloop.db", "sha256_before": formal_hash, "sha256_after": after_hash, "unchanged": True}, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
