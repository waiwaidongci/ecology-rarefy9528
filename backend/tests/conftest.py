"""Test setup: one throwaway SQLite database and artifact directory.

Environment must be set before importing src.config.settings (JWT_SECRET is
mandatory there; DATABASE_URL is read at import of src.config.database and by
the worker's SessionLocal).
"""
import os
import tempfile
from datetime import date
from pathlib import Path

ROOT=Path(tempfile.mkdtemp(prefix='ecology-tests-'))
os.environ['DATABASE_URL']='sqlite:///'+str(ROOT/'test.db')
os.environ['ARTIFACT_DIR']=str(ROOT/'artifacts')
os.environ['JWT_SECRET']='test-secret-not-for-deployment'
os.environ['REDIS_URL']='redis://127.0.0.1:6399/0'
os.environ['SEED_DEMO']='false'
os.environ['RUN_LEASE_SECONDS']='120'

import pytest
from fastapi.testclient import TestClient

import src.config.database as database
from src.config.database import Base
from src.constants.species import SPECIES
from src.models import AuditLog,ConservationNote,DiversityRun,RunAttempt,SpeciesCount,Survey,Transect,User
from src.services import publication
from src.utils.security import hash_password,issue_token


@pytest.fixture(autouse=True)
def clean_artifacts():
 from src.services import artifacts
 folder=artifacts.directory()
 for entry in folder.iterdir():
  if entry.is_file():entry.unlink()
 yield


@pytest.fixture()
def db():
 Base.metadata.drop_all(database.engine);Base.metadata.create_all(database.engine)
 session=database.SessionLocal()
 try:yield session
 finally:session.close()


@pytest.fixture()
def seeded(db):
 survey=Survey(name='测试调查期',survey_date=date(2026,10,14),filename='test.csv',description='test')
 db.add(survey);db.commit()
 vectors=[[10,5]+[0]*22,[7,3,2]+[0]*21]
 transects=[]
 for index,values in enumerate(vectors):
  t=Transect(survey_id=survey.id,reference=f'SJ-{index+1:02d}',name=f'样线{index+1}',habitat='测试生境')
  db.add(t);db.flush();transects.append(t)
  db.add_all([SpeciesCount(transect_id=t.id,species=s,count=c) for s,c in zip(SPECIES,values)])
 empty=Transect(survey_id=survey.id,reference='SJ-99',name='空样线',habitat='测试生境')
 db.add(empty);db.flush();db.add_all([SpeciesCount(transect_id=empty.id,species=s,count=0) for s in SPECIES])
 users={}
 for role,email in [('admin','admin@test.demo'),('analyst','analyst@test.demo'),('viewer','viewer@test.demo')]:
  u=User(email=email,name=role,role=role,password_hash=hash_password('password123'));db.add(u);users[role]=u
 db.commit()
 for t in transects:db.refresh(t)
 db.refresh(survey);db.refresh(empty)
 return {'survey':survey,'transects':transects,'empty':empty,'users':users}


@pytest.fixture()
def client(db,seeded):
 import src.main as main
 def override():
  session=database.SessionLocal()
  try:yield session
  finally:session.close()
 main.app.dependency_overrides[database.get_db]=override
 with TestClient(main.app) as c:
  c.seed_data=seeded
  c.auth_headers={'Authorization':'Bearer '+issue_token(seeded['users']['admin'])}
  yield c
 main.app.dependency_overrides.clear()


@pytest.fixture()
def broker(monkeypatch):
 """Capture dispatched attempts instead of talking to Redis."""
 sent=[]
 monkeypatch.setattr(publication,'dispatch',lambda run_id,attempt_id:sent.append((run_id,attempt_id)))
 return sent
