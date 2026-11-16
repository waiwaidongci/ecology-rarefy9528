from fastapi import HTTPException
from src.models import ConservationNote,DiversityRun
from src.constants.enums import NoteStatus,RunStatus
from src.services import publication
from src.utils.csv_export import csv_response
def export_note(db,id):
 note=db.get(ConservationNote,id)
 if not note:raise HTTPException(404,'观察建议不存在')
 if note.status!=NoteStatus.APPROVED:raise HTTPException(403,'只有获批观察建议可导出')
 run=db.get(DiversityRun,note.run_id)
 if not run or run.status!=RunStatus.SUCCEEDED:raise HTTPException(409,'所属研究尚未发布，无法导出')
 # The export carries the publication evidence, and refuses to leave if the
 # archive no longer matches what was published.
 check=publication.verify_artifact(db,run)
 if not check['ok']:raise HTTPException(409,'已发布归档无法核对：'+str(check['reason']))
 rows=[]
 for t in run.result['transects']:
  if t['id'] in note.transect_ids:rows.append([note.name,t['reference'],t['n'],run.result['m'],t['rarefied'],t['shannon'],t['simpson'],note.observation,note.suggested_action,note.review_note,'观察性差异，非因果承诺；区间仅条件于已观测组成'])
 response=csv_response(f'conservation-note-{id}.csv',rows,['建议','样线','原N','共同m','稀释期望物种','原N Shannon ln','原N Simpson 1-sump2','观察依据','观察动作','审核说明','边界'])
 response.headers['X-Publication-Run']=str(run.id)
 response.headers['X-Publication-Attempt']=str(run.published_attempt_id)
 response.headers['X-Archive-SHA256']=run.artifact_sha256 or ''
 response.headers['X-Archive-Name']=run.artifact_path.rsplit('/',1)[-1] if run.artifact_path else ''
 return response
