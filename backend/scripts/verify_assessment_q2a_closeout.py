"""Run assessment browser acceptance against isolated real services."""
import json, os, subprocess, sys
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
import uvicorn
from sqlalchemy import func, select
from app.api.v1.dependencies import clear_engine_cache, get_settings
from app.config import Settings
from app.main import create_app
from app.persistence.database import create_db_engine, create_session_factory
from app.persistence.migrations_runner import upgrade_database
from app.persistence.models import AssessmentRun, LearningSession, Attempt
from scripts.verify_listening_browser import port, wait_http

ROOT=Path(__file__).resolve().parents[2]
OUT=ROOT/'docs/development/assessment-a3-browser-evidence/q2a-closeout'
def main():
 with TemporaryDirectory(prefix='assessment-a3-') as temp:
  api_port,ui_port,cdp_port=port(),port(),port()
  api,ui,cdp=(f'http://127.0.0.1:{p}' for p in (api_port,ui_port,cdp_port))
  settings=Settings(APP_ENV='testing',DATABASE_URL=f"sqlite:///{(Path(temp)/'isolated.sqlite3').as_posix()}",CORS_ORIGINS=ui,MODEL_PROVIDER='fake',MODEL_API_KEY='',ALLOW_REMOTE_MODEL_CALLS=False)
  engine=create_db_engine(settings);upgrade_database(database_url=settings.database_url,engine=engine)
  app=create_app(settings);app.dependency_overrides[get_settings]=lambda:settings
  received=[]
  @app.middleware('http')
  async def audit(request, call_next):
   if request.method=='POST' and request.url.path.startswith('/api/v1/assessment-runs'):
    response=await call_next(request);received.append({'method':request.method,'path':request.url.path,'status':response.status_code});return response
   return await call_next(request)
  server=uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=api_port,log_level='warning'))
  thread=Thread(target=server.run,daemon=True);thread.start()
  env={**os.environ,'VITE_API_BASE_URL':api,'SMOKE_UI_URL':ui,'SMOKE_CDP_URL':cdp,'SMOKE_OUTPUT_DIR':str(OUT)}
  chrome=os.environ.get('CHROME_BIN',r'C:\Program Files\Google\Chrome\Application\chrome.exe'); processes=[]
  try:
   wait_http(api+'/openapi.json')
   for args,cwd,url in [(['node','node_modules/vite/bin/vite.js','--host','127.0.0.1','--port',str(ui_port),'--strictPort'],ROOT/'frontend',ui),([chrome,'--headless=new',f'--user-data-dir={Path(temp)/"chrome"}',f'--remote-debugging-port={cdp_port}','--disable-gpu','about:blank'],ROOT,cdp+'/json/list')]:
    p=subprocess.Popen(args,cwd=cwd,env=env,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0),stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL);processes.append(p);wait_http(url,p)
   subprocess.run(['node','scripts/verify-assessment-q2a-closeout.mjs'],cwd=ROOT/'frontend',env=env,check=True)
   with create_session_factory(engine)() as db:
    counts={c.__name__:db.scalar(select(func.count()).select_from(c)) for c in (AssessmentRun,LearningSession,Attempt)}
    assert counts=={'AssessmentRun':2,'LearningSession':2,'Attempt':4},counts
    sessions=[{'id':s.id,'status':s.status} for s in db.scalars(select(LearningSession)).all()]
   (OUT/'database-counts.json').write_text(json.dumps({'counts':counts,'sessions':sessions,'server_posts':received},indent=2),encoding='utf-8')
  finally:
   for p in reversed(processes):
    p.terminate()
    try:p.wait(timeout=10)
    except subprocess.TimeoutExpired:p.kill();p.wait()
   server.should_exit=True;thread.join(timeout=10);clear_engine_cache();engine.dispose()
if __name__=='__main__':main()
