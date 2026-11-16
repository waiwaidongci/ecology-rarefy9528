import {useState} from 'react';import type {ArchiveCheck,Run,RunAttempt,User} from '../types/domain';import {researchApi} from '../api/research';import {Badge} from './common/Badge';import {Table} from './common/Table';import {useAsync} from '../hooks/useAsync';import {date} from '../utils/format';
const short=(v:string|null|undefined,n=16)=>v?v.slice(0,n)+'…':'—';
export function AttemptPanel({run,user,reload}:{run:Run;user:User;reload:()=>Promise<any>}){
 const {busy,error,message,act}=useAsync(),[check,setCheck]=useState<ArchiveCheck|null>(null),[reason,setReason]=useState('worker 失联，接管未发布的尝试');
 const attempts=run.attempts||[],publishedId=run.published_attempt_id,canAct=user.role==='admin'&&run.status!=='succeeded';
 async function verify(){const report=await researchApi.verify(run.id);setCheck(report);if(!report.ok)throw new Error('归档核对未通过：'+(report.reason||'未知原因'));}
 return <section className="attempt-panel">
  <div className="section-title"><h2>发布单元 / 尝试与恢复</h2><small>结果、完成状态与导出依据同批发布；仅当前有效尝试可写回</small></div>
  <div className="publication-strip">
   <div><small>发布尝试</small><b>{publishedId?('A'+String(attempts.find(a=>a.id===publishedId)?.attempt_no??publishedId)):'未发布'}</b></div>
   <div><small>归档文件</small><b className="mono">{run.artifact_path?.split('/').pop()||'—'}</b></div>
   <div><small>SHA-256</small><b className="mono">{short(run.artifact_sha256,20)}</b></div>
   <div><small>字节 / 发布于</small><b>{run.artifact_bytes??'—'}<span>{run.published_at?date(run.published_at):''}</span></b></div>
  </div>
  <Table headers={['尝试','状态','执行者','开始 / 结束','说明','归档']}>{attempts.map((a:RunAttempt)=><tr key={a.id} className={a.id===publishedId?'selected':''}>
   <td><small>{a.id===run.current_attempt_id?'当前有效':'历史'}</small><strong>A{a.attempt_no??'—'}{a.recovered_from_attempt_id?' · 承接 A'+(attempts.find(x=>x.id===a.recovered_from_attempt_id)?.attempt_no??'?'):''}</strong></td>
   <td><Badge status={a.display_status}/>{a.id===publishedId&&<small>已发布</small>}</td>
   <td>{a.worker||'—'}</td>
   <td>{a.started_at?date(a.started_at):'—'}<small>{a.finished_at?date(a.finished_at):'—'}</small></td>
   <td className="detail">{a.reason||'—'}</td>
   <td className="mono">{short(a.artifact_sha256,12)}</td></tr>)}</Table>
  {!attempts.length&&<p className="fine">尚无尝试记录。</p>}
  {canAct&&<div className="attempt-actions"><label>恢复说明<input value={reason} onChange={e=>setReason(e.target.value)} maxLength={500}/></label>
   <button disabled={busy||!run.can_takeover} onClick={()=>act(async()=>{await researchApi.takeOver(run.id,reason);await reload()},'已接管：新尝试进入队列，旧尝试不再可发布')}>接管中断尝试 →</button>
   {(run.status==='failed'||run.status==='interrupted')&&<button disabled={busy||!run.can_takeover} onClick={()=>act(async()=>{await researchApi.retry(run.id,reason);await reload()},'已重试：新增尝试排队')}>重试研究 ↻</button>}
   <button disabled={busy} onClick={()=>act(async()=>{await researchApi.recover();await reload()},'已扫描过期租约')}>扫描失联尝试 ↻</button></div>}
  {run.status!=='succeeded'&&<p className="fine">中断不等于完成：中断尝试不产生已发布结果，可安全接管；迟到尝试的结果会被丢弃，不会覆盖新结果。</p>}
  <div className="attempt-actions">
   <button disabled={busy||run.status!=='succeeded'} onClick={()=>act(verify,'归档核对通过：文件、库内结果与发布尝试一致')}>核对已发布归档 ↗</button>
   {check&&<p className="fine">{check.ok?'✓ '+'尝试 A'+check.attempt_no+' · '+short(check.file?.sha256,24)+' · '+check.file?.bytes+' 字节':'✗ '+check.reason}</p>}
  </div>
  {check?.file?.path&&<p className="fine mono">{check.file.path}</p>}
 </section>}
