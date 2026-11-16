"""Publication-unit tests: attempts, interruption, takeover, late results, idempotency, verification, sweep."""
import json
import os
from datetime import datetime,timezone,timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from src.constants.enums import AttemptStatus,RunStatus
from src.models import AuditLog,DiversityRun,RunAttempt,SpeciesCount,Survey,Transect
import src.config.database as database
from src.services import artifacts,publication,runs
from src.services.analysis import analyze
from src.services.transects import list_transects
from src.utils.serialize import row
from src.worker import execute


def config_for(survey,transects,m=5,bootstrap=50,seed=7):
 return {'transect_ids':[t.id for t in transects],'m':m,'bootstrap':bootstrap,'seed':seed}


def make_run(client,extra=None):
 data=client.seed_data
 payload={'survey_id':data['survey'].id,'name':'attempt test','transect_ids':[t.id for t in data['transects']],'m':5,'bootstrap':50,'seed':7,'request_key':'key-'+os.urandom(6).hex()}
 payload.update(extra or {})
 response=client.post('/runs',headers=client.auth_headers,json=payload)
 assert response.status_code==201,response.text
 return response.json()


def compute_result(db,run):
 selection=set(run.config['transect_ids'])
 data=[t for t in list_transects(db,run.survey_id) if t['id'] in selection]
 return analyze(row(db.get(Survey,run.survey_id)),data,run.config)


def test_interruption_after_archive_leaves_no_published_result(client,broker):
 """Archive written, then publication transaction never lands: no owner, no visible result."""
 run=make_run(client)
 # Re-run the claim outside the API so we control the commit point.
 factory=database.SessionLocal
 with factory() as db:
  attempt=db.get(RunAttempt,run['current_attempt_id']);run_obj=db.get(DiversityRun,run['id'])
  result=compute_result(db,run_obj)
  claimed=publication.claim_attempt(db,run_obj.id,attempt.id,'crasher');assert claimed['ok']
  db.refresh(attempt)
  artifact=artifacts.write(run_obj.id,attempt.attempt_no,result)
  path=artifact['path'];assert os.path.exists(path)
 with factory() as db:  # simulate process death: no publish_attempt called
  run_obj=db.get(DiversityRun,run['id'])
  assert run_obj.status==RunStatus.RUNNING and run_obj.result=={}
  assert run_obj.artifact_path is None and run_obj.published_attempt_id is None
  assert db.get(RunAttempt,run['current_attempt_id']).status==AttemptStatus.RUNNING
 # Lease expires -> interrupted, then admin recovery marks it, still no result.
 with factory() as db:
  run_obj=db.get(DiversityRun,run['id']);run_obj.lease_expires_at=publication.now_utc()-timedelta(seconds=5);db.commit()
  recovered=publication.recover_stale(db)
  assert any(r['run_id']==run['id'] for r in recovered)
 with factory() as db:
  run_obj=db.get(DiversityRun,run['id'])
  assert run_obj.status==RunStatus.INTERRUPTED and run_obj.result=={}
  assert db.get(RunAttempt,run['current_attempt_id']).status==AttemptStatus.INTERRUPTED
  # The orphan archive is older than the grace window only artificially: set mtime back.
  os.utime(path,(0,0))
  report=artifacts.sweep(db,dry_run=False,grace_seconds=60)
  assert run_obj.artifact_path is None
  assert os.path.basename(path) in report['deleted']
  assert not os.path.exists(path)


def test_takeover_publishes_new_attempt_and_late_attempt_cannot_overwrite(client,broker):
 run=make_run(client)
 factory=database.SessionLocal
 first_id=run['current_attempt_id']
 with factory() as db:
  run_obj=db.get(DiversityRun,run['id'])
  # Old attempt claims, then stalls (lease expires); admin takes over.
  assert publication.claim_attempt(db,run_obj.id,first_id,'slow-worker')['ok']
  run_obj=db.get(DiversityRun,run['id']);run_obj.lease_expires_at=publication.now_utc()-timedelta(seconds=1);db.commit()
  assert publication.recover_stale(db)
  run_obj=db.get(DiversityRun,run['id'])
  second=publication.take_over(db,run_obj,'worker失联接管','admin@test.demo')
  assert second.attempt_no==2 and second.recovered_from_attempt_id==first_id
  assert db.get(RunAttempt,first_id).status==AttemptStatus.INTERRUPTED
  second_id=second.id
 # New attempt executes and publishes.
 outcome=execute(run['id'],second_id)
 with factory() as db:print('DEBUG2',outcome,db.get(RunAttempt,second_id).status,db.get(RunAttempt,second_id).reason)
 assert outcome=='published',outcome
 with factory() as db:
  run_obj=db.get(DiversityRun,run['id']);published=run_obj.result['fingerprint'];archive=run_obj.artifact_path
  assert run_obj.status==RunStatus.SUCCEEDED and run_obj.published_attempt_id==second_id
  # The old (late) attempt tries to write back now.
  stale=db.get(RunAttempt,first_id);stale.status=AttemptStatus.RUNNING;db.commit()
  late_result=dict(run_obj.result);late_result['fingerprint']='late-fingerprint'
  outcome=publication.publish_attempt(db,run_obj.id,first_id,late_result)
  assert outcome['ok'] is False and outcome['reason']=='late'
 with factory() as db:
  run_obj=db.get(DiversityRun,run['id'])
  assert run_obj.result['fingerprint']==published and run_obj.artifact_path==archive
  assert db.get(RunAttempt,first_id).status==AttemptStatus.SUPERSEDED
  audit=db.scalars(select(AuditLog).where(AuditLog.action=='diversity.late_discarded')).all()
  assert audit
  # Late archive never survived.
  names=set(os.listdir(artifacts.directory()))
  assert f'diversity-{run["id"]}-a1.json' not in names
  assert os.path.basename(archive) in names


def test_duplicate_delivery_runs_once_and_duplicate_task_adds_no_archive(client,broker):
 run=make_run(client)
 attempt_id=run['current_attempt_id']
 factory=database.SessionLocal
 with factory() as db:
  run_obj=db.get(DiversityRun,run['id']);run_obj.status=RunStatus.QUEUED;db.commit()
 assert execute(run['id'],attempt_id)=='published'
 # Second delivery of the same attempt: no claim, no second archive, same result.
 assert execute(run['id'],attempt_id)=='duplicate'
 with factory() as db:
  run_obj=db.get(DiversityRun,run['id'])
  assert run_obj.status==RunStatus.SUCCEEDED
  assert db.get(RunAttempt,attempt_id).status==AttemptStatus.SUCCEEDED
  assert len(os.listdir(artifacts.directory()))==1
  assert publication.verify_artifact(db,run_obj)['ok']


def test_idempotent_request_key_creates_single_run_and_archive(client,broker):
 data=client.seed_data
 payload={'survey_id':data['survey'].id,'name':'same intent','transect_ids':[t.id for t in data['transects']],'m':5,'bootstrap':50,'seed':11,'request_key':'request-key-abc123'}
 first=client.post('/runs',headers=client.auth_headers,json=payload);assert first.status_code==201
 second=client.post('/runs',headers=client.auth_headers,json=payload);assert second.status_code==200
 assert first.json()['id']==second.json()['id']
 with database.SessionLocal() as db:
  all_runs=db.scalars(select(DiversityRun)).all();assert len(all_runs)==1
  attempts=db.scalars(select(RunAttempt).where(RunAttempt.run_id==first.json()['id'])).all();assert len(attempts)==1
 conflicting=dict(payload,m=6)
 response=client.post('/runs',headers=client.auth_headers,json=conflicting)
 assert response.status_code==409


def test_verify_artifact_detects_tampering(client,broker):
 run=make_run(client)
 assert execute(run['id'],run['current_attempt_id'])=='published'
 with database.SessionLocal() as db:
  run_obj=db.get(DiversityRun,run['id']);assert publication.verify_artifact(db,run_obj)['ok']
  path=run_obj.artifact_path
  with open(path,'a') as handle:handle.write(' ')
  report=publication.verify_artifact(db,run_obj)
  assert report['ok'] is False and 'SHA-256' in report['reason']


def test_verify_endpoint_and_export_carry_publication_evidence(client,broker):
 run=make_run(client)
 assert execute(run['id'],run['current_attempt_id'])=='published'
 response=client.get(f'/runs/{run["id"]}/verify',headers=client.auth_headers)
 assert response.status_code==200;body=response.json()
 assert body['ok'] and body['attempt_no']==1 and body['result_matches_archive']
 # Approved note exports only with matching evidence.
 note=client.post('/notes',headers=client.auth_headers,json={'run_id':run['id'],'name':'export test','transect_ids':[client.seed_data['transects'][0].id],'observation':'观察依据','suggested_action':'下期复查'})
 assert note.status_code==201,note.text
 approve=client.patch(f'/notes/{note.json()["id"]}/approval',headers=client.auth_headers,json={'status':'approved','review_note':'边界确认'})
 assert approve.status_code==200,approve.text
 exported=client.get(f'/notes/{note.json()["id"]}/export',headers=client.auth_headers)
 assert exported.status_code==200
 assert exported.headers['X-Publication-Attempt']==str(run['current_attempt_id'])
 assert exported.headers['X-Archive-SHA256']
 # Tampered archive blocks export instead of shipping unverified numbers.
 with database.SessionLocal() as db:
  path=db.get(DiversityRun,run['id']).artifact_path
 with open(path,'a') as handle:handle.write('x')
 blocked=client.get(f'/notes/{note.json()["id"]}/export',headers=client.auth_headers)
 assert blocked.status_code==409


def test_sweep_keeps_referenced_files_and_spares_fresh_orphans(client,broker,tmp_path):
 run=make_run(client)
 assert execute(run['id'],run['current_attempt_id'])=='published'
 with database.SessionLocal() as db:
  run_obj=db.get(DiversityRun,run['id']);published=run_obj.artifact_path
  orphan=artifacts.directory()/'diversity-999-a1.json';orphan.write_text('{}')
  fresh=artifacts.directory()/'diversity-998-a1.json';fresh.write_text('{}')
  os.utime(orphan,(0,0))
  report=artifacts.sweep(db,dry_run=False,grace_seconds=3600)
  assert os.path.exists(published)
  assert not os.path.exists(orphan)
  assert os.path.exists(fresh)
  assert os.path.basename(published) in report['kept_referenced']
  assert os.path.basename(fresh) in report['kept_recent']


def test_cleanup_concurrent_with_publication_never_deletes_published_archive(client,broker):
 """A publish that commits just after a sweep scan must still be intact."""
 run=make_run(client)
 factory=database.SessionLocal
 with factory() as db:
  run_obj=db.get(DiversityRun,run['id'])
  result=compute_result(db,run_obj)
  assert publication.claim_attempt(db,run_obj.id,run['current_attempt_id'],'racer')['ok']
  artifact=artifacts.write(run_obj.id,1,result)
  # Sweep scans between write and commit: no DB reference exists yet, but the
  # grace window must spare it.
  report=artifacts.sweep(db,dry_run=False,grace_seconds=3600)
  assert os.path.basename(artifact['path']) in report['kept_recent']
  assert publication.publish_attempt(db,run_obj.id,run['current_attempt_id'],result)['ok']
 with factory() as db:
  run_obj=db.get(DiversityRun,run['id'])
  assert os.path.exists(run_obj.artifact_path)
  assert publication.verify_artifact(db,run_obj)['ok']
  # A later sweep with a zero grace window must still keep the referenced file.
  later=artifacts.sweep(db,dry_run=False,grace_seconds=0)
  assert os.path.exists(run_obj.artifact_path)
  assert os.path.basename(run_obj.artifact_path) in later['kept_referenced']


def test_recover_is_idempotent_and_keeps_healthy_lease(client,broker):
 run=make_run(client)
 factory=database.SessionLocal
 with factory() as db:
  run_obj=db.get(DiversityRun,run['id'])
  assert publication.claim_attempt(db,run_obj.id,run['current_attempt_id'],'live')['ok']
  assert publication.recover_stale(db)==[]
  run_obj=db.get(DiversityRun,run['id']);assert run_obj.status==RunStatus.RUNNING
  run_obj.lease_expires_at=publication.now_utc()-timedelta(seconds=1);db.commit()
  assert len(publication.recover_stale(db))==1
  assert publication.recover_stale(db)==[]  # idempotent


def test_sweep_concurrent_with_live_publication_thread(client,broker):
 """Threaded race: a publisher commits while the sweeper scans; archive must survive."""
 import threading
 run=make_run(client)
 factory=database.SessionLocal
 with factory() as db:
  run_obj=db.get(DiversityRun,run['id']);result=compute_result(db,run_obj)
  assert publication.claim_attempt(db,run_obj.id,run['current_attempt_id'],'racer')['ok']
  artifact=artifacts.write(run_obj.id,1,result)
  errors=[]
  def sweep_once():
   try:
    with factory() as session:artifacts.sweep(session,dry_run=False,grace_seconds=3600)
   except Exception as exc:errors.append(exc)
  sweeper=threading.Thread(target=sweep_once);sweeper.start()
  publication.publish_attempt(db,run_obj.id,run['current_attempt_id'],result)
  sweeper.join(timeout=5)
  assert not errors and not sweeper.is_alive()
  db.expire_all();run_obj=db.get(DiversityRun,run['id'])
  assert os.path.exists(artifact['path']) and publication.verify_artifact(db,run_obj)['ok']


def test_concurrent_duplicate_submissions_create_one_run_one_archive(client,broker):
 """Same request_key from two threads: one 201, one replay, one archive."""
 import threading
 data=client.seed_data
 payload={'survey_id':data['survey'].id,'name':'race idempotency','transect_ids':[t.id for t in data['transects']],'m':5,'bootstrap':50,'seed':13,'request_key':'race-key-'+os.urandom(4).hex()}
 results=[]
 def submit():
  with TestClient(__import__('src.main',fromlist=['app']).app) as local:
   local.headers.update(client.auth_headers)
   r=local.post('/runs',headers=client.auth_headers,json=payload);results.append((r.status_code,r.json().get('id')))
  # TestClient per thread avoids sharing one portal; cookie auth is header-based.
 threads=[threading.Thread(target=submit) for _ in range(2)]
 for t in threads:t.start()
 for t in threads:t.join(timeout=10)
 assert all(r[1] is not None for r in results),(results,)
 assert len({r[1] for r in results})==1,results
 assert sorted(r[0] for r in results)==[200,201]



def test_takeover_api_requires_admin_and_records_reason(client,broker):
 run=make_run(client)
 analyst=__import__('src.utils.security',fromlist=['issue_token']).issue_token(client.seed_data['users']['analyst'])
 analyst_headers={'Authorization':'Bearer '+analyst}
 with database.SessionLocal() as db:
  run_obj=db.get(DiversityRun,run['id']);assert publication.claim_attempt(db,run_obj.id,run['current_attempt_id'],'dead-worker')['ok']
  run_obj=db.get(DiversityRun,run['id']);run_obj.lease_expires_at=publication.now_utc()-timedelta(seconds=1);db.commit()
 denied=client.post(f'/runs/{run["id"]}/attempts',headers=analyst_headers,json={'reason':'not allowed'})
 assert denied.status_code==403
 response=client.post(f'/runs/{run["id"]}/attempts',headers=client.auth_headers,json={'reason':'worker 失联，负责人接管'})
 assert response.status_code==201,response.text
 body=response.json()
 assert body['status']=='queued' and body['can_takeover'] is True
 assert [a['attempt_no'] for a in body['attempts']]==[1,2]
 assert body['attempts'][0]['status']==AttemptStatus.INTERRUPTED and body['attempts'][1]['recovered_from_attempt_id']==run['current_attempt_id']
 assert broker[-1]==(run['id'],body['current_attempt_id'])
 # Published runs cannot be taken over again after the new attempt publishes.
 assert execute(run['id'],body['current_attempt_id'])=='published'
 denied=client.post(f'/runs/{run["id"]}/attempts',headers=client.auth_headers,json={'reason':'should fail'})
 assert denied.status_code==409
 details=client.get(f'/runs/{run["id"]}',headers=client.auth_headers).json()
 assert details['status']=='succeeded' and len(details['attempts'])==2
 assert details['published_attempt_id']==body['current_attempt_id']
 logs=client.get('/audit',headers=client.auth_headers).json()
 actions=[entry['action'] for entry in logs]
 assert 'diversity.takeover' in actions and 'diversity.published' in actions


def test_retry_endpoint_only_for_failed_runs(client,broker):
 run=make_run(client)
 response=client.post(f'/runs/{run["id"]}/retry',headers=client.auth_headers,json={'reason':'early retry'})
 assert response.status_code==409
 with database.SessionLocal() as db:
  run_obj=db.get(DiversityRun,run['id']);assert publication.claim_attempt(db,run_obj.id,run['current_attempt_id'],'w')['ok']
  publication.fail_attempt(db,run_obj.id,run['current_attempt_id'],'计算错误')
 retried=client.post(f'/runs/{run["id"]}/retry',headers=client.auth_headers,json={'reason':'修复后重试'})
 assert retried.status_code==201,retried.text
 assert retried.json()['attempts'][-1]['attempt_no']==2
 assert execute(run['id'],retried.json()['current_attempt_id'])=='published'


def test_worker_compute_error_marks_failed_attempt_without_archive(client,broker,monkeypatch):
 run=make_run(client)
 import src.worker as worker
 def boom(*args,**kwargs):raise ValueError('计算失败：模拟')
 monkeypatch.setattr(worker,'analyze',boom)
 assert execute(run['id'],run['current_attempt_id'])=='failed'
 with database.SessionLocal() as db:
  attempt=db.get(RunAttempt,run['current_attempt_id']);run_obj=db.get(DiversityRun,run['id'])
  assert attempt.status==AttemptStatus.FAILED and '计算失败' in attempt.reason
  assert run_obj.status==RunStatus.FAILED and run_obj.result=={}
  assert list(artifacts.directory().iterdir())==[]
  # Failed runs stay non-publishable by late attempts and are retryable by admin.
  retried=client.post(f'/runs/{run["id"]}/retry',headers=client.auth_headers,json={'reason':'修复后重试'})
  assert retried.status_code==201
  assert retried.json()['attempts'][-1]['attempt_no']==2
