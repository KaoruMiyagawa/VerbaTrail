"""B2 real Chrome integration, isolated SQLite, Uvicorn and Vite."""
import hashlib,json,os,subprocess
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
import uvicorn
import httpx
from app.content.reading_initial_assessment import V2_QUESTIONS
from app.content.listening_initial_assessment import QUESTIONS as LISTENING_QUESTIONS
from sqlalchemy import select,func
from app.api.v1.dependencies import clear_engine_cache,get_settings
from app.config import Settings
from app.main import create_app
from app.persistence.database import create_db_engine,create_session_factory
from app.persistence.migrations_runner import upgrade_database
from app.persistence.models import AssessmentRun,AssessmentTask,LearningSession,Attempt
from scripts.verify_listening_browser import port,wait_http
ROOT=Path(__file__).resolve().parents[2]
OUT=ROOT/'docs/development/listening-initial-assessment-b3/isolation'
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
   states=[('completed','not_started'),('not_started','completed'),('completed','in_progress'),('in_progress','completed'),('completed','completed')]
   fixtures=[]
   with httpx.Client(base_url=api,trust_env=False,timeout=15) as c:
    def req(method,path,**kwargs):
     r=c.request(method,path,**kwargs);r.raise_for_status();return r.json()
    for i,(reading,listening) in enumerate(states):
     run=req('POST','/api/v1/assessment-runs',json={'learner_id':'default_learner','assessment_version':'initial-v2','idempotency_key':f'isolation-run-{i}'})['run_id']
     results={}
     for skill,status,questions in [('reading',reading,V2_QUESTIONS),('listening',listening,LISTENING_QUESTIONS)]:
      root=f'/api/v1/assessment-runs/{run}/{skill}'
      if status!='not_started':req('POST',root+'/start',params={'learner_id':'default_learner'})
      if status=='completed':
       results[skill]=req('POST',root+'/submit',json={'learner_id':'default_learner','answers':{q['id']:q['answer'] for q in questions},'idempotency_key':f'isolation-submit-{skill}-{i}'})
     runobj=req('GET',f'/api/v1/assessment-runs/{run}',params={'learner_id':'default_learner'})
     assert runobj['status']!='completed'
     fixtures.append({'run_id':run,'reading':reading,'listening':listening,'run_status':runobj['status'],'results':results})
    order=[r['run_id'] for r in req('GET','/api/v1/assessment-runs',params={'learner_id':'default_learner'})]
   env['ISOLATION_FIXTURES']=json.dumps(fixtures);env['ISOLATION_ORDER']=json.dumps(order)
   subprocess.run(['node','scripts/verify-listening-b3-isolation.mjs'],cwd=ROOT/'frontend',env=env,check=True)
   with httpx.Client(base_url=api,trust_env=False,timeout=15) as c:
    restored={}
    both=fixtures[-1]
    for skill in ('reading','listening'):
     result=c.get(f"/api/v1/assessment-runs/{both['run_id']}/{skill}",params={'learner_id':'default_learner'});result.raise_for_status();body=result.json()
     old=both['results'][skill]
     assert body['correct_count']==old['correct_count'] and body['attempt_references']==old['attempt_references']
     restored[skill]={'correct_count':body['correct_count'],'attempt_references':body['attempt_references']}
   with factory() as db:
    counts={name:db.scalar(select(func.count()).select_from(model)) for name,model in [('runs',AssessmentRun),('sessions',LearningSession),('attempts',Attempt)]}
    tasks=[{'run_id':t.run_id,'skill':t.skill_id,'status':t.status,'session_id':t.learning_session_id} for t in db.scalars(select(AssessmentTask))]
    assert counts=={'runs':5,'sessions':8,'attempts':30},counts
    for fixture in fixtures:
     skill_status={t['skill']:t['status'] for t in tasks if t['run_id']==fixture['run_id']}
     assert skill_status['reading']==fixture['reading'] and skill_status['listening']==fixture['listening']
     assert skill_status['speaking']==skill_status['writing']=='not_started'
   assert before==hashlib.sha256((ROOT/'backend/unlearnloop.db').read_bytes()).hexdigest()
   (OUT/'backend.json').write_text(json.dumps({'status':'pass','requests':received,'counts':counts,'tasks':tasks,'fixtures':fixtures,'restored':restored,'formal_database_sha256':before},indent=2),encoding='utf-8')
  finally:
   for p in reversed(processes):
    p.terminate()
    try:p.wait(timeout=10)
    except subprocess.TimeoutExpired:p.kill();p.wait()
   server.should_exit=True;thread.join(timeout=10);clear_engine_cache();engine.dispose()
if __name__=='__main__':main()
