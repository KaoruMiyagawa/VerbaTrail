"""Run C3 against migrated SQLite, Uvicorn, Vite and two temporary Chrome profiles."""

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
from threading import Thread
import time

import httpx
from sqlalchemy import select
import uvicorn

from app.api.v1.dependencies import clear_engine_cache, get_settings
from app.config import Settings
from app.content.listening_initial_assessment import QUESTIONS as LISTENING_QUESTIONS
from app.content.reading_initial_assessment import V2_QUESTIONS
from app.main import create_app
from app.persistence.database import create_db_engine, create_session_factory
from app.persistence.migrations_runner import upgrade_database
from app.persistence.models import AssessmentRun, AssessmentTask, Attempt, SpeakingAssessmentRecording, SpeakingPractice
from scripts.verify_listening_browser import port, wait_http

ROOT = Path(__file__).resolve().parents[2]
BASE = '/api/v1/assessment-runs'
FORMAL = ROOT / 'backend/unlearnloop.db'
SHA = 'BC4656AA2BC624F0A257254BA25597DB3031126FDFCBE79E5F3BB0CB607793E0'


def prepare(api):
    with httpx.Client(base_url=api, trust_env=False) as client:
        def post(path, body=None):
            response = client.post(path, json=body)
            response.raise_for_status()
            return response.json()

        runs = {name: post(BASE, {'idempotency_key': f'c3-{name}', 'assessment_version': version})['run_id']
                for name, version in [('a', 'initial-v2'), ('b', 'initial-v2'), ('legacy', 'initial-v1')]}
        for skill, questions in [('reading', V2_QUESTIONS), ('listening', LISTENING_QUESTIONS)]:
            path = f"{BASE}/{runs['a']}/{skill}"
            post(path + '/start')
            result = post(path + '/submit', {'learner_id': 'default_learner', 'answers': {question['id']: question['answer'] for question in questions},
                                              'idempotency_key': f'c3-{skill}'})
            assert result['correct_count'] == len(questions)
        return runs


def inspect_database(factory, report, browser):
    with factory() as db:
        runs = list(db.scalars(select(AssessmentRun)))
        tasks = list(db.scalars(select(AssessmentTask)))
        registrations = list(db.scalars(select(SpeakingAssessmentRecording)))
        by_task = {item.id: item for item in tasks}
        rows = {by_task[item.assessment_task_id].run_id: {
            'recording_id': item.recording_id, 'idempotency_key': item.idempotency_key,
            'duration_seconds': item.duration_seconds, 'recorded_at': item.recorded_at.isoformat(),
            'task_evidence_reference': by_task[item.assessment_task_id].evidence_reference,
        } for item in registrations}
        report['database'] = {
            'runs': {run.id: {'status': run.status, 'version': run.assessment_version,
                              'tasks': {item.skill_id: {'status': item.status, 'version': item.content_version,
                                                        'evidence_reference': item.evidence_reference}
                                        for item in tasks if item.run_id == run.id}} for run in runs},
            'registrations': rows, 'registration_count': len(registrations),
            'attempt_count': len(db.scalars(select(Attempt)).all()),
            'ordinary_speaking_count': len(db.scalars(select(SpeakingPractice)).all()),
        }
        assert len(runs) == 5 and len(rows) == len(registrations) == 4
        assert report['database']['attempt_count'] == 10 and report['database']['ordinary_speaking_count'] == 0
        assert set(rows) == set(browser['registrations'])
        for run_id, body in browser['registrations'].items():
            row = rows[run_id]
            for key in ('recording_id', 'idempotency_key', 'duration_seconds'):
                assert row[key] == body[key]
            assert row['task_evidence_reference'] == body['recording_id']
            task_states = report['database']['runs'][run_id]['tasks']
            assert task_states['writing']['status'] == 'not_started'
            assert task_states['speaking']['version'] == body['task_version']
        assert all(run.status == 'in_progress' for run in runs)
        for run_id in rows:
            posts = [item for item in report['requests'] if item['method'] == 'POST' and item['path'] == f'{BASE}/{run_id}/speaking/complete']
            assert len(posts) == 1 and posts[0]['status'] == 200 and posts[0]['body'] == browser['registrations'][run_id]


def main():
    before = hashlib.sha256(FORMAL.read_bytes()).hexdigest().upper()
    assert before == SHA
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    out = ROOT / 'docs/development/speaking-initial-assessment-c3' / stamp
    out.mkdir(parents=True)
    report = {'status': 'running', 'requests': []}
    with TemporaryDirectory(prefix='verbatrail-c3-') as directory:
        temp = Path(directory)
        ap, up, cp, second_port = (port() for _ in range(4))
        api, ui, cdp, second = (f'http://127.0.0.1:{p}' for p in (ap, up, cp, second_port))
        settings = Settings(APP_ENV='testing', DATABASE_URL=f"sqlite:///{(temp / 'c3.sqlite').as_posix()}",
                            CORS_ORIGINS=ui, MODEL_PROVIDER='fake', MODEL_API_KEY='', ALLOW_REMOTE_MODEL_CALLS=False)
        engine = create_db_engine(settings)
        upgrade_database(database_url=settings.database_url, engine=engine)
        factory = create_session_factory(engine)
        app = create_app(settings)
        app.dependency_overrides[get_settings] = lambda: settings

        @app.middleware('http')
        async def trace(request, call_next):
            path = request.url.path
            entry = {'method': request.method, 'path': path, 'at_ms': time.time_ns() // 1_000_000}
            if request.method == 'POST' and path.endswith('/speaking/complete'):
                entry['body'] = await request.json()
            response = await call_next(request)
            report['requests'].append({**entry, 'status': response.status_code, 'response_at_ms': time.time_ns() // 1_000_000})
            return response

        server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=ap, log_level='warning'))
        thread = Thread(target=server.run, daemon=True)
        thread.start()
        env = {**os.environ, 'VITE_API_BASE_URL': api, 'SMOKE_UI_URL': ui, 'SMOKE_CDP_URL': cdp,
               'SMOKE_SECOND_CDP': second, 'SMOKE_OUTPUT_DIR': str(out)}
        chrome = os.environ.get('CHROME_BIN', r'C:\Program Files\Google\Chrome\Application\chrome.exe')
        processes = []
        try:
            wait_http(api + '/openapi.json')
            env['SMOKE_RUNS'] = json.dumps(prepare(api))
            instances = [(['node', 'node_modules/vite/bin/vite.js', '--host', '127.0.0.1', '--port', str(up), '--strictPort'], ROOT / 'frontend', ui)]
            for name, debug_port, url in [('primary', cp, cdp), ('separate-profile', second_port, second)]:
                instances.append(([chrome, '--headless=new', f'--user-data-dir={temp / name}', f'--remote-debugging-port={debug_port}',
                                   '--disable-gpu', '--use-fake-device-for-media-stream', 'about:blank'], ROOT, url + '/json/list'))
            for args, cwd, url in instances:
                process = subprocess.Popen(args, cwd=cwd, env=env, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
                                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                processes.append(process)
                wait_http(url, process)
            subprocess.run(['node', 'scripts/verify-speaking-assessment-c3.mjs'], cwd=ROOT / 'frontend', env=env, check=True)
            browser = json.loads((out / 'browser.json').read_text(encoding='utf-8'))
            inspect_database(factory, report, browser)
            report['status'] = 'pass'
        except Exception as error:
            report.update(status='fail', error=str(error))
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
            report['formal_database_sha256'] = {'before': before, 'after': after}
            (out / 'backend.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
            print(f'C3 {report["status"]}: {out.relative_to(ROOT)}', flush=True)
            assert before == after


if __name__ == '__main__':
    main()
