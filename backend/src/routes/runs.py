from fastapi import APIRouter,Depends,HTTPException,Response
from sqlalchemy.orm import Session
from src.config.database import get_db
from src.controllers.dependencies import current_user,writers,admins
from src.schemas.run import DiversityInput,AttemptInput
from src.services import runs
router=APIRouter()
@router.get('/runs')
def index(db:Session=Depends(get_db),user=Depends(current_user)):return runs.list_runs(db)
@router.get('/runs/{id}')
def detail(id:int,db:Session=Depends(get_db),user=Depends(current_user)):return runs.detail(db,id)
@router.post('/runs',status_code=201)
def create(data:DiversityInput,response:Response,db:Session=Depends(get_db),user=Depends(writers)):
 payload,created=runs.create_run(db,data,user)
 # 200 for an idempotent replay, 201 only for a newly created publication slot.
 if not created:response.status_code=200
 return payload
@router.get('/runs/{id}/attempts')
def attempts(id:int,db:Session=Depends(get_db),user=Depends(current_user)):return runs.publication.attempt_rows(db,runs.get_run(db,id).id)
@router.post('/runs/{id}/attempts',status_code=201)
def take_over(id:int,data:AttemptInput,db:Session=Depends(get_db),user=Depends(admins)):return runs.take_over(db,id,data.reason,user)
@router.post('/runs/{id}/retry',status_code=201)
def retry(id:int,data:AttemptInput,db:Session=Depends(get_db),user=Depends(admins)):return runs.retry(db,id,data.reason,user)
@router.get('/runs/{id}/verify')
def verify(id:int,db:Session=Depends(get_db),user=Depends(current_user)):return runs.verify(db,id)
@router.post('/runs/maintenance/sweep')
def sweep(db:Session=Depends(get_db),user=Depends(admins),dry_run:bool=True,grace_seconds:int|None=None):return runs.sweep(db,dry_run=dry_run,grace_seconds=grace_seconds)
