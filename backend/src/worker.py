import os
from celery import Celery
from src.config.database import SessionLocal
from src.config.settings import REDIS_URL
from src.models import DiversityRun,Survey
from src.services.transects import list_transects
from src.services.analysis import analyze
from src.services import publication
from src.utils.serialize import row
app=Celery('ecology_rarefy',broker=REDIS_URL,backend=REDIS_URL)
app.conf.update(task_track_started=True,worker_prefetch_multiplier=1,task_acks_late=True)


def execute(id,attempt_id):
 """Run one attempt. Redeliveries and takeovers are separated by attempt id.

 Returns one of: skipped / duplicate / superseded / published / late / failed /
 interrupted. Only 'published' wrote a visible result, and it wrote exactly one
 (result, status, attempt, artifact evidence) publication.
 """
 worker_name=os.getenv('HOSTNAME') or 'worker'
 with SessionLocal() as db:
  claim=publication.claim_attempt(db,id,attempt_id,worker_name)
  if not claim['ok']:return claim['reason']
 run=claim['run'];keeper=publication.LeaseKeeper(id,attempt_id).start()
 try:
  if keeper.lost.is_set():
   with SessionLocal() as db:publication.fail_attempt(db,id,attempt_id,'尝试租约在计算前已失效，结果不发布',interrupted=True)
   return 'interrupted'
  with SessionLocal() as db:
   selected=set(run.config['transect_ids'])
   data=[t for t in list_transects(db,run.survey_id) if t['id'] in selected]
   result=analyze(row(db.get(Survey,run.survey_id)),data,run.config)
  if keeper.lost.is_set():
   with SessionLocal() as db:publication.fail_attempt(db,id,attempt_id,'执行完成前租约已失效，结果不发布',interrupted=True)
   return 'interrupted'
  with SessionLocal() as db:
   outcome=publication.publish_attempt(db,id,attempt_id,result)
  if not outcome['ok'] and outcome['reason']=='not_running':
   # Lost a takeover race between compute and publish: never retry blindly.
   with SessionLocal() as db:publication.fail_attempt(db,id,attempt_id,'尝试在发布前已被接管，结果丢弃',interrupted=True)
   return 'interrupted'
  return 'published' if outcome['ok'] else outcome['reason']
 except Exception as exc:
  with SessionLocal() as db:publication.fail_attempt(db,id,attempt_id,str(exc))
  return 'failed'
 finally:keeper.close()

@app.task(name='ecology.analyze',soft_time_limit=180,time_limit=210)
def analyze_run(id,attempt_id):
 return execute(id,attempt_id)
