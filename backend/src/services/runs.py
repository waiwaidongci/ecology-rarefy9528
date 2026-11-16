import os
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from fastapi import HTTPException
from src.models import DiversityRun,RunAttempt,Survey,Transect
from src.constants.enums import RunStatus,AttemptStatus
from src.services import publication
from src.services.audit import record
def list_runs(db):return [publication.run_payload(db,r) for r in db.scalars(select(DiversityRun).order_by(DiversityRun.id.desc()))]
def get_run(db,id):
 obj=db.get(DiversityRun,id)
 if not obj:raise HTTPException(404,'研究不存在')
 return obj
def detail(db,id):return publication.run_payload(db,get_run(db,id))
def find_by_key(db,key):return db.scalar(select(DiversityRun).where(DiversityRun.request_key==key))
def create_run(db,data,user):
 if not db.get(Survey,data.survey_id):raise HTTPException(404,'调查期不存在')
 valid=set(db.scalars(select(Transect.id).where(Transect.survey_id==data.survey_id)))
 if not set(data.transect_ids)<=valid:raise HTTPException(422,'所选样线必须来自同一调查期')
 config=data.model_dump(exclude={'name','survey_id','request_key'})
 existing=find_by_key(db,data.request_key)
 if existing:
  if existing.config!=config or existing.survey_id!=data.survey_id:raise HTTPException(409,'该幂等键已用于不同的研究参数')
  return publication.run_payload(db,existing),False
 obj=DiversityRun(survey_id=data.survey_id,name=data.name,config=config,creator_id=user.id,status=RunStatus.QUEUED,request_key=data.request_key,attempt_seq=0)
 db.add(obj)
 try:db.flush()
 except IntegrityError:
  db.rollback();existing=find_by_key(db,data.request_key)
  if not existing:raise
  return publication.run_payload(db,existing),False
 attempt=RunAttempt(run_id=obj.id,attempt_no=1,status=AttemptStatus.QUEUED,token=os.urandom(16).hex(),reason='首次提交')
 db.add(attempt);db.flush();obj.current_attempt_id=attempt.id;obj.attempt_seq=1
 record(db,user.email,'diversity.queued',{'run_id':obj.id,'attempt_id':attempt.id,'request_key':data.request_key})
 try:publication.dispatch_attempt(db,obj,attempt,user.email)
 except Exception:raise HTTPException(503,'队列不可用，研究未进入执行')
 return publication.run_payload(db,obj),True
def take_over(db,id,reason,user):
 obj=get_run(db,id)
 try:attempt=publication.take_over(db,obj,reason,user.email)
 except ValueError as exc:raise HTTPException(409,str(exc))
 try:publication.dispatch_attempt(db,obj,attempt,user.email)
 except Exception:raise HTTPException(503,'队列不可用，接管未进入执行')
 return publication.run_payload(db,obj)
def retry(db,id,reason,user):
 obj=get_run(db,id)
 try:attempt=publication.retry_attempt(db,obj,reason,user.email)
 except ValueError as exc:raise HTTPException(409,str(exc))
 try:publication.dispatch_attempt(db,obj,attempt,user.email)
 except Exception:raise HTTPException(503,'队列不可用，重试未进入执行')
 return publication.run_payload(db,obj)
def recover(db):
 return publication.recover_stale(db)
def verify(db,id):
 obj=get_run(db,id)
 if obj.status!=RunStatus.SUCCEEDED:raise HTTPException(409,'仅已发布研究可核对归档')
 return publication.verify_artifact(db,obj)
def sweep(db,dry_run=True,grace_seconds=None):
 from src.services import artifacts
 return artifacts.sweep(db,dry_run=dry_run,grace_seconds=grace_seconds)
