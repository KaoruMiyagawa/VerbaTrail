"""Isolated V1/V2 browser draft acceptance using real API and services."""
import json, os, subprocess
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
import httpx, uvicorn
from app.api.v1 import assessment_reading
from app.api.v1.dependencies import clear_engine_cache, get_settings
from app.config import Settings
from app.content.reading_initial_assessment import V1_VERSION, VERSIONS
from app.main import create_app
from app.persistence.database import create_db_engine
from app.persistence.migrations_runner import upgrade_database
from scripts.verify_listening_browser import port, wait_http

ROOT=Path(__file__).resolve().parents[2]
OUT=ROOT/'docs/development/assessment-a3-browser-evidence/q2a-draft'
def main():
 with TemporaryDirectory(prefix='assessment-draft-') as temp:
  ap,up,cp=port(),port(),port();api,ui,cdp=(f'http://127.0.0.1:{p}' for p in (ap,up,cp))
  settings=Settings(APP_ENV='testing',DATABASE_URL=f"sqlite:///{(Path(temp)/'isolated.sqlite3').as_posix()}",CORS_ORIGINS=ui,MODEL_PROVIDER='fake',MODEL_API_KEY='',ALLOW_REMOTE_MODEL_CALLS=False)
  engine=create_db_engine(settings);upgrade_database(database_url=settings.database_url,engine=engine)
  app=create_app(settings);app.dependency_overrides[get_settings]=lambda:settings
  server=uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=ap,log_level='warning'));thread=Thread(target=server.run,daemon=True);thread.start()
  env={**os.environ,'VITE_API_BASE_URL':api,'SMOKE_UI_URL':ui,'SMOKE_CDP_URL':cdp,'SMOKE_OUTPUT_DIR':str(OUT)}
  chrome=os.environ.get('CHROME_BIN',r'C:\Program Files\Google\Chrome\Application\chrome.exe');processes=[]
  try:
   wait_http(api+'/openapi.json')
   with httpx.Client(base_url=api,trust_env=False,timeout=10) as c:
    ids=[];original=assessment_reading._version_for
    for idx,version in enumerate(('initial-v1','initial-v2','initial-v2')):
     r=c.post('/api/v1/assessment-runs',json={'learner_id':'default_learner','assessment_version':version,'idempotency_key':f'draft-fixture-{idx}'});r.raise_for_status();run=r.json()['run_id'];ids.append(run)
     if idx==0:assessment_reading._version_for=lambda task,run: VERSIONS[V1_VERSION] if not task.content_version and run.assessment_version=='initial-v1' else original(task,run)
     try:r=c.post(f'/api/v1/assessment-runs/{run}/reading/start',params={'learner_id':'default_learner'});r.raise_for_status()
     finally:assessment_reading._version_for=original
    history=c.get('/api/v1/assessment-runs',params={'learner_id':'default_learner'}).json();env['DRAFT_IDS']=json.dumps(ids);env['DRAFT_HISTORY']=json.dumps([x['run_id'] for x in history])
   for args,cwd,url in [(['node','node_modules/vite/bin/vite.js','--host','127.0.0.1','--port',str(up),'--strictPort'],ROOT/'frontend',ui),([chrome,'--headless=new',f'--user-data-dir={Path(temp)/"chrome"}',f'--remote-debugging-port={cp}','--disable-gpu','about:blank'],ROOT,cdp+'/json/list')]:
    p=subprocess.Popen(args,cwd=cwd,env=env,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0),stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL);processes.append(p);wait_http(url,p)
   subprocess.run(['node','scripts/verify-assessment-draft.mjs'],cwd=ROOT/'frontend',env=env,check=True)
  finally:
   for p in reversed(processes):
    p.terminate()
    try:p.wait(timeout=10)
    except subprocess.TimeoutExpired:p.kill();p.wait()
   server.should_exit=True;thread.join(timeout=10);clear_engine_cache();engine.dispose()
if __name__=='__main__':main()
