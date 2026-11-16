"""B2 real Chrome integration, isolated SQLite, Uvicorn and Vite."""
import hashlib,json,os,subprocess
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
import uvicorn
from sqlalchemy import select,func
from app.api.v1.dependencies import clear_engine_cache,get_settings
from app.config import Settings
from app.main import create_app
from app.persistence.database import create_db_engine,create_session_factory
from app.persistence.migrations_runner import upgrade_database
from app.persistence.models import AssessmentRun,AssessmentTask,LearningSession,Attempt
from scripts.verify_listening_browser import port,wait_http
ROOT=Path(__file__).resolve().parents[2]
OUT=ROOT/'docs/development/listening-initial-assessment-b2'
def main():
 OUT.mkdir(parents=True,exist_ok=True)
 before=hashlib.sha256((ROOT/'backend/unlearnloop.db').read_bytes()).hexdigest()
 with TemporaryDirectory(prefix='verbatrail-b2-') as temp:
  ap,up,cp=port(),port(),port();api,ui,cdp=(f'http://127.0.0.1:{p}' for p in (ap,up,cp))
  settings=Settings(APP_ENV='testing',DATABASE_URL=f"sqlite:///{(Path(temp)/'isolated.sqlite').as_posix()}",CORS_ORIGINS=ui,MODEL_PROVIDER='fake',MODEL_API_KEY='',ALLOW_REMOTE_MODEL_CALLS=False)
  engine=create_db_engine(settings);upgrade_database(database_url=settings.database_url,engine=engine);factory=create_session_factory(engine)
  app=create_app(settings);app.dependency_overrides[get_settings]=lambda:settings;received=[]
  @app.middleware('http')
  async def trace(request,call_next):
   response=await call_next(request);received.append({'method':request.method,'path':request.url.path,'status':response.status_code});return response
  server=uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=ap,log_level='warning'));thread=Thread(target=server.run,daemon=True);thread.start()
  env={**os.environ,'VITE_API_BASE_URL':api,'SMOKE_UI_URL':ui,'SMOKE_CDP_URL':cdp,'SMOKE_OUTPUT_DIR':str(OUT)};processes=[]
  try:
   wait_http(api+'/openapi.json')
   for args,cwd,url in [(['node','node_modules/vite/bin/vite.js','--host','127.0.0.1','--port',str(up),'--strictPort'],ROOT/'frontend',ui),([os.environ.get('CHROME_BIN',r'C:\Program Files\Google\Chrome\Application\chrome.exe'),'--headless=new',f'--user-data-dir={Path(temp)/"chrome"}',f'--remote-debugging-port={cp}','--disable-gpu','about:blank'],ROOT,cdp+'/json/list')]:
    p=subprocess.Popen(args,cwd=cwd,env=env,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0),stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL);processes.append(p);wait_http(url,p)
   subprocess.run(['node','scripts/verify-listening-assessment-b2.mjs'],cwd=ROOT/'frontend',env=env,check=True)
   with factory() as db:
    counts={name:db.scalar(select(func.count()).select_from(model)) for name,model in [('runs',AssessmentRun),('sessions',LearningSession),('attempts',Attempt)]}
    tasks=[{'run_id':t.run_id,'skill':t.skill_id,'status':t.status,'session_id':t.learning_session_id} for t in db.scalars(select(AssessmentTask))]
    assert counts=={'runs':1,'sessions':3,'attempts':10},counts
    assert {t['skill']:t['status'] for t in tasks}=={'reading':'completed','listening':'completed','speaking':'not_started','writing':'not_started'}
   assert before==hashlib.sha256((ROOT/'backend/unlearnloop.db').read_bytes()).hexdigest()
   (OUT/'backend.json').write_text(json.dumps({'status':'pass','requests':received,'counts':counts,'tasks':tasks,'formal_database_sha256':before},indent=2),encoding='utf-8')
  finally:
   for p in reversed(processes):
    p.terminate()
    try:p.wait(timeout=10)
    except subprocess.TimeoutExpired:p.kill();p.wait()
   server.should_exit=True;thread.join(timeout=10);clear_engine_cache();engine.dispose()
if __name__=='__main__':main()
