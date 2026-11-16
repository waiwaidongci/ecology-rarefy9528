import logging
from contextlib import asynccontextmanager
from fastapi import Depends,FastAPI
from sqlalchemy import text
from redis import Redis
from src.config.database import Base,engine
from src.config.settings import SEED_DEMO,REDIS_URL
from src.models import *
from src.middlewares.request_id import RequestIDMiddleware
from src.controllers.dependencies import current_user,writers,admins
from src.config.database import get_db
from sqlalchemy.orm import Session
from src.services import runs as run_service
from src.middlewares.errors import ErrorMiddleware
from src.routes import auth,surveys,transects,runs,notes,audit
@asynccontextmanager
async def lifespan(app):
 Base.metadata.create_all(engine)
 from src.services.schema import ensure_publication_unit
 ensure_publication_unit(engine)
 if SEED_DEMO:
  try:
   from scripts.seed_demo import seed
   seed()
  except Exception:logging.exception('Seed failure; startup continues')
 yield
app=FastAPI(title='松际生态样线研究档案',lifespan=lifespan)
app.add_middleware(ErrorMiddleware);app.add_middleware(RequestIDMiddleware)
for module in (auth,surveys,transects,runs,notes,audit):app.include_router(module.router)
@app.post('/runs/recover')
def recover(db:Session=Depends(get_db),user=Depends(admins)):
 return {'recovered':run_service.recover(db)}
@app.get('/health')
def health():
 with engine.connect() as c:c.execute(text('select 1'))
 Redis.from_url(REDIS_URL).ping();return {'status':'ok'}
