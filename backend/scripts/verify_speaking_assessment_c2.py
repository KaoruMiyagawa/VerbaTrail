"""Verify Speaking assessment in Chrome against Uvicorn, Vite and disposable SQLite."""

import hashlib
import json
import os
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
from threading import Thread

from sqlalchemy import func, select
import uvicorn

from app.api.v1.dependencies import clear_engine_cache, get_settings
from app.config import Settings
from app.main import create_app
from app.persistence.database import create_db_engine, create_session_factory
from app.persistence.migrations_runner import upgrade_database
from app.persistence.models import AssessmentRun, AssessmentTask, Attempt, SpeakingAssessmentRecording, SpeakingPractice
from scripts.verify_listening_browser import port, wait_http

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'docs/development/speaking-initial-assessment-c2'
FORMAL = ROOT / 'backend/unlearnloop.db'


def main():
    before = hashlib.sha256(FORMAL.read_bytes()).hexdigest().upper()
    OUT.mkdir(parents=True, exist_ok=True)
    evidence = {'status': 'running', 'requests': []}
    with TemporaryDirectory(prefix='verbatrail-c2-') as temp:
        ap, up, cp = port(), port(), port()
        api, ui, cdp = (f'http://127.0.0.1:{p}' for p in (ap, up, cp))
        settings = Settings(
            APP_ENV='testing', DATABASE_URL=f"sqlite:///{(Path(temp) / 'isolated.sqlite').as_posix()}",
            CORS_ORIGINS=ui, MODEL_PROVIDER='fake', MODEL_API_KEY='', ALLOW_REMOTE_MODEL_CALLS=False,
        )
        engine = create_db_engine(settings)
        upgrade_database(database_url=settings.database_url, engine=engine)
        factory = create_session_factory(engine)
        app = create_app(settings)
        app.dependency_overrides[get_settings] = lambda: settings

        @app.middleware('http')
        async def trace(request, call_next):
            response = await call_next(request)
            evidence['requests'].append({'method': request.method, 'path': request.url.path, 'status': response.status_code})
            return response

        server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=ap, log_level='warning'))
        thread = Thread(target=server.run, daemon=True)
        thread.start()
        env = {**os.environ, 'VITE_API_BASE_URL': api, 'SMOKE_UI_URL': ui, 'SMOKE_CDP_URL': cdp, 'SMOKE_OUTPUT_DIR': str(OUT)}
        chrome = os.environ.get('CHROME_BIN', r'C:\Program Files\Google\Chrome\Application\chrome.exe')
        processes = []
        try:
            wait_http(api + '/openapi.json')
            for args, cwd, url in [
                (['node', 'node_modules/vite/bin/vite.js', '--host', '127.0.0.1', '--port', str(up), '--strictPort'], ROOT / 'frontend', ui),
                ([chrome, '--headless=new', f'--user-data-dir={Path(temp) / "chrome"}', f'--remote-debugging-port={cp}',
                  '--disable-gpu', '--use-fake-device-for-media-stream', '--use-fake-ui-for-media-stream', 'about:blank'], ROOT, cdp + '/json/list'),
            ]:
                process = subprocess.Popen(args, cwd=cwd, env=env, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
                                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                processes.append(process)
                wait_http(url, process)
            subprocess.run(['node', 'scripts/verify-speaking-assessment-c2.mjs'], cwd=ROOT / 'frontend', env=env, check=True)
            with factory() as db:
                counts = {name: db.scalar(select(func.count()).select_from(model)) for name, model in [
                    ('runs', AssessmentRun), ('registrations', SpeakingAssessmentRecording), ('attempts', Attempt), ('ordinary_speaking_practices', SpeakingPractice),
                ]}
                assert counts == {'runs': 1, 'registrations': 1, 'attempts': 10, 'ordinary_speaking_practices': 0}, counts
                run = db.scalars(select(AssessmentRun)).one()
                recording = db.scalars(select(SpeakingAssessmentRecording)).one()
                tasks = {task.skill_id: {'status': task.status, 'evidence_reference': task.evidence_reference}
                         for task in db.scalars(select(AssessmentTask))}
                assert run.status == 'in_progress'
                assert {skill: task['status'] for skill, task in tasks.items()} == {
                    'reading': 'completed', 'listening': 'completed', 'speaking': 'completed', 'writing': 'not_started',
                }
                assert tasks['speaking']['evidence_reference'] == recording.recording_id
                posts = [entry for entry in evidence['requests'] if entry['method'] == 'POST' and entry['path'].endswith('/speaking/complete')]
                assert len(posts) == 1 and posts[0]['status'] == 200
                browser = json.loads((OUT / 'browser.json').read_text(encoding='utf-8'))
                assert recording.recording_id == browser['registration']['body']['recording_id']
                assert recording.idempotency_key == browser['registration']['body']['idempotency_key']
                assert recording.duration_seconds == browser['registration']['body']['duration_seconds']
                evidence.update(status='pass', counts=counts, run_id=run.id, run_status=run.status,
                                recording_id=recording.recording_id, tasks=tasks)
        except Exception as error:
            evidence.update(status='fail', error=str(error))
            raise
        finally:
            for process in reversed(processes):
                if process.poll() is None:
                    process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            server.should_exit = True
            thread.join(timeout=10)
            clear_engine_cache()
            engine.dispose()
            after = hashlib.sha256(FORMAL.read_bytes()).hexdigest().upper()
            evidence['formal_database_sha256'] = {'before': before, 'after': after}
            (OUT / 'backend.json').write_text(json.dumps(evidence, indent=2) + '\n', encoding='utf-8')
            assert before == after


if __name__ == '__main__':
    main()
