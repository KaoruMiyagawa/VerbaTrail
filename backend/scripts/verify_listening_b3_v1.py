"""Isolated Q1 browser network acceptance; preserves A3 artifacts."""
import json, os, subprocess
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
import uvicorn
import httpx
from sqlalchemy import func, select
from app.api.v1.dependencies import clear_engine_cache, get_settings
from app.config import Settings
from app.main import create_app
from app.persistence.database import create_db_engine, create_session_factory
from app.persistence.migrations_runner import upgrade_database
from app.persistence.models import AssessmentRun, Attempt
from scripts.verify_listening_browser import port, wait_http
from app.api.v1 import assessment_reading
from app.content.reading_initial_assessment import V1_VERSION, V1_QUESTIONS, VERSIONS

ROOT=Path(__file__).resolve().parents[2]
OUT=ROOT/'docs/development/listening-initial-assessment-b3'
def main():
 with TemporaryDirectory(prefix='assessment-q1-') as temp:
  ap,up,cp=port(),port(),port();api,ui,cdp=(f'http://127.0.0.1:{p}' for p in (ap,up,cp))
  settings=Settings(APP_ENV='testing',DATABASE_URL=f"sqlite:///{(Path(temp)/'isolated.sqlite3').as_posix()}",CORS_ORIGINS=ui,MODEL_PROVIDER='fake',MODEL_API_KEY='',ALLOW_REMOTE_MODEL_CALLS=False)
  engine=create_db_engine(settings);upgrade_database(database_url=settings.database_url,engine=engine)
  app=create_app(settings);app.dependency_overrides[get_settings]=lambda:settings
  server=uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=ap,log_level='warning'));thread=Thread(target=server.run,daemon=True);thread.start()
  env={**os.environ,'VITE_API_BASE_URL':api,'SMOKE_UI_URL':ui,'SMOKE_CDP_URL':cdp,'SMOKE_OUTPUT_DIR':str(OUT)}
  chrome=os.environ.get('CHROME_BIN',r'C:\Program Files\Google\Chrome\Application\chrome.exe');processes=[]
  try:
   wait_http(api+'/openapi.json')
   for args,cwd,url in [(['node','node_modules/vite/bin/vite.js','--host','127.0.0.1','--port',str(up),'--strictPort'],ROOT/'frontend',ui),([chrome,'--headless=new',f'--user-data-dir={Path(temp)/"chrome"}',f'--remote-debugging-port={cp}','--disable-gpu','about:blank'],ROOT,cdp+'/json/list')]:
    p=subprocess.Popen(args,cwd=cwd,env=env,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0),stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL);processes.append(p);wait_http(url,p)
   # Reproduce the old release's choice of V1 when creating historical fixtures,
   # using the real API and persisted records; restore the current handler before UI checks.
   original=assessment_reading._version_for
   assessment_reading._version_for=lambda task,run: VERSIONS[V1_VERSION] if not task.content_version and run.assessment_version=='initial-v1' else original(task,run)
   try:
    with httpx.Client(base_url=api,trust_env=False,timeout=10) as client:
     ids=[]
     for label in ('complete','started','unstarted'):
      r=client.post('/api/v1/assessment-runs',json={'learner_id':'default_learner','assessment_version':'initial-v1','idempotency_key':'q1-legacy-'+label});r.raise_for_status();run=r.json()['run_id'];ids.append(run)
      if label!='unstarted':
       r=client.post(f'/api/v1/assessment-runs/{run}/reading/start',params={'learner_id':'default_learner'});r.raise_for_status()
      if label=='complete':
       r=client.post(f'/api/v1/assessment-runs/{run}/reading/submit',json={'learner_id':'default_learner','answers':{q['id']:q['answer'] for q in V1_QUESTIONS},'idempotency_key':'q1-v1-submitted'});r.raise_for_status()
   finally:assessment_reading._version_for=original
   env['Q1_V1_IDS']=json.dumps(ids)
   subprocess.run(['node','scripts/verify-listening-b3-v1.mjs'],cwd=ROOT/'frontend',env=env,check=True)
   with httpx.Client(base_url=api,trust_env=False,timeout=10) as client:
    for run in ids[:1]:
     result=client.get(f'/api/v1/assessment-runs/{run}/reading',params={'learner_id':'default_learner'});result.raise_for_status()
     assert result.json()['task_version']==V1_VERSION and result.json()['correct_count']==4
    assert client.get(f'/api/v1/assessment-runs/{ids[2]}/reading',params={'learner_id':'default_learner'}).status_code==409
    model=client.get('/api/v1/learner-model',params={'learner_id':'default_learner'}).json()
    reading=next(s for s in model['skills'] if s['skill_id']=='reading')
    legacy=[e for e in reading['evidence'] if e['content_version']==V1_VERSION]
    assert len(legacy)==4 and all(e['assessment_status']=='not_assessed' for e in legacy)
   (OUT/'v1-model-evidence.json').write_text(json.dumps({'legacy_attempts':len(legacy),'legacy_all_not_assessed':True,'unstarted_status':409},indent=2),encoding='utf-8')
  finally:
   for p in reversed(processes):
    p.terminate()
    try:p.wait(timeout=10)
    except subprocess.TimeoutExpired:p.kill();p.wait()
   server.should_exit=True;thread.join(timeout=10);clear_engine_cache();engine.dispose()
if __name__=='__main__':main()
