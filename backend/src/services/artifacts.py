import hashlib,json,os,tempfile,time
from pathlib import Path
from src.config.settings import ARTIFACT_DIR,ARTIFACT_GRACE_SECONDS


def sha256_of(data):return hashlib.sha256(data).hexdigest()

def discard(path):
 """Remove an archive that provably belongs to a rejected attempt."""
 try:
  if inside(path):os.unlink(path)
 except OSError:pass

def directory():
 path=Path(ARTIFACT_DIR);path.mkdir(parents=True,exist_ok=True);return path

def name_for(run_id,attempt_no):
 # Every attempt gets its own name: a late attempt can never rename over a newer
 # attempt's archive even if its DB write-back is rejected.
 return f'diversity-{run_id}-a{int(attempt_no)}.json'

def canonical(payload):
 return json.dumps(payload,ensure_ascii=False,allow_nan=False,separators=(',',':')).encode('utf-8')

def write(run_id,attempt_no,result):
 """Write the archive atomically (temp file + fsync + rename) before DB commit.

 If the process dies after this call but before the publication transaction
 commits, the file stays unreferenced and is removed by the orphan sweeper;
 the database never points at a missing or partially written archive.
 """
 data=canonical(result);target=directory()/name_for(run_id,attempt_no)
 fd,tmp=tempfile.mkstemp(prefix='.tmp-',dir=str(target.parent))
 try:
  with os.fdopen(fd,'wb') as handle:
   handle.write(data);handle.flush();os.fsync(handle.fileno())
  os.replace(tmp,target)
  dir_fd=os.open(str(target.parent),os.O_RDONLY)
  try:os.fsync(dir_fd)
  finally:os.close(dir_fd)
 except BaseException:
  try:os.unlink(tmp)
  except OSError:pass
  raise
 return {'path':str(target),'sha256':hashlib.sha256(data).hexdigest(),'bytes':len(data)}

def inside(path):
 """Only accept archive paths inside ARTIFACT_DIR; never follow escape paths."""
 root=Path(ARTIFACT_DIR).resolve()
 target=Path(path).resolve()
 return target==root or root in target.parents

def inspect(path,expected_sha=None,expected_bytes=None):
 report={'path':str(path),'exists':False,'sha256':None,'bytes':None,'fingerprint':None,'error':None}
 try:
  if not inside(path):raise ValueError('归档路径超出共享卷')
  target=Path(path)
  if not target.is_file():raise FileNotFoundError('归档文件不存在')
  data=target.read_bytes()
  report.update(exists=True,sha256=hashlib.sha256(data).hexdigest(),bytes=len(data))
  if expected_sha is not None and report['sha256']!=expected_sha:raise ValueError('SHA-256与发布记录不一致')
  if expected_bytes is not None and report['bytes']!=expected_bytes:raise ValueError('文件字节数与发布记录不一致')
  payload=json.loads(data.decode('utf-8'))
  if not isinstance(payload,dict):raise ValueError('归档内容不是JSON对象')
  report['fingerprint']=payload.get('fingerprint')
 except Exception as exc:
  report['error']=str(exc)[:300]
 return report

def referenced_paths(db):
 from sqlalchemy import select
 from src.models import DiversityRun
 paths=set()
 for path in db.scalars(select(DiversityRun.artifact_path).where(DiversityRun.artifact_path.is_not(None))):
  if path:paths.add(os.path.realpath(path))
 return paths

def sweep(db,dry_run=True,grace_seconds=None):
 """Delete unreferenced archives older than the grace period.

 Referenced = every path currently named by a run's publication record. The
 grace window covers the tiny gap between rename and commit during a live
 publication, and keeps fresh temp files; the DB transaction that publishes
 an archive is the authority, never directory listing order.
 """
 grace=ARTIFACT_GRACE_SECONDS if grace_seconds is None else max(0,grace_seconds)
 root=directory();referenced=referenced_paths(db);now=time.time();report={'scanned':0,'referenced':0,'deleted':[],'kept_referenced':[],'kept_recent':[],'dry_run':dry_run,'grace_seconds':grace}
 for entry in sorted(os.scandir(root),key=lambda e:e.name):
  if not entry.is_file(follow_symlinks=False):continue
  report['scanned']+=1;path=os.path.realpath(entry.path)
  if path in referenced:
   report['referenced']+=1;report['kept_referenced'].append(entry.name);continue
  try:age=now-entry.stat(follow_symlinks=False).st_mtime
  except OSError:continue
  if age<grace:
   report['kept_recent'].append(entry.name);continue
  if not dry_run:
   try:os.unlink(entry.path)
   except OSError as exc:
    report.setdefault('errors',[]).append({'file':entry.name,'error':str(exc)});continue
  report['deleted'].append(entry.name)
 return report
