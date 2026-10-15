import React,{useState,useEffect,useCallback,useRef} from 'react';
import {createRoot} from 'react-dom/client';
import {api,ApiError,passwordLogin,authorize,setCsrf,type RecordData,type Principal,type Envelope,type Capability} from './api';
import {Filters,SecretStage,serverFilters} from './forms';
import {DomainTable,DomainDetail,LatchBanner} from './domain';
import './style.css';
const screens=[['overview','Overview','Observe'],['signals','Signals & trace','Observe'],['trading','Trading desk','Trade'],['positions','Positions','Trade'],['orders','Orders & intents','Trade'],['protection','Protection','Trade'],['management','Management','Trade'],['recovery','Recovery & safety','Operate'],['account','Account & PnL','Observe'],['services','Services & deployment','Operate'],['config','Configuration','Operate'],['notifications','Notifications','Operate'],['audit','Audit & jobs','Govern'],['backups','Backups','Govern'],['access','Access','Govern']];
const tabs:Record<string,string[]>={orders:['orders','fills'],audit:['jobs','approvals','audit'],services:['services','releases'],account:['account','account/pnl','account/reservations','account/admission-snapshots'],config:['config','config/history','config/drafts'],access:['access','access/principals','access/sessions','access/policy'],recovery:['recovery','recovery/baselines']};
const labels:Record<string,string>={jobs:'Command jobs',approvals:'Approvals',audit:'Audit trail',fills:'Fills & ledger',releases:'Releases',orders:'Orders & intents'};
const human=(s:string)=>s.replace(/_/g,' ').replace(/\b\w/g,c=>c.toUpperCase());
const isObject=(x:unknown):x is RecordData=>typeof x==='object'&&x!==null&&!Array.isArray(x);
function route(){const parts=location.pathname.split('/').filter(Boolean);return {screen:screens.some(s=>s[0]===parts[0])?parts[0]:'overview',id:parts.slice(1).map(decodeURIComponent).join('/')};}
function navigate(path:string){history.pushState(null,'',path);window.dispatchEvent(new PopStateEvent('popstate'));}
function Link({to,children}:{to:string;children:React.ReactNode}){const path=to.split('/')[1]===location.pathname.split('/')[1]&&!to.includes('?')?to+location.search:to;return <a href={path} onClick={e=>{if(e.button===0&&!e.ctrlKey&&!e.metaKey&&!e.shiftKey&&!e.altKey){e.preventDefault();navigate(path);}}}>{children}</a>;}
function Value({value,depth=0}:{value:unknown;depth?:number}):React.ReactNode{
 if(value===null||value===undefined)return <span className="muted">Not observed</span>;
 if(typeof value==='boolean')return <span className={`badge ${value?'positive':'neutral'}`}>{value?'Yes':'No'}</span>;
 if(typeof value==='string'){
  if(value.startsWith('/')&&!value.startsWith('//'))return <Link to={value}>{value}</Link>;
  if(/^\d{4}-\d\d-\d\dT/.test(value)&&Number.isFinite(Date.parse(value))){const timezone=document.documentElement.dataset.timezone||'UTC';return <time dateTime={value} title={`Original UTC instant: ${value}`}>{new Intl.DateTimeFormat(undefined,{dateStyle:'medium',timeStyle:'long',timeZone:timezone}).format(new Date(value))}</time>;}
  if(/^-?\d+(?:\.\d+)?$/.test(value)){const [whole,fraction]=value.split('.');return <span className="number" title={value}>{whole.replace(/\B(?=(\d{3})+(?!\d))/g,',')}{fraction===undefined?'':'.'+fraction}</span>;}
  return <span className={value.length>180?'preserve':''}>{value}</span>;
 }
 if(typeof value==='number')return <span className="number">{value}</span>;
 if(Array.isArray(value)){if(!value.length)return <span className="muted">No records returned</span>;return <div className="stack">{value.map((v,i)=><div className="array-item" key={i}><Value value={v} depth={depth+1}/></div>)}</div>;}
 if(isObject(value)){
  if(typeof value.section==='string'&&value.id!==undefined){
   const section=value.section;
   const path=section==='jobs'?'audit/jobs':section==='dispatches'?'signals/dispatches':section==='fills'?'orders/fills':section;
   return <Link to={`/${path}/${encodeURIComponent(String(value.id))}`}>{human(section)} · {String(value.id)}</Link>;
  }
  return <dl className={depth===0?'record-grid':'record'}>{Object.entries(value).map(([k,v])=><div key={k}><dt>{human(k)}</dt><dd><Value value={v} depth={depth+1}/></dd></div>)}</dl>;
 }
 return String(value);
}
function Evidence({value}:{value:unknown}){const list=Array.isArray(value)?value:isObject(value)?[value]:[];return <div className="evidence">{list.map((item,i)=>{const e=isObject(item)?item:{};const time=e.observed_at?Date.parse(String(e.observed_at)):NaN;const age=Number.isFinite(time)?Math.max(0,Math.floor((Date.now()-time)/1000)):null;const stale=age!==null&&typeof e.stale_after_seconds==='number'&&age>e.stale_after_seconds;return <details key={i}><summary><span className={`dot ${stale||e.freshness==='stale'?'warn':e.freshness==='fresh'?'good':'unknown'}`}/>{String(e.id||e.source||'Evidence')} · {String(e.read_status||'unknown')} · {age===null?'age unknown':`${age}s ago`}{stale?' · stale':''}</summary><Value value={e}/></details>;})}</div>;}
function ErrorBox({error}:{error:unknown}){return error?<div role="alert" className="alert danger">{error instanceof Error?error.message:String(error)}{error instanceof ApiError&&error.detail.request_id?<small>Request {String(error.detail.request_id)}</small>:null}</div>:null;}
function Auth({onAuthenticated}:{onAuthenticated:()=>void}){
 const [password,setPassword]=useState(''),[busy,setBusy]=useState(false),[error,setError]=useState<unknown>(null);
 async function submit(e:React.FormEvent){
  e.preventDefault();setError(null);setBusy(true);
  const submitted=password;setPassword('');
  try{await passwordLogin(submitted);onAuthenticated();}
  catch(e){setError(e);}finally{setBusy(false);}
 }
 return <main className="auth"><div className="auth-card">
  <span className="eyebrow">PRIVATE OPERATOR CONSOLE</span><h1>Evidence before action.</h1>
  <p>Sign in with the shared console password to access scoped account observations and durable management jobs.</p><ErrorBox error={error}/>
  <form onSubmit={submit} data-testid="password-auth">
   <label>Console password<input data-testid="console-password" type="password" autoComplete="current-password" value={password} onChange={e=>setPassword(e.target.value)} required disabled={busy}/></label>
   <button className="primary" data-testid="password-login" disabled={busy}>{busy?'Signing in…':'Sign in'}</button>
  </form>
  <p className="muted">Use the authorized HTTPS origin or localhost SSH tunnel. Password input is held only in memory and cleared on submission.</p>
 </div></main>;
}
function SchemaField({name,schema,value,onChange,required=false}:{name:string;schema:RecordData;value:unknown;onChange:(v:unknown)=>void;required?:boolean}){
 const type=String(schema.type||'string'),title=String(schema.title||human(name));
 const secret=/secret|password|token|credential/i.test(name)||schema.format==='password';
 if(Array.isArray(schema.oneOf)){
  const variants=schema.oneOf.filter(isObject);const selected=variants.findIndex(candidate=>candidate.type===typeof value||(candidate.type==='integer'&&typeof value==='number'));const choice=Math.max(0,selected);
  return <fieldset><legend>{human(name)} input type</legend><select aria-label={`${human(name)} input type`} value={choice} onChange={e=>onChange(variants[Number(e.target.value)]?.type==='integer'?0:'')}>{variants.map((variant,index)=><option key={index} value={index}>{variant.type==='integer'?'Numeric channel ID':'Public @channel identifier'}</option>)}</select><SchemaField name={name} schema={variants[choice]||{}} value={value} required={required} onChange={onChange}/></fieldset>;
 }
 if(type==='object'){
  const properties=isObject(schema.properties)?schema.properties:{},data=isObject(value)?value:{};
  return <fieldset><legend>{title}</legend>{Object.entries(properties).map(([key,s])=><SchemaField key={key} name={key} schema={isObject(s)?s:{}} value={data[key]} required={Array.isArray(schema.required)&&schema.required.includes(key)} onChange={v=>onChange({...data,[key]:v})}/>)}</fieldset>;
 }
 const itemSchema=isObject(schema.items)?schema.items:{};
 const enumItems=Array.isArray(itemSchema.enum)?itemSchema.enum:null;
 return <label>{title}{required?' *':''}
  {Array.isArray(schema.enum)?<select value={value===undefined?'':JSON.stringify(value)} required={required} onChange={e=>onChange(e.target.value?JSON.parse(e.target.value):undefined)}><option value="">Choose…</option>{schema.enum.map(v=><option key={JSON.stringify(v)} value={JSON.stringify(v)}>{String(v)}</option>)}</select>
  :type==='boolean'?<select value={value===undefined?'':String(value)} required={required} onChange={e=>onChange(e.target.value===''?undefined:e.target.value==='true')}><option value="">Choose…</option>{schema.const!==false&&<option value="true">Enabled / accepted</option>}{schema.const!==true&&<option value="false">Disabled / rejected</option>}</select>
  :type==='array'&&enumItems?<select multiple required={required} value={Array.isArray(value)?value.map(v=>JSON.stringify(v)):[]} onChange={e=>onChange(Array.from(e.target.selectedOptions,o=>JSON.parse(o.value)))}>{enumItems.map(v=><option key={JSON.stringify(v)} value={JSON.stringify(v)}>{String(v)}</option>)}</select>
  :type==='array'?<div className="array-editor">{(Array.isArray(value)?value:[]).map((entry,index)=><div className="array-item" key={index}><SchemaField name={`${name} ${index+1}`} schema={itemSchema} value={entry} required onChange={v=>onChange((value as unknown[]).map((old,i)=>i===index?v:old))}/><button type="button" onClick={()=>onChange((value as unknown[]).filter((_,i)=>i!==index))}>Remove item {index+1}</button></div>)}<button type="button" disabled={Array.isArray(value)&&value.length>=Number(schema.maxItems??200)} onClick={()=>onChange([...(Array.isArray(value)?value:[]),itemSchema.type==='object'?{}:itemSchema.type==='boolean'?false:''])}>Add {title} item</button></div>
  :<input data-testid={`argument-${name}`} type={secret?'password':type==='integer'||type==='number'?'number':schema.format==='date-time'?'datetime-local':'text'} step={type==='integer'?'1':'any'} autoComplete="off" required={required} value={schema.format==='date-time'&&typeof value==='string'?value.replace(/Z$/,'').slice(0,16):String(value??'')} min={schema.minimum as number|undefined} max={schema.maximum as number|undefined} minLength={schema.minLength as number|undefined} maxLength={schema.maxLength as number|undefined} pattern={schema.pattern as string|undefined} onChange={e=>onChange(e.target.value===''?undefined:type==='integer'||type==='number'?Number(e.target.value):schema.format==='date-time'?new Date(e.target.value+'Z').toISOString():e.target.value)}/>} 
  {Boolean(schema.description)&&<small>{String(schema.description)}</small>}
  {schema.const!==undefined&&<small>Fixed policy value: {String(schema.const)}. The server rejects every other value.</small>}
  {schema.format==='date-time'&&<small>Enter UTC; display timezone does not change the operation cutoff.</small>}
  {type==='array'&&<small>Item constraints: {JSON.stringify(itemSchema)}. Minimum {String(schema.minItems??0)}; maximum {String(schema.maxItems??'server policy')}.</small>}
  {secret&&<small>Secret input stays in memory only; never included in URL or browser storage.</small>}
 </label>;
}
type PendingOperation={key:string;action:string;payload:RecordData};
function storedOperation(key:string):PendingOperation|null{
 try{const raw=sessionStorage.getItem(key);if(!raw)return null;const value=JSON.parse(raw) as PendingOperation;return typeof value.key==='string'&&typeof value.action==='string'&&isObject(value.payload)?value:null;}catch{return null;}
}
function ReviewDialog({children}:{children:React.ReactNode}){
 const dialog=useRef<HTMLDialogElement>(null);
 useEffect(()=>{dialog.current?.showModal();},[]);
 return <><button onClick={()=>dialog.current?.showModal()}>Open exact preview review</button><dialog ref={dialog} className="review-dialog" aria-labelledby="preview-title"><button className="dialog-close" onClick={()=>dialog.current?.close()}>Close review</button>{children}</dialog></>;
}
function ActionPanel({capabilities,scope,section,items,onChanged,principalId,initialAction=''}:{capabilities:Capability[];scope:RecordData;section:string;items:RecordData[];onChanged:()=>void;principalId:string;initialAction?:string}){
 const [action,setAction]=useState(initialAction),[args,setArgs]=useState<RecordData>({}),[targets,setTargets]=useState<string[]>([]),[reason,setReason]=useState(''),[preview,setPreview]=useState<RecordData|null>(null),[phrase,setPhrase]=useState(''),[approval,setApproval]=useState(''),[busy,setBusy]=useState(false),[error,setError]=useState<unknown>(null),[job,setJob]=useState<RecordData|null>(null),[acknowledged,setAcknowledged]=useState(false),[verified,setVerified]=useState(''),[unaccepted,setUnaccepted]=useState(false);
 const recoveryStorage=`console.pending.${principalId}.${String(scope.account_id)}.${String(scope.environment)}.${section}`;
 const [pending,setPending]=useState<PendingOperation|null>(()=>storedOperation(recoveryStorage));
 const cap=capabilities.find(c=>c.action===action);
 const expired=preview?.expires_at?Date.parse(String(preview.expires_at))<=Date.now():true;
 const tier=Number(preview?.danger_tier??0);
 const gatesPass=Array.isArray(preview?.gates)&&preview.gates.every(g=>isObject(g)&&g.verdict==='pass');
 function invalidate(){setPreview(null);setPhrase('');setJob(null);setAcknowledged(false);setVerified('');}
 function accepted(result:RecordData){setJob(result);setPreview(null);sessionStorage.removeItem(recoveryStorage);setPending(null);onChanged();}
 async function recover(){
  if(!pending)return;setBusy(true);setError(null);
  try{
   try{const receipt=await api<Envelope>(`/jobs/by-idempotency/${encodeURIComponent(pending.key)}?action=${encodeURIComponent(pending.action)}`);if(isObject(receipt.data)){accepted(receipt.data);return;}}
   catch(e){if(!(e instanceof ApiError&&e.status===404))throw e;}
   const p=await api<RecordData>(`/previews/${encodeURIComponent(String(pending.payload.preview_id))}`);
   setPreview(p);setAction(pending.action);setReason(String(pending.payload.reason));setApproval(String(pending.payload.approval_reference||''));setPhrase(String(pending.payload.confirmation_phrase||''));setAcknowledged(false);setVerified('');
  }catch(e){setError(e);}finally{setBusy(false);}
 }
 async function makePreview(e:React.FormEvent){
  e.preventDefault();if(pending)return;setError(null);setBusy(true);
  try{
   const refs=targets.map(id=>{const item=items.find(i=>String(i.id)===id);return {kind:section,id,resource_version:item?.resource_version};});
   const p=await api<RecordData>('/previews',{action,arguments:args,target_refs:refs,reason,scope,policy_version:cap?.policy_version});
   setPreview(p);setPhrase('');setAcknowledged(false);setVerified('');
  }catch(e){setError(e);}finally{setBusy(false);}
 }
 async function submit(){
  if(!preview)return;setBusy(true);setError(null);
  try{
   const operation=pending||{key:crypto.randomUUID(),action,payload:{preview_id:preview.id,preview_digest:preview.preview_digest,confirmation_phrase:phrase,reason,approval_reference:approval||null,policy_version:preview.policy_version,resource_version:preview.resource_version}};
   sessionStorage.setItem(recoveryStorage,JSON.stringify(operation));setPending(operation);
   const result=await api<RecordData>('/jobs',operation.payload,{'Idempotency-Key':operation.key,'If-Match':String(operation.payload.resource_version)});
   accepted(result);
  }catch(e){setError(e);if(e instanceof ApiError&&[400,403,404,409,412,422,428].includes(e.status)&&e.detail.code!=='idempotency_conflict')setUnaccepted(true);}finally{setBusy(false);}
 }
 return <section className="panel actions" data-testid="command-panel">
  <div className="section-heading"><div><span className="eyebrow">DURABLE COMMANDS</span><h2>Review → authorize → queue</h2></div></div>
  <p className="muted">Preview is read-only. The server rechecks ownership, freshness, gates and approval before execution. Accepted is not executed.</p>
  <ErrorBox error={error}/>
  {pending&&!job&&<div className="alert warning"><strong>An operation may already have been accepted.</strong><p>Recover its durable receipt first. Retry uses the exact persisted public approval payload and original operation ID; no account credentials, passwords or CSRF tokens are stored.</p><button disabled={busy} onClick={recover}>Recover same operation</button><Link to="/audit">Inspect durable jobs</Link>{unaccepted&&<button disabled={busy} onClick={()=>{sessionStorage.removeItem(recoveryStorage);setPending(null);setUnaccepted(false);invalidate();}}>Discard request proved unaccepted</button>}</div>}
  <form onSubmit={makePreview}><fieldset disabled={busy||!!pending} className="command-fields">
   <label>Action catalog<select data-testid="action-catalog" value={action} onChange={e=>{setAction(e.target.value);setArgs({});setTargets([]);invalidate();}}><option value="">Choose an action…</option>{capabilities.map(c=><option key={c.action} value={c.action}>{c.action}{!c.preview_allowed?' · unavailable':''}</option>)}</select></label>
   {cap&&<>
    <div className="capability"><strong>Danger tier {cap.danger_tier} · {cap.required_role||'Scoped authorization'}</strong><p>{cap.description}</p><Value value={{reasons:cap.reasons,gates:cap.gates}}/></div>
    {cap.arguments_schema?<SchemaField name="arguments" schema={cap.arguments_schema} value={args} onChange={v=>{setArgs(v as RecordData);invalidate();}}/>:<p role="status">No editable arguments declared for this action.</p>}
    <label>Exact target records<select data-testid="target-chooser" multiple value={targets} onChange={e=>{setTargets(Array.from(e.target.selectedOptions,o=>o.value));invalidate();}}>{items.filter(i=>i.id!==undefined&&typeof i.resource_version==='string').map(i=><option key={String(i.id)} value={String(i.id)}>{String(i.symbol||i.display_name||i.action||section)} · {String(i.id)}</option>)}</select><small>Only versioned scoped records are selectable. Ownership, exact client order roots and account scope are revalidated by the server; selecting a symbol never adopts external exposure.</small></label>
    {targets.length>0&&<div className="capability"><strong>Selected exact targets and observed versions</strong><Value value={items.filter(item=>targets.includes(String(item.id))).map(item=>({kind:section,id:item.id,resource_version:item.resource_version,ownership:item.ownership??null,ownership_root:item.ownership_root??null,client_order_id:item.client_order_id??(isObject(item.ledger_intent)?item.ledger_intent.client_order_id:null)}))}/></div>}
    <label>Operator reason<textarea data-testid="operator-reason" required maxLength={1000} value={reason} onChange={e=>{setReason(e.target.value);invalidate();}}/></label>
    <button disabled={busy||!cap.preview_allowed} data-testid="generate-preview" className="primary">{busy?'Reading authoritative evidence…':'Generate read-only preview'}</button>
   </>}
  </fieldset></form>
  {preview&&<ReviewDialog><div className="preview" data-testid="action-preview">
   <h3 id="preview-title">Full effects, exclusions & gates</h3><Value value={preview}/>
   <p className={expired?'alert danger':'muted'}>{expired?'Preview expired: only receipt retrieval / identical replay is permitted. An unaccepted request needs a new preview.':`Expires ${String(preview.expires_at)}`}</p>
   <label className="risk-acknowledgment"><input type="checkbox" checked={acknowledged} onChange={e=>setAcknowledged(e.target.checked)}/>I reviewed the exact effects, exclusions, evidence gates and all warnings above. Accepted work is not execution proof.</label>
   {tier>=2&&<><label>Exact confirmation phrase<input data-testid="confirmation-phrase" disabled={!!pending} value={phrase} onChange={e=>setPhrase(e.target.value)} autoComplete="off" placeholder={String(preview.confirmation_phrase||'')}/></label>
   <label>Approval reference<input disabled={!!pending} required maxLength={200} value={approval} onChange={e=>setApproval(e.target.value)} autoComplete="off"/><small>Actions requiring a distinct approver remain unavailable under the shared-password identity; this reference does not replace that policy.</small></label></>}
   <ErrorBox error={error}/><div className="button-row">
    {tier>=2&&<button data-testid="preview-consent" disabled={busy||expired||!acknowledged} onClick={async()=>{setBusy(true);setError(null);try{await authorize({preview_digest:String(preview.preview_digest)});setVerified(String(preview.preview_digest));onChanged();}catch(e){setError(e);}finally{setBusy(false);}}}>{verified===preview.preview_digest?'Exact digest authorized':'Authorize reviewed digest'}</button>}
    <button data-testid="enqueue-job" className="danger-button" disabled={busy||!acknowledged||!!job||(!pending&&(expired||!gatesPass||!cap?.enqueue_allowed||(tier>=2&&(phrase!==String(preview.confirmation_phrase)||!approval.trim()||verified!==preview.preview_digest))))} onClick={submit}>{pending?'Retrieve / retry exact same operation':'Queue exact approved operation'}</button>
   </div>
   <small>An ambiguous outcome keeps the same operation ID after reload. Never create a new command to resolve a claimed effect. All authority remains server-enforced.</small>
  </div></ReviewDialog>}
  {job&&<div role="status" className="alert"><h3>Durable job accepted</h3><Value value={job}/>{Boolean(job.id)&&<Link to={`/audit/jobs/${encodeURIComponent(String(job.id))}`}>Follow job and reconciliation</Link>}</div>}
 </section>;
}
function JobControls({job,onChanged}:{job:RecordData;onChanged:()=>void}){
 const [reason,setReason]=useState(''),[busy,setBusy]=useState(false),[error,setError]=useState<unknown>(null),[verified,setVerified]=useState('');
 async function command(name:string){
  setBusy(true);setError(null);
  try{
   await api(`/jobs/${encodeURIComponent(String(job.id))}/${name}`,{preview_digest:job.preview_digest,resource_version:job.resource_version,reason},{'If-Match':String(job.resource_version)});setVerified('');
   onChanged();
  }catch(e){setError(e);}finally{setBusy(false);}
 }
 return <div className="panel">
  <h2>Job authorization & lifecycle</h2><p>Cancellation is a request, not proof that an in-flight effect stopped. Reconciliation uses authoritative readback, not replay.</p>
  <ErrorBox error={error}/><p>Exact approval digest: <code>{String(job.preview_digest)}</code> · observed revision {String(job.resource_version)}. Review the requested arguments, effects and durable steps before authorizing.</p><label>Reason<textarea maxLength={1000} value={reason} onChange={e=>setReason(e.target.value)} required/></label>
  <div className="button-row">
   {['approve','reject','cancel','reconcile'].map(name=>{const eligible=name==='reconcile'?job.state==='reconciliation_pending':name==='cancel'?!['succeeded','failed','partially_succeeded','rejected','expired','cancelled'].includes(String(job.state)):['awaiting_approval','queued'].includes(String(job.state))&&!job.effect_claimed;return <button title={eligible?'Server revalidates authority and exact revision':'Unavailable in the observed job state'} data-testid={`job-${name}`} key={name} disabled={busy||!reason.trim()||!eligible||(['approve','reject'].includes(name)&&verified!==job.preview_digest)} onClick={()=>command(name)}>{human(name)}</button>;})}
   {['awaiting_approval','queued'].includes(String(job.state))&&<button data-testid="job-consent" disabled={busy} onClick={async()=>{setBusy(true);setError(null);try{await authorize({job_id:String(job.id)});setVerified(String(job.preview_digest));onChanged();}catch(e){setError(e);}finally{setBusy(false);}}}>Authorize reviewed job digest</button>}
   {job.action==='report.export'&&job.state==='succeeded'&&<a href={`/api/v1/jobs/${encodeURIComponent(String(job.id))}/download`}>Download authorized export</a>}
  </div>
 </div>;
}
function RelatedRead({path,revision,title}:{path:string;revision:unknown;title:string}){
 const [snapshot,setSnapshot]=useState<Envelope|null>(null),[error,setError]=useState<unknown>(null);
 useEffect(()=>{
  const controller=new AbortController();setError(null);setSnapshot(null);
  void api<Envelope>(path,undefined,{},controller.signal).then(setSnapshot).catch(e=>{if(e.name!=='AbortError')setError(e);});
  return()=>controller.abort();
 },[path,revision]);
 return <section className="panel"><h2>{title}</h2><ErrorBox error={error}/>{snapshot?<><Evidence value={snapshot.evidence}/><Value value={snapshot.data}/><Value value={snapshot.warnings}/></>:error?<p>Linked evidence unavailable. No successful read is inferred.</p>:<p role="status">Loading linked evidence…</p>}</section>;
}
function App(){
 const [principal,setPrincipal]=useState<Principal|null>(null),[checking,setChecking]=useState(true);
 const [current,setCurrent]=useState(route),[tab,setTab]=useState(()=>new URLSearchParams(location.search).get('view')||'');
 const [envelope,setEnvelope]=useState<Envelope|null>(null),[safety,setSafety]=useState<Envelope|null>(null),[caps,setCaps]=useState<Capability[]>([]);
 const [error,setError]=useState<unknown>(null),[loading,setLoading]=useState(false);
 const [cursor,setCursor]=useState(()=>new URLSearchParams(location.search).get('cursor')||''),[historyCursors,setHistoryCursors]=useState<string[]>([]),[filters,setFilters]=useState<Record<string,string>>(()=>Object.fromEntries([...new URLSearchParams(location.search)].filter(([key])=>!['view','cursor'].includes(key))));
 const [refresh,setRefresh]=useState(0),[connection,setConnection]=useState('connecting'),[mobile,setMobile]=useState(false);
 const [theme,setTheme]=useState(()=>localStorage.getItem('console.theme')||'system'),[timezone,setTimezone]=useState(()=>localStorage.getItem('console.timezone')||'UTC'),[,tick]=useState(0);
 const eventCursor=useRef<string>(''),liveRefresh=useRef<number|undefined>(undefined);
 const subSection=current.id.split('/')[0];
 const nestedSection=`${current.screen}/${subSection}`;
 const linkedSection=current.screen==='signals'&&subSection==='dispatches'?'dispatches':current.screen==='orders'&&subSection==='fills'?'fills':current.screen==='audit'&&subSection==='jobs'?'jobs':null;
 const isNested=Boolean(linkedSection)||Object.hasOwn(serverFilters,nestedSection)||nestedSection==='access/policy';
 const readSection=current.screen==='trading'?'positions':isNested?linkedSection||nestedSection:tab&&tabs[current.screen]?.includes(tab)?tab:tabs[current.screen]?.[0]||current.screen;
 const resourceId=isNested?current.id.split('/').slice(1).join('/'):current.id;
 const reload=useCallback(()=>setRefresh(r=>r+1),[]);
 async function session(){setChecking(true);try{const result=await api<RecordData>('/session');const p=result.principal as Principal;setCsrf(String(result.csrf_token||p.csrf_token));setPrincipal(p);setError(null);}catch(e){setPrincipal(null);if(!(e instanceof ApiError&&e.status===401))setError(e);}finally{setChecking(false);}}
 useEffect(()=>{void session();const fn=()=>{const q=new URLSearchParams(location.search);setCurrent(route());setTab(q.get('view')||'');setCursor(q.get('cursor')||'');setHistoryCursors([]);setFilters(Object.fromEntries([...q].filter(([key])=>!['view','cursor'].includes(key))));setEnvelope(null);setMobile(false);document.getElementById('content')?.focus();};window.addEventListener('popstate',fn);const timer=setInterval(()=>tick(t=>t+1),1000);return ()=>{window.removeEventListener('popstate',fn);clearInterval(timer);};},[]);
 useEffect(()=>{const q=new URLSearchParams(Object.fromEntries(Object.entries(filters).filter(([,v])=>v)));if(tab)q.set('view',tab);if(cursor)q.set('cursor',cursor);history.replaceState(null,'',location.pathname+(q.size?'?'+q:''));},[filters,tab,cursor]);
 useEffect(()=>{localStorage.setItem('console.timezone',timezone);document.documentElement.dataset.timezone=timezone;},[timezone]);
 useEffect(()=>{document.documentElement.dataset.theme=theme;localStorage.setItem('console.theme',theme);},[theme]);
 useEffect(()=>{
  const changed=(event:Event)=>{
   const result=(event as CustomEvent<RecordData>).detail;
   const next=result.principal as Principal|undefined;
   if(next)setPrincipal(next);
  };
  const expired=()=>{setPrincipal(null);setCsrf('');setEnvelope(null);setSafety(null);setCaps([]);eventCursor.current='';setConnection('session expired');setError(new Error('Session expired or was revoked. Sign in again; existing jobs retain their durable outcome.'));};
  window.addEventListener('console-session',changed);window.addEventListener('console-session-expired',expired);
  return()=>{window.removeEventListener('console-session',changed);window.removeEventListener('console-session-expired',expired);};
 },[]);
 useEffect(()=>{if(!principal)return;const controller=new AbortController();setLoading(true);setError(null);const q=new URLSearchParams({...Object.fromEntries(Object.entries(filters).filter(([,v])=>v)),limit:'25',...(cursor?{cursor}:{})});api<Envelope>(`/${readSection}${resourceId?'/'+encodeURIComponent(resourceId):''}?${q}`,undefined,{},controller.signal).then(r=>{if(controller.signal.aborted)return;setEnvelope(r);if(!eventCursor.current)eventCursor.current=String(r.event_cursor??'');}).catch(e=>{if(controller.signal.aborted||e.name==='AbortError')return;setError(e);}).finally(()=>{if(!controller.signal.aborted)setLoading(false);});return()=>controller.abort();},[principal,current,readSection,resourceId,filters,cursor,refresh]);
 useEffect(()=>{if(!principal)return;const controller=new AbortController();void api<Envelope>('/overview',undefined,{},controller.signal).then(r=>{if(!controller.signal.aborted)setSafety(r);}).catch(e=>{if(!controller.signal.aborted)setError(e);});void api<Envelope>('/actions',undefined,{},controller.signal).then(r=>{if(controller.signal.aborted)return;const catalog=isObject(r.data)&&Array.isArray(r.data.items)?r.data.items:[];setCaps(catalog as Capability[]);}).catch(e=>{if(!controller.signal.aborted)setError(e);});return()=>controller.abort();},[principal,refresh]);
 useEffect(()=>{
  if(!principal)return;
  let source:EventSource|null=null,stopped=false,reconnect:number|undefined,delay=1000,last=Date.now();
  function invalidate(){clearTimeout(liveRefresh.current);liveRefresh.current=setTimeout(reload,350);}
  async function connect(){
   if(!eventCursor.current){
    try{const snapshot=await api<Envelope>('/overview');setSafety(snapshot);eventCursor.current=String(snapshot.event_cursor??'');}
    catch(e){setError(e);setConnection('snapshot unavailable');if(!stopped)reconnect=setTimeout(connect,delay);return;}
   }
   if(stopped)return;
   source=new EventSource(`/api/v1/events?cursor=${encodeURIComponent(eventCursor.current)}`);
   source.onopen=()=>{setConnection('live');last=Date.now();delay=1000;};
   const event=(e:MessageEvent)=>{last=Date.now();if(e.lastEventId&&/^\d+$/.test(e.lastEventId)){if(eventCursor.current&&BigInt(e.lastEventId)<=BigInt(eventCursor.current))return;eventCursor.current=e.lastEventId;}invalidate();};
   source.onmessage=event;source.addEventListener('change',event as EventListener);
   source.addEventListener('heartbeat',()=>{last=Date.now();setConnection('live');});
   const resync=()=>{eventCursor.current='';setEnvelope(null);setSafety(null);setCaps([]);setConnection('resynchronizing');invalidate();source?.close();if(!stopped)reconnect=setTimeout(connect,1000);};
   source.addEventListener('resync',resync);source.addEventListener('resync_required',resync);
   source.addEventListener('session_revoked',()=>{stopped=true;source?.close();window.dispatchEvent(new Event('console-session-expired'));});
   source.onerror=()=>{setConnection('disconnected — last known data');source?.close();void api('/session').catch(e=>{if(!(e instanceof ApiError&&e.status===401))setError(e);});if(!stopped)reconnect=setTimeout(connect,delay+Math.random()*500);delay=Math.min(delay*2,30000);};
  }
  void connect();
  const heartbeat=setInterval(()=>{if(Date.now()-last>45000){setConnection('stale connection — reconnecting');source?.close();last=Date.now();if(!stopped){clearTimeout(reconnect);reconnect=setTimeout(connect,delay);}}},5000);
  return()=>{stopped=true;source?.close();clearTimeout(reconnect);clearInterval(heartbeat);clearTimeout(liveRefresh.current);};
 },[principal,reload]);
 const data=envelope?.data;const list=isObject(data)&&Array.isArray(data.items)?data.items:[];const items=list.filter(isObject);const screenName=screens.find(s=>s[0]===current.screen)?.[1]||'Overview';const safetyData=isObject(safety?.data)?safety.data:{};
 if(checking&&!principal)return <main className="auth"><p role="status">Checking secure session…</p></main>;if(!principal)return <><ErrorBox error={error}/><Auth onAuthenticated={()=>void session()}/></>;
 return <div className="app">
  <a className="skip" href="#content">Skip to content</a>
  <aside className={mobile?'sidebar open':'sidebar'}>
   <Link to="/overview"><span className="brand">F<span>FATTY / CONSOLE</span></span></Link>
   <nav aria-label="Main navigation">{['Observe','Trade','Operate','Govern'].map(group=><div className="nav-group" key={group}>
    <span className="eyebrow">{group}</span>{screens.filter(s=>s[2]===group).map(([path,title])=><a key={path} href={'/'+path} aria-current={current.screen===path?'page':undefined} onClick={e=>{e.preventDefault();navigate('/'+path);}}><span className="nav-mark"/>{title}</a>)}
   </div>)}</nav>
   <div className="sidebar-footer">Scoped control plane<br/><small>No direct browser mutations</small></div>
  </aside>
  <div className="workspace">
   <header className="topbar">
    <button className="mobile-menu" aria-label="Toggle navigation" aria-expanded={mobile} onClick={()=>setMobile(!mobile)}>Menu</button>
    <div className="scope"><strong>{String(envelope?.scope?.account_id||safety?.scope?.account_id||'Account not observed')}</strong><span className="badge risk">{String(envelope?.scope?.environment||safety?.scope?.environment||'Unknown environment')}</span><span>{String(envelope?.scope?.venue||safety?.scope?.venue||'Unknown venue')}</span></div>
    <div className="top-tools">
     <span role="status" className="connection"><span className={`dot ${connection==='live'?'good':'warn'}`}/>{connection}</span>
     <label className="sr-only" htmlFor="timezone">Display timezone</label><select id="timezone" value={timezone} onChange={e=>setTimezone(e.target.value)}><option value="UTC">UTC</option><option value={Intl.DateTimeFormat().resolvedOptions().timeZone}>Local timezone</option></select>
     <label className="sr-only" htmlFor="theme">Color theme</label><select id="theme" value={theme} onChange={e=>setTheme(e.target.value)}><option value="system">System theme</option><option value="light">Light</option><option value="dark">Dark</option></select>
     <details className="identity"><summary>{principal.display_name}</summary><div className="identity-menu">
      <Value value={{roles:principal.roles,session_id:principal.session_id}}/>
      <button onClick={async()=>{try{await api('/auth/logout',{});setPrincipal(null);setCsrf('');setEnvelope(null);setSafety(null);setCaps([]);eventCursor.current='';}catch(e){setError(e);}}}>Sign out</button>
     </div></details>
    </div>
   </header>
   <section className="safety-strip" aria-label="Independent account and safety states">
    {['latches','effective_config','clock','stream'].map(key=><details key={key}><summary>{human(key)} <span className="muted">independent evidence</span></summary><Value value={safetyData[key]??null}/></details>)}
    <details><summary>Account / risk evidence</summary><Value value={safetyData.account??safetyData.provider??null}/><Evidence value={safety?.evidence}/></details>
   </section>
   <main id="content" tabIndex={-1}>
    <div className="page-heading"><div><span className="eyebrow">{current.screen==='trading'?'CONTROLLED OPERATOR ENTRY':'SCOPED OBSERVATIONS'}</span><h1>{screenName}</h1><p className="muted">{resourceId?'Exact resource record and linked evidence':'Current persisted evidence. Unknown is never treated as zero or safe.'}</p></div>
     <div className="button-row">{resourceId&&<Link to={'/'+current.screen}>← Back to list</Link>}<button onClick={reload} disabled={loading}>{loading?'Reading…':'Refresh evidence'}</button></div>
    </div>
    {tabs[current.screen]&&!resourceId&&<div className="tabs" role="tablist" aria-label="Record views" onKeyDown={e=>{
     if(!['ArrowLeft','ArrowRight','Home','End'].includes(e.key))return;
     const choices=Array.from(e.currentTarget.querySelectorAll<HTMLButtonElement>('[role=tab]'));
     const index=choices.indexOf(document.activeElement as HTMLButtonElement);
     const next=e.key==='Home'?0:e.key==='End'?choices.length-1:(index+(e.key==='ArrowRight'?1:-1)+choices.length)%choices.length;
     e.preventDefault();choices[next]?.focus();choices[next]?.click();
    }}>{tabs[current.screen].map(t=><button key={t} role="tab" tabIndex={readSection===t?0:-1} aria-selected={readSection===t} onClick={()=>{
     if(isNested)navigate('/'+current.screen);
     setTab(t);setCursor('');setHistoryCursors([]);setFilters({});setEnvelope(null);
    }}>{labels[t]||human(t)}</button>)}</div>}
    <ErrorBox error={error}/>
    <LatchBanner value={safetyData.latches}/>
    {current.screen==='access'&&<div className="alert" data-testid="password-access-policy"><strong>Shared-password owner access</strong><p>The console has one active login identity. Review its scope and sessions below; use Sign out to revoke the current session. Financial actions requiring a distinct approver remain unavailable to this shared identity.</p></div>}
    {!resourceId&&current.screen!=='trading'&&<Filters key={readSection} section={readSection} value={filters} onApply={value=>{setFilters(value);setCursor('');setHistoryCursors([]);setEnvelope(null);}}/>}
    {loading&&!envelope?<div className="panel skeleton" role="status">Loading authorized records and evidence…</div>:envelope?<div role="tabpanel" aria-label={human(readSection)}>
     <Evidence value={envelope.evidence}/>
     {envelope.warnings?.length>0&&<div className="alert warning"><strong>Evidence / policy warnings</strong><Value value={envelope.warnings}/></div>}
     {Boolean(error)&&<p className="alert warning">Last known snapshot retained. Failed refresh does not advance observation time.</p>}
     {isObject(data)&&Array.isArray(data.items)?<section className="panel">
      <div className="section-heading"><h2>{resourceId?'Linked records':'Records'}</h2><span className="muted">{data.total===null||data.total===undefined?'Total not observed':`${data.total} records`}</span></div>
      {items.length?<DomainTable section={readSection} items={items} render={value=><Value value={value}/>}/>:<div className="empty"><h3>No records returned</h3><p>{Array.isArray(envelope.evidence)&&envelope.evidence.some(e=>isObject(e)&&String(e.id).startsWith(`snapshot:${readSection==='protection'?'positions':readSection}:`)&&e.complete===true&&e.read_status==='complete')?'Successful scoped provider observation returned no matching records.':'No matching records in this query; evidence completeness above determines whether the read was complete or unavailable.'}</p><p>Inspect source traces and archived histories without creating synthetic exposure.</p><Link to="/signals">Open source signal traces</Link> · <Link to="/audit">Open durable job history</Link></div>}
      <div className="pagination"><button disabled={!historyCursors.length||loading} onClick={()=>{setCursor(historyCursors[historyCursors.length-1]);setHistoryCursors(historyCursors.slice(0,-1));}}>Previous page</button><span>Page {historyCursors.length+1}</span><button disabled={!data.has_more||!data.next_cursor||loading} onClick={()=>{setHistoryCursors([...historyCursors,cursor]);setCursor(String(data.next_cursor));}}>Next page</button></div>
     </section>:<DomainDetail section={current.screen==='trading'?'positions':readSection} data={data} render={value=><Value value={value}/>}/>}
     {readSection==='jobs'&&Boolean(resourceId)&&isObject(data)&&<JobControls job={data} onChanged={reload}/>}
     {Boolean(resourceId)&&readSection==='jobs'&&<RelatedRead path={`/jobs/${encodeURIComponent(resourceId)}/steps`} revision={isObject(data)?data.resource_version:refresh} title="Durable claims, checkpoints & readback"/>}
     {Boolean(resourceId)&&readSection==='services'&&<RelatedRead path={`/services/${encodeURIComponent(resourceId)}/logs`} revision={refresh} title="Bounded redacted service logs"/>}
    </div>:<div className="empty"><h2>No observation available</h2><p>Refresh to request the authorized read model. No synthetic data is displayed.</p></div>}
    {current.screen==='config'&&<SecretStage isOwner={principal.roles.includes('owner')} onChanged={reload}/>}
    {envelope&&<ActionPanel key={`${principal.id}/${String(envelope.scope.account_id)}/${String(envelope.scope.environment)}/${readSection}/${resourceId}`} principalId={principal.id} initialAction={current.screen==='trading'?'trade.open':''} capabilities={caps} scope={envelope.scope} section={readSection} items={resourceId&&isObject(data)?[data]:items} onChanged={reload}/>}
    <footer>Authoritative server policy controls every command · Provider and ledger evidence remain independent · No optimistic success</footer>
   </main>
  </div>
 </div>;
}
createRoot(document.getElementById('root')!).render(<React.StrictMode><App/></React.StrictMode>);
