"""Real browser response-loss test with SQLite and independent services."""
import json, os, subprocess
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
import uvicorn
from sqlalchemy import select, func
from app.api.v1.dependencies import clear_engine_cache, get_settings
from app.config import Settings
from app.main import create_app
from app.persistence.database import create_db_engine, create_session_factory
from app.persistence.migrations_runner import upgrade_database
from app.persistence.models import AssessmentRun, AssessmentTask, LearningSession, Attempt
from scripts.verify_listening_browser import port, wait_http

ROOT=Path(__file__).resolve().parents[2]
OUT=ROOT/'docs/development/listening-initial-assessment-b3'
def main():
 with TemporaryDirectory(prefix='assessment-lost-') as temp:
  ap,up,cp=port(),port(),port();api,ui,cdp=(f'http://127.0.0.1:{p}' for p in (ap,up,cp))
  settings=Settings(APP_ENV='testing',DATABASE_URL=f"sqlite:///{(Path(temp)/'isolated.sqlite3').as_posix()}",CORS_ORIGINS=ui,MODEL_PROVIDER='fake',MODEL_API_KEY='',ALLOW_REMOTE_MODEL_CALLS=False)
  engine=create_db_engine(settings);upgrade_database(database_url=settings.database_url,engine=engine);factory=create_session_factory(engine)
  app=create_app(settings);app.dependency_overrides[get_settings]=lambda:settings;received=[]
  @app.middleware('http')
  async def trace(request,call_next):
   if request.url.path.startswith('/api/v1/assessment-runs'):
    entry={'method':request.method,'path':request.url.path}
    if request.method=='POST' and request.url.path.endswith('/listening/submit'):
     entry['body']=json.loads(await request.body())
     with factory() as db:entry['attempt_count_before']=db.scalar(select(func.count()).select_from(Attempt))
    response=await call_next(request);entry['status']=response.status_code
    if request.method=='POST' and request.url.path.endswith('/listening/submit'):
     with factory() as db:entry['attempt_count_after']=db.scalar(select(func.count()).select_from(Attempt))
    received.append(entry);return response
   return await call_next(request)
  server=uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=ap,log_level='warning'));thread=Thread(target=server.run,daemon=True);thread.start()
  env={**os.environ,'VITE_API_BASE_URL':api,'SMOKE_UI_URL':ui,'SMOKE_CDP_URL':cdp,'SMOKE_OUTPUT_DIR':str(OUT)}
  chrome=os.environ.get('CHROME_BIN',r'C:\Program Files\Google\Chrome\Application\chrome.exe');processes=[]
  try:
   wait_http(api+'/openapi.json')
   for args,cwd,url in [(['node','node_modules/vite/bin/vite.js','--host','127.0.0.1','--port',str(up),'--strictPort'],ROOT/'frontend',ui),([chrome,'--headless=new',f'--user-data-dir={Path(temp)/"chrome"}',f'--remote-debugging-port={cp}','--disable-gpu','about:blank'],ROOT,cdp+'/json/list')]:
    p=subprocess.Popen(args,cwd=cwd,env=env,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0),stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL);processes.append(p);wait_http(url,p)
   subprocess.run(['node','scripts/verify-listening-b3-loss.mjs'],cwd=ROOT/'frontend',env=env,check=True)
   browser=json.loads((OUT/'browser-loss.json').read_text(encoding='utf-8'));submits=[e for e in received if e['path'].endswith('/listening/submit') and e['method']=='POST'];assert len(submits)==1 and submits[0]['status']==200
   assert submits[0]['attempt_count_before']==0 and submits[0]['attempt_count_after']==6
   with factory() as db:
    runs=db.scalars(select(AssessmentRun)).all();sessions=db.scalars(select(LearningSession)).all();attempts=db.scalars(select(Attempt)).all();task=db.scalar(select(AssessmentTask).where(AssessmentTask.run_id==browser['run_id'],AssessmentTask.skill_id=='listening'))
    assert len(runs)==1 and len(sessions)==1 and len(attempts)==6 and task.status=='completed' and sessions[0].status=='completed'
    assert {a.question_id:a.submitted_answer for a in attempts}==submits[0]['body']['answers']
    db_result={'run_id':runs[0].id,'task_status':task.status,'session_id':sessions[0].id,'session_status':sessions[0].status,'attempt_ids':[a.id for a in attempts],'assessment_runs':len(runs),'learning_sessions':len(sessions),'attempts':len(attempts),'answers_match':True}
   (OUT/'backend-evidence.json').write_text(json.dumps({'backend_requests':received,'proxy':'none: Chrome DevTools response interception','database':db_result},ensure_ascii=False,indent=2),encoding='utf-8')
  finally:
   for p in reversed(processes):
    p.terminate()
    try:p.wait(timeout=10)
    except subprocess.TimeoutExpired:p.kill();p.wait()
   server.should_exit=True;thread.join(timeout=10);clear_engine_cache();engine.dispose()
if __name__=='__main__':main()
