"""Scenario walkthrough against a real seeded database (SQLite backend).

Covers: seed publication, interrupted attempt with orphan archive, takeover,
late write-back rejection, duplicate request idempotency, verification, export
gating, and sweep concurrency. Prints a JSON report; exits non-zero on mismatch.
"""
import json,os,sys,tempfile,threading
from datetime import timedelta
root=tempfile.mkdtemp(prefix='gb0014-')
os.environ['DATABASE_URL']='sqlite:///'+root+'/scenario.db'
os.environ['ARTIFACT_DIR']=root+'/artifacts'
os.environ['JWT_SECRET']='gb0014-scenario'
os.environ['REDIS_URL']='redis://127.0.0.1:6399/0'
os.environ['SEED_DEMO']='false'
sys.path.insert(0,'/workspace/backend')
import src.config.database as database
from src.config.database import Base
from sqlalchemy import select
from src.models import AuditLog,ConservationNote,DiversityRun,RunAttempt,Survey
from src.services import artifacts,publication
from src.services.analysis import analyze
from src.services.transects import list_transects
from src.utils.serialize import row
from src.worker import execute
from scripts.seed_demo import seed

sent=[]
publication.dispatch=lambda run_id,attempt_id:sent.append((run_id,attempt_id))
Base.metadata.create_all(database.engine)
seed_counts=seed()
report={'database':os.environ['DATABASE_URL'],'seed':seed_counts,'steps':[]}
def step(name,**data):report['steps'].append({'step':name,**data})
def eq(a,b,label):
 if a!=b:raise AssertionError(f'{label}: {a!r} != {b!r}')

with database.SessionLocal() as db:
 run=db.scalar(select(DiversityRun).where(DiversityRun.name=='秋季共同努力量研究'))
 seeded_archive=run.artifact_path
 eq(publication.verify_artifact(db,run)['ok'],True,'seed archive verifies')
 step('seed publication',run_id=run.id,status=run.status,published_attempt=run.published_attempt_id,archive=os.path.basename(seeded_archive),sha256=run.artifact_sha256[:16])

# 1. New run whose worker stalls after writing the archive but before commit.
survey=db.scalar(select(Survey).where(Survey.name=='秋季样线调查'))
data=list_transects(db,survey.id)
config={'transect_ids':[t['id'] for t in data],'m':100,'bootstrap':200,'seed':20261114}
run2=DiversityRun(survey_id=survey.id,name='归档后中断验证',config=config,status='queued',creator_id=1,request_key='gb0014-interrupt',attempt_seq=1)
db.add(run2);db.flush();a1=RunAttempt(run_id=run2.id,attempt_no=1,status='queued',token='t1',reason='首次提交');db.add(a1);db.flush();run2.current_attempt_id=a1.id;db.commit()
result=analyze(row(survey),data,config)
eq(publication.claim_attempt(db,run2.id,a1.id,'worker-crash')['ok'],True,'claim attempt 1')
orphan=artifacts.write(run2.id,1,result)['path']
eq(os.path.exists(orphan),True,'archive written before crash')
# process dies here: no publish transaction
with database.SessionLocal() as fresh:
 run2=fresh.get(DiversityRun,run2.id)
 eq(run2.status,'running','run still running with no published result')
 eq(run2.result,{},'no result leaked')
 eq(run2.artifact_path,None,'no archive referenced')
 run2.lease_expires_at=publication.now_utc()-timedelta(seconds=5);fresh.commit()
 recovered=publication.recover_stale(fresh)
 eq(len(recovered),1,'lease recovery marks interrupted')
 run2=fresh.get(DiversityRun,run2.id)
 eq(run2.status,'interrupted','interrupted after lease expiry')
 eq(run2.result,{},'still no result after interruption')
 os.utime(orphan,(0,0))
 swept=artifacts.sweep(fresh,dry_run=False,grace_seconds=60)
 eq(os.path.basename(orphan) in swept['deleted'],True,'orphan archive swept')
 eq(os.path.exists(seeded_archive),True,'seeded published archive survives sweep')
 step('archive-then-interrupt',run_id=run2.id,status=run2.status,swept_orphan=os.path.basename(orphan))

# 2. Admin takeover creates attempt 2; attempt 1 is recorded as interrupted.
 with database.SessionLocal() as fresh:
  run2=fresh.get(DiversityRun,run2.id)
  takeover=publication.take_over(fresh,run2,'worker失联，负责人接管','admin@songji.demo')
  attempt2_id=takeover.id
  eq(takeover.attempt_no,2,'attempt number increments')
  eq(fresh.get(RunAttempt,a1.id).status,'interrupted','previous attempt interrupted')
 out=execute(run2.id,attempt2_id)
 eq(out,'published','attempt 2 publishes')
 with database.SessionLocal() as fresh:
  run2=fresh.get(DiversityRun,run2.id)
  eq(run2.published_attempt_id,attempt2_id,'attempt 2 is the published attempt')
  eq(publication.verify_artifact(fresh,run2)['ok'],True,'attempt 2 archive verifies')
  fingerprint=run2.result['fingerprint'];archive=run2.artifact_path
  step('takeover',run_id=run2.id,attempt=2,status=run2.status,archive=os.path.basename(archive),sha256=run2.artifact_sha256[:16])

# 3. Late write-back from attempt 1 must not overwrite attempt 2.
 with database.SessionLocal() as fresh:
  stale=fresh.get(RunAttempt,a1.id);stale.status='running';fresh.commit()
  late=dict(result);late['fingerprint']='late-fingerprint-from-attempt-1'
  rejected=publication.publish_attempt(fresh,run2.id,a1.id,late)
  eq(rejected['ok'],False,'late publish rejected')
  eq(rejected['reason'],'late','rejection reason is late')
 with database.SessionLocal() as fresh:
  run2=fresh.get(DiversityRun,run2.id)
  eq(run2.result['fingerprint'],fingerprint,'published fingerprint unchanged')
  eq(run2.artifact_path,archive,'published archive unchanged')
  eq(fresh.get(RunAttempt,a1.id).status,'superseded','late attempt marked superseded')
  names=os.listdir(artifacts.directory())
  eq(f'diversity-{run2.id}-a1.json' in names,False,'late attempt archive discarded')
  audits=[a.action for a in fresh.scalars(select(AuditLog))]
  eq('diversity.late_discarded' in audits,True,'late discard recorded in audit')
  step('late attempt rejected',run_id=run2.id,fingerprint_preserved=True,attempts=publication.attempt_rows(fresh,run2.id))

# 4. Duplicate execution and duplicate submission.
 out=execute(run2.id,attempt2_id)
 eq(out,'duplicate','redelivery skipped')
 eq(len([n for n in os.listdir(artifacts.directory()) if n.startswith(f'diversity-{run2.id}-')]),1,'one archive for run 2')
 with database.SessionLocal() as fresh:
  existing=fresh.scalar(select(DiversityRun).where(DiversityRun.request_key=='gb0014-interrupt'))
  eq(existing.id,run2.id,'request key resolves to the same run')
  eq(fresh.scalar(select(RunAttempt).where(RunAttempt.run_id==run2.id,RunAttempt.attempt_no==3)),None,'no extra attempt from duplicate')
  step('duplicate execution',run_id=run2.id,result='duplicate delivery skipped, one archive, one request slot')

# 5. Sweep racing a live publication.
 with database.SessionLocal() as fresh:
  config3=dict(config,seed=20261115)
  run3=DiversityRun(survey_id=survey.id,name='清理并发验证',config=config3,status='queued',creator_id=1,request_key='gb0014-sweep-race',attempt_seq=1)
  fresh.add(run3);fresh.flush();a3=RunAttempt(run_id=run3.id,attempt_no=1,status='queued',token='t3');fresh.add(a3);fresh.flush();run3.current_attempt_id=a3.id;fresh.commit()
  eq(publication.claim_attempt(fresh,run3.id,a3.id,'racer')['ok'],True,'claim race attempt')
  result3=analyze(row(survey),data,config3)
  artifact3=artifacts.write(run3.id,1,result3)
  errors=[]
  def sweep():
   try:
    with database.SessionLocal() as session:artifacts.sweep(session,dry_run=False,grace_seconds=0)
   except Exception as exc:errors.append(str(exc))
  thread=threading.Thread(target=sweep);thread.start()
  eq(publication.publish_attempt(fresh,run3.id,a3.id,result3)['ok'],True,'publish during sweep')
  thread.join(5)
  eq(errors,[],'sweeper raised nothing')
  fresh.expire_all();run3=fresh.get(DiversityRun,run3.id)
  eq(os.path.exists(artifact3['path']),True,'archive survives concurrent cleanup')
  eq(publication.verify_artifact(fresh,run3)['ok'],True,'concurrent publication verifies')
  final=artifacts.sweep(fresh,dry_run=False,grace_seconds=0)
  eq(os.path.basename(artifact3['path']) in final['kept_referenced'],True,'referenced file kept on later sweep')
  for path in (seeded_archive,archive):
   eq(os.path.exists(path),True,f'published archive kept: {os.path.basename(path)}')
  step('cleanup concurrency',run_id=run3.id,archive=os.path.basename(artifact3['path']),swept=final['deleted'],kept=final['kept_referenced'])

target=os.path.join(os.path.dirname(os.path.abspath(__file__)),'scenario-report.json')
with open(target,'w') as handle:json.dump(report,handle,ensure_ascii=False,indent=1)
print('scenario ok ->',target)
