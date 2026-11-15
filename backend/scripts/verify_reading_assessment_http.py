"""Real Reading assessment HTTP acceptance with restart and repeat run."""
import json, os, socket, subprocess, sys, time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
import httpx
from app.persistence.migrations_runner import upgrade_database
from app.content.reading_initial_assessment import QUESTIONS
ROOT=Path(__file__).resolve().parents[2]
ANS={q['id']:q['answer'] for q in QUESTIONS}
def main():
 with TemporaryDirectory(prefix='reading-initial-http-') as temp:
  url=f"sqlite:///{(Path(temp)/'isolated.sqlite3').as_posix()}";upgrade_database(database_url=url)
  with socket.socket() as s:s.bind(('127.0.0.1',0));port=s.getsockname()[1]
  base=f'http://127.0.0.1:{port}';env={**os.environ,'DATABASE_URL':url,'APP_ENV':'testing','MODEL_PROVIDER':'fake','MODEL_API_KEY':'','ALLOW_REMOTE_MODEL_CALLS':'false'};proc=None;trace=[]
  def start():
   p=subprocess.Popen([sys.executable,'-m','uvicorn','app.main:app','--host','127.0.0.1','--port',str(port),'--log-level','error'],cwd=ROOT/'backend',env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
   for _ in range(150):
    if p.poll() is not None:raise RuntimeError('Uvicorn startup failed')
    try:
     if httpx.get(base+'/openapi.json',timeout=1,trust_env=False).status_code==200:return p
    except httpx.TransportError:pass
    time.sleep(.1)
   p.terminate();p.wait(timeout=10);raise RuntimeError('Uvicorn timeout')
  def stop(p):
   p.terminate()
   try:p.wait(timeout=10)
   except subprocess.TimeoutExpired:p.kill();p.wait(timeout=10)
  try:
   proc=start()
   with httpx.Client(base_url=base,trust_env=False,timeout=10) as c:
    def req(label,method,path,**kw):
     r=c.request(method,path,**kw);d=r.json();trace.append({'step':label,'method':method,'path':path,'status':r.status_code,'response':d});assert r.status_code<300,(label,r.text);return d
    run=req('create_run','POST','/api/v1/assessment-runs',json={'learner_id':'http-reading','idempotency_key':'r1'});root=f"/api/v1/assessment-runs/{run['run_id']}/reading";params={'learner_id':'http-reading'}
    startdata=req('start_reading','POST',root+'/start',params=params);assert all('correct_answer' not in q for q in startdata['questions'])
    def concurrent_submit(key):
     with httpx.Client(base_url=base,trust_env=False,timeout=20) as concurrent_client:
      response=concurrent_client.post(root+'/submit',json={'learner_id':'http-reading','answers':ANS,'idempotency_key':key})
      data=response.json();return response.status_code,data
    with ThreadPoolExecutor(max_workers=2) as pool:
     responses=list(pool.map(concurrent_submit,['submit-one-a','submit-one-b']))
    assert all(code==200 for code,_ in responses),responses
    trace.extend({'step':'concurrent_submit','status':code,'response':data} for code,data in responses)
    result=req('submit_idempotent_retry','POST',root+'/submit',json={'learner_id':'http-reading','answers':ANS,'idempotency_key':'submit-one'});assert result['correct_count']==4 and result['evidence_insufficient']
    req('read_result','GET',root,params=params);stop(proc);proc=None;proc=start();req('restore_after_restart','GET',root,params=params)
    run2=req('create_second_run','POST','/api/v1/assessment-runs',json={'learner_id':'http-reading','idempotency_key':'r2'});root2=f"/api/v1/assessment-runs/{run2['run_id']}/reading";rep=req('start_reassessment','POST',root2+'/start',params=params);assert rep['repeated_exposure']
    sec=req('submit_repeated','POST',root2+'/submit',json={'learner_id':'http-reading','answers':ANS,'idempotency_key':'submit-two'});assert sec['repeated_exposure'];req('assessment_history','GET','/api/v1/assessment-runs',params=params)
   out=ROOT/'docs/development/reading-assessment-http-evidence.json';out.write_text(json.dumps({'isolated_sqlite':True,'uvicorn_restart':True,'steps':trace},ensure_ascii=False,indent=2),encoding='utf-8');print(out)
  finally:
   if proc:stop(proc)
if __name__=='__main__':main()
