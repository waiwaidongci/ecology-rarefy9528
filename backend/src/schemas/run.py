from pydantic import BaseModel,Field,model_validator
class DiversityInput(BaseModel):
 name:str=Field(min_length=1,max_length=100)
 survey_id:int=Field(gt=0)
 transect_ids:list[int]=Field(min_length=1,max_length=30)
 m:int=Field(ge=0,le=100000,strict=True)
 bootstrap:int=Field(ge=50,le=1000,strict=True)
 seed:int=Field(ge=0,le=2147483647,strict=True)
 # Client-generated idempotency key. Two requests carrying the same key are the
 # same publication intent: the second returns the existing run without queueing
 # another attempt or creating another archive.
 request_key:str=Field(min_length=8,max_length=64,pattern=r'^[A-Za-z0-9_-]+$')
 @model_validator(mode='after')
 def unique_transects(self):
  if len(set(self.transect_ids))!=len(self.transect_ids):raise ValueError('样线不得重复')
  return self
class AttemptInput(BaseModel):
 reason:str=Field(min_length=1,max_length=500)
