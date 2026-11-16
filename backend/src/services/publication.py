"""Attempt lifecycle for a diversity run.

A run is a stable publication slot. Every execution is a separate RunAttempt
row, and only the run's current attempt may publish. Publication is one
database transaction that sets result, succeeded status, published attempt and
the archive's path/hash/size together. The archive bytes are written atomically
before that transaction; if the transaction loses the race (or never commits),
the file is unreferenced and may be swept, never silently published.
"""
import os,threading
from datetime import datetime,timedelta,timezone
from sqlalchemy import update,select
from src.config.database import SessionLocal
from src.config.settings import RUN_LEASE_SECONDS
from src.constants.enums import AttemptDisplay,AttemptStatus,RunStatus
from src.models import AuditLog,DiversityRun,RunAttempt
from src.services import artifacts
from src.utils.serialize import row

LEASE=timedelta(seconds=max(10,RUN_LEASE_SECONDS))
RUN_TERMINAL=(RunStatus.SUCCEEDED,)


def now_utc():return datetime.now(timezone.utc)

def as_utc(value):
 if value is None:return None
 return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)

def lease_active(run):
 return run.status==RunStatus.RUNNING and run.lease_expires_at is not None and as_utc(run.lease_expires_at)>now_utc()

def display_status(value):
 try:return AttemptDisplay(value).value
 except ValueError:return value

def attempt_rows(db,run_id):
 items=db.execute(select(RunAttempt).where(RunAttempt.run_id==run_id).order_by(RunAttempt.id)).scalars().all()
 return [{**row(a),'display_status':display_status(a.status)} for a in items]

def run_payload(db,run):
 payload=row(run)
 payload['attempts']=attempt_rows(db,run.id)
 payload['can_takeover']=run.status!=RunStatus.SUCCEEDED and not lease_active(run)
 return payload

def dispatch(run_id,attempt_id):
 """Queue exactly one attempt, identified by id, so tracebacks and redeliveries
 stay attributable to the attempt that was current when the message was sent.
 A stale message cannot run a newer attempt: it can only claim its own attempt
 row, and that row is rejected unless it is still the run's current attempt.
 """
 from src.worker import analyze_run
 analyze_run.apply_async(args=[run_id,attempt_id])

def mark_attempt(db,attempt_id,status,reason=None):
 changed=db.execute(update(RunAttempt).where(RunAttempt.id==attempt_id,RunAttempt.status==AttemptStatus.RUNNING).values(status=status,reason=(reason or '')[:500] or None,finished_at=now_utc()).execution_options(synchronize_session=False)).rowcount
 db.commit();return changed==1

def claim_attempt(db,run_id,attempt_id,worker_name):
 """Exactly one delivery may claim an attempt; only the current attempt may run."""
 attempt=db.get(RunAttempt,attempt_id)
 if not attempt or attempt.run_id!=run_id:return {'ok':False,'reason':'missing'}
 stamp=now_utc()
 claimed=db.execute(update(RunAttempt).where(RunAttempt.id==attempt_id,RunAttempt.status==AttemptStatus.QUEUED).values(status=AttemptStatus.RUNNING,worker=worker_name,started_at=stamp,heartbeat_at=stamp),execution_options={'synchronize_session':False})
 if claimed.rowcount!=1:
  db.rollback();return {'ok':False,'reason':'duplicate'}
 guarded=db.execute(update(DiversityRun).where(DiversityRun.id==run_id,DiversityRun.current_attempt_id==attempt_id,DiversityRun.status==RunStatus.QUEUED).values(status=RunStatus.RUNNING,lease_expires_at=stamp+LEASE,status_since=stamp,error=None),execution_options={'synchronize_session':False})
 if guarded.rowcount!=1:
  db.rollback();mark_attempt(db,attempt_id,AttemptStatus.SUPERSEDED,'不是当前有效尝试，拒绝执行')
  return {'ok':False,'reason':'superseded'}
 db.commit();db.expire_all()
 return {'ok':True,'run':db.get(DiversityRun,run_id)}


def dispatch_attempt(db,run,attempt,actor='system'):
 try:dispatch(run.id,attempt.id)
 except Exception:
  attempt.status=AttemptStatus.FAILED;attempt.reason='队列不可用';attempt.finished_at=now_utc()
  if run.current_attempt_id==attempt.id and run.status not in RUN_TERMINAL:
   run.status=RunStatus.FAILED;run.error='队列不可用';run.status_since=now_utc();run.lease_expires_at=None
  db.add(AuditLog(actor=actor,action='diversity.dispatch_failed',detail={'run_id':run.id,'attempt_id':attempt.id}))
  db.commit();raise


def publish_attempt(db,run_id,attempt_id,result):
 """Atomically publish result + succeeded status + archive evidence.

 Returns ok=False for late/stale attempts. They can never overwrite a newer
 publication because the guarded UPDATE requires current_attempt_id and a live
 lease. The just-written archive of a rejected attempt is removed immediately.
 """
 attempt=db.get(RunAttempt,attempt_id);run=db.get(DiversityRun,run_id)
 if not attempt or attempt.run_id!=run_id or not run:return {'ok':False,'reason':'missing'}
 if run.status==RunStatus.SUCCEEDED and run.published_attempt_id==attempt_id:
  return {'ok':True,'idempotent':True,'run':run,'attempt':attempt}
 if attempt.status!=AttemptStatus.RUNNING:return {'ok':False,'reason':'not_running'}
 artifact=artifacts.write(run_id,attempt.attempt_no,result);stamp=now_utc()
 guarded=db.execute(update(DiversityRun).where(
  DiversityRun.id==run_id,
  DiversityRun.current_attempt_id==attempt_id,
  DiversityRun.status==RunStatus.RUNNING,
  DiversityRun.lease_expires_at>stamp,
 ).values(status=RunStatus.SUCCEEDED,result=result,published_attempt_id=attempt_id,artifact_path=artifact['path'],artifact_sha256=artifact['sha256'],artifact_bytes=artifact['bytes'],published_at=stamp,status_since=stamp,lease_expires_at=None,error=None),execution_options={'synchronize_session':False})
 if guarded.rowcount!=1:
  db.rollback()
  current=db.get(DiversityRun,run_id)
  reason=('迟到尝试：结果已由尝试 %s 发布'%current.published_attempt_id) if current and current.published_attempt_id else '迟到尝试：已被接管或租约失效'
  db.execute(update(RunAttempt).where(RunAttempt.id==attempt_id,RunAttempt.status==AttemptStatus.RUNNING).values(status=AttemptStatus.SUPERSEDED,reason=reason,finished_at=stamp),execution_options={'synchronize_session':False})
  db.add(AuditLog(actor='worker',action='diversity.late_discarded',detail={'run_id':run_id,'attempt_id':attempt_id,'reason':reason}))
  db.commit();artifacts.discard(artifact['path'])
  return {'ok':False,'reason':'late','detail':reason}
 db.execute(update(RunAttempt).where(RunAttempt.id==attempt_id,RunAttempt.status==AttemptStatus.RUNNING).values(status=AttemptStatus.SUCCEEDED,reason=None,finished_at=stamp,artifact_path=artifact['path'],artifact_sha256=artifact['sha256'],artifact_bytes=artifact['bytes']),execution_options={'synchronize_session':False})
 db.add(AuditLog(actor='worker',action='diversity.published',detail={'run_id':run_id,'attempt_id':attempt_id,'attempt_no':attempt.attempt_no,'m':result.get('m'),'eligible':result.get('eligible'),'empty':result.get('excluded_empty'),'artifact_sha256':artifact['sha256'],'bytes':artifact['bytes']}))
 db.commit();db.expire_all()
 return {'ok':True,'run':db.get(DiversityRun,run_id),'attempt':db.get(RunAttempt,attempt_id),'artifact':artifact}


def fail_attempt(db,run_id,attempt_id,message,interrupted=False):
 """Record a failed/interrupted attempt; only the current attempt marks the run."""
 status=AttemptStatus.INTERRUPTED if interrupted else AttemptStatus.FAILED;stamp=now_utc()
 changed=db.execute(update(RunAttempt).where(RunAttempt.id==attempt_id,RunAttempt.status==AttemptStatus.RUNNING).values(status=status,reason=(message or '')[:500],finished_at=stamp).execution_options(synchronize_session=False)).rowcount
 run_changed=db.execute(update(DiversityRun).where(DiversityRun.id==run_id,DiversityRun.current_attempt_id==attempt_id,DiversityRun.status==RunStatus.RUNNING).values(status=status,error=(message or '')[:1000],status_since=stamp,lease_expires_at=None).execution_options(synchronize_session=False)).rowcount
 db.add(AuditLog(actor='worker',action='diversity.interrupted' if interrupted else 'diversity.failed',detail={'run_id':run_id,'attempt_id':attempt_id,'error':(message or '')[:300],'run_state_changed':bool(run_changed)}))
 db.commit();return {'attempt_changed':bool(changed),'run_changed':bool(run_changed)}


def heartbeat(db,run_id,attempt_id):
 """Extend the lease only while this attempt is still the current one."""
 stamp=now_utc()
 alive=db.execute(update(RunAttempt).where(RunAttempt.id==attempt_id,RunAttempt.status==AttemptStatus.RUNNING).values(heartbeat_at=stamp).execution_options(synchronize_session=False)).rowcount
 if alive!=1:db.rollback();return False
 current=db.execute(update(DiversityRun).where(DiversityRun.id==run_id,DiversityRun.current_attempt_id==attempt_id,DiversityRun.status==RunStatus.RUNNING).values(lease_expires_at=stamp+LEASE).execution_options(synchronize_session=False)).rowcount
 db.commit();return bool(current==1)


def recover_stale(db):
 """Mark runs whose worker lease expired as interrupted (recoverable).

 Idempotent and conditional: it never touches succeeded runs and never releases
 a lease that a live heartbeat has just renewed.
 """
 stamp=now_utc()
 stale=db.execute(select(DiversityRun).where(DiversityRun.status==RunStatus.RUNNING,DiversityRun.lease_expires_at.is_not(None),DiversityRun.lease_expires_at<stamp).limit(20)).scalars().all()
 recovered=[]
 for run in stale:
  attempt_id=run.current_attempt_id
  changed=db.execute(update(DiversityRun).where(DiversityRun.id==run.id,DiversityRun.current_attempt_id==attempt_id,DiversityRun.status==RunStatus.RUNNING,DiversityRun.lease_expires_at<stamp).values(status=RunStatus.INTERRUPTED,error='尝试租约过期，worker失联；可接管重试',status_since=stamp,lease_expires_at=None).execution_options(synchronize_session=False)).rowcount
  if not changed:continue
  db.execute(update(RunAttempt).where(RunAttempt.id==attempt_id,RunAttempt.status==AttemptStatus.RUNNING).values(status=AttemptStatus.INTERRUPTED,reason='租约过期，worker失联',finished_at=stamp),execution_options={'synchronize_session':False})
  db.add(AuditLog(actor='system',action='diversity.interrupted',detail={'run_id':run.id,'attempt_id':attempt_id,'reason':'lease_expired','recoverable':True}))
  recovered.append({'run_id':run.id,'attempt_id':attempt_id})
 db.commit();db.expire_all()
 return recovered


def take_over(db,run,reason,actor):
 """Start a new attempt that supersedes the previous one.

 Only allowed when the run is not published and no live lease is held. The
 previous attempt is explicitly marked superseded, and because the run's
 current_attempt_id moves, any late write-back from it is rejected. The run
 row is locked so two simultaneous takeovers cannot fork the attempt chain.
 """
 run=db.execute(select(DiversityRun).where(DiversityRun.id==run.id).with_for_update()).scalar_one()
 if run.status==RunStatus.SUCCEEDED:raise ValueError('已发布的研究不可接管')
 if lease_active(run):raise ValueError('当前尝试租约仍在有效期内，稍后再接管')
 previous=run.current_attempt_id;stamp=now_utc();run.attempt_seq=int(run.attempt_seq or 0)+1
 if previous is not None:
  prior=db.get(RunAttempt,previous)
  # A running attempt whose lease lapsed was interrupted, not replaced: record
  # both truthfully so the page can distinguish "worker died" from "queued but
  # superseded before it ever started".
  if prior and prior.status==AttemptStatus.RUNNING:
   db.execute(update(RunAttempt).where(RunAttempt.id==previous,RunAttempt.status==AttemptStatus.RUNNING).values(status=AttemptStatus.INTERRUPTED,reason=('中断后接管：'+(reason or ''))[:480],finished_at=stamp),execution_options={'synchronize_session':False})
  elif prior:
   db.execute(update(RunAttempt).where(RunAttempt.id==previous,RunAttempt.status==AttemptStatus.QUEUED).values(status=AttemptStatus.SUPERSEDED,reason=('接管：'+(reason or ''))[:480],finished_at=stamp),execution_options={'synchronize_session':False})
 attempt=RunAttempt(run_id=run.id,attempt_no=run.attempt_seq,status=AttemptStatus.QUEUED,token=os.urandom(16).hex(),reason=(reason or '')[:500],recovered_from_attempt_id=previous)
 db.add(attempt);db.flush()
 run.current_attempt_id=attempt.id;run.status=RunStatus.QUEUED;run.error=None;run.status_since=stamp;run.lease_expires_at=None
 db.add(AuditLog(actor=actor,action='diversity.takeover',detail={'run_id':run.id,'attempt_id':attempt.id,'attempt_no':attempt.attempt_no,'recovered_from':previous,'reason':reason}))
 db.commit();return attempt


def retry_attempt(db,run,reason,actor):
 if run.status not in (RunStatus.FAILED,RunStatus.INTERRUPTED):raise ValueError('只有失败或中断的研究可重试')
 return take_over(db,run,reason,actor)


def verify_artifact(db,run):
 attempt=db.get(RunAttempt,run.published_attempt_id) if run.published_attempt_id else None
 report={'run_id':run.id,'published_attempt_id':run.published_attempt_id,'attempt_no':attempt.attempt_no if attempt else None,'file':None,'result_matches_archive':False,'attempt_matches_run':False,'ok':False,'reason':None}
 if run.status!=RunStatus.SUCCEEDED or not run.published_attempt_id or not run.artifact_path:
  report['reason']='尚未发布归档';return report
 report['file']=artifacts.inspect(run.artifact_path,run.artifact_sha256,run.artifact_bytes)
 if not attempt:report['reason']='发布尝试不存在';return report
 report['attempt_matches_run']=attempt.status==AttemptStatus.SUCCEEDED and attempt.artifact_sha256==run.artifact_sha256
 report['result_matches_archive']=artifacts.sha256_of(artifacts.canonical(run.result))==run.artifact_sha256
 file=report['file'] or {}
 if file.get('error'):report['reason']=file['error']
 elif not report['attempt_matches_run']:report['reason']='发布尝试与运行记录不一致'
 elif not report['result_matches_archive']:report['reason']='库内结果与归档内容不一致'
 elif file.get('fingerprint')!=run.result.get('fingerprint'):report['reason']='归档指纹与库内结果不一致'
 else:report['ok']=True
 return report


class LeaseKeeper:
 """Heartbeat that owns its own DB session, safe to run in a daemon thread."""
 def __init__(self,run_id,attempt_id):
  self.run_id=run_id;self.attempt_id=attempt_id;self.stop=threading.Event();self.lost=threading.Event();self.thread=None
 def start(self):
  interval=max(5,LEASE.total_seconds()/3)
  def loop():
   while not self.stop.wait(interval):
    try:
     with SessionLocal() as db:
      if not heartbeat(db,self.run_id,self.attempt_id):self.lost.set();return
    except Exception:
     self.lost.set();return
  self.thread=threading.Thread(target=loop,daemon=True);self.thread.start();return self
 def close(self):
  self.stop.set()
  if self.thread:self.thread.join(timeout=2)
