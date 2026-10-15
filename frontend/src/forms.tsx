import {useState,useEffect,useRef} from 'react';
import {api,passkey,type RecordData} from './api';
export const serverFilters:Record<string,string[]>={
 signals:['source','channel_id','message_id','state','intake_state','symbol','direction','revision','dispatch_state','terminal_reason','has_media','classification'],
 dispatches:['source_id','source','symbol','direction','revision','state','terminal_reason','exchange'],
 orders:['symbol','role','state','client_order_id','provider_order_id','dispatch_id','entry_leg','order_type','ownership'],
 fills:['symbol','client_order_id','provider_fill_id','intent_id','ownership','source','fee_ccy'],
 management:['symbol','state','action','source_message_id','revision'],jobs:['state','action','actor_id','resource_id','channel','approval_reference'],
 approvals:['job_id','actor_id','approver_id','state'],audit:['action','actor_id','job_id','resource_id','request_id','channel','outcome'],
 notifications:['state','event_kind','dedup_key','job_id','source_id','intent_id'],positions:['symbol','direction','ownership','protection_state','state'],
 protection:['symbol','direction','ownership','native_state','fallback_state','stream_state','missing_sl','missing_tp'],services:['state','service_id'],
 backups:['state','release_id','verified'],releases:['state','approved'],config:['kind','state'],'config/history':['kind','state'],'config/drafts':['kind','state'],
 'recovery/baselines':['approval_reference','record_kind','record_id'],'account/reservations':['symbol','state','client_order_id','dispatch_id'],
 'account/admission-snapshots':['margin_coin'],'account/pnl':['symbol','client_order_id','ownership','fee_ccy','source','intent_id'],
 'access/principals':['enabled','role'],'access/sessions':['principal_id','revoked'],account:['symbol','ownership']
};
export function Filters({section,value,onApply}:{section:string;value:Record<string,string>;onApply:(value:Record<string,string>)=>void}){
 const [draft,setDraft]=useState<Record<string,string>>(value);
 const apply=useRef(onApply);apply.current=onApply;
 const observed=useRef(JSON.stringify(value));
 useEffect(()=>{setDraft(value);observed.current=JSON.stringify(value);},[value]);
 useEffect(()=>{
  if(JSON.stringify(draft)===observed.current)return;
  const timer=setTimeout(()=>{observed.current=JSON.stringify(draft);apply.current(draft);},400);
  return()=>clearTimeout(timer);
 },[draft]);
 return <form className="filters" data-testid="server-filters" onSubmit={e=>{e.preventDefault();observed.current=JSON.stringify(draft);onApply(draft);}}>
  <label>Search<input type="search" value={draft.search||''} onChange={e=>setDraft({...draft,search:e.target.value})} placeholder="Search server records"/></label>
  {(serverFilters[section]||[]).map(field=><label key={field}>{field.replace(/_/g,' ')}<input value={draft[field]||''} onChange={e=>setDraft({...draft,[field]:e.target.value})}/></label>)}
  <label>From (UTC)<input type="datetime-local" value={draft.from?draft.from.replace(/Z$/,'').slice(0,16):''} onChange={e=>setDraft({...draft,from:e.target.value?new Date(e.target.value+'Z').toISOString():''})}/></label>
  <label>To (UTC)<input type="datetime-local" value={draft.to?draft.to.replace(/Z$/,'').slice(0,16):''} onChange={e=>setDraft({...draft,to:e.target.value?new Date(e.target.value+'Z').toISOString():''})}/></label>
  <label>Sort<select value={draft.sort||'newest'} onChange={e=>setDraft({...draft,sort:e.target.value})}><option value="newest">Newest first</option><option value="oldest">Oldest first</option></select></label>
  <button type="submit">Apply server filters</button><button type="button" onClick={()=>{setDraft({});observed.current='{}';onApply({});}}>Clear</button>
 </form>;
}
export function SecretStage({isOwner,onChanged}:{isOwner:boolean;onChanged:()=>void}){
 const [kind,setKind]=useState('BITGET_API_KEY'),[secret,setSecret]=useState(''),[result,setResult]=useState<RecordData|null>(null),[error,setError]=useState(''),[busy,setBusy]=useState(false);
 return <section className="panel"><span className="eyebrow">ENCRYPTED SECRET STAGING</span><h2>Stage a replacement credential</h2>
  <p>Staging does not apply or activate a credential. Use the returned opaque stage ID in the separately reviewed configuration job. Secret input is memory-only and cleared after staging.</p>
  {!isOwner?<p className="alert warning">Policy: only an owner with fresh passkey step-up may stage secrets.</p>:<form data-testid="secret-stage" onSubmit={async e=>{
   e.preventDefault();setBusy(true);setError('');setResult(null);
   try{await passkey('stepup');const r=await api<RecordData>('/secret-stages',{kind,value:secret});setSecret('');setResult(r);onChanged();}
   catch(e){setError(e instanceof Error?e.message:'Secret staging failed.');}
   finally{setBusy(false);}
  }}>
   <label>Credential kind<select value={kind} onChange={e=>{setKind(e.target.value);setSecret('');setResult(null);}}>{['BITGET_API_KEY','BITGET_API_SECRET','BITGET_API_PASSPHRASE','TG_BOT_TOKEN'].map(k=><option key={k}>{k}</option>)}</select></label>
   <label>Replacement secret<input type="password" autoComplete="off" required maxLength={4096} value={secret} onChange={e=>setSecret(e.target.value)}/></label>
   <div className="button-row"><button disabled={busy} type="submit">{busy?'Authorizing and staging…':'Verify passkey and stage secret'}</button><button type="button" onClick={()=>{setSecret('');setResult(null);setError('');}}>Clear input</button></div>
  </form>}
  {error&&<p className="alert danger" role="alert">{error}</p>}
  {result&&<div className="alert" role="status"><strong>Encrypted stage created; no configuration applied.</strong><dl className="record">{Object.entries(result).filter(([key])=>['stage_id','kind','expires_at'].includes(key)).map(([key,value])=><div key={key}><dt>{key}</dt><dd>{String(value)}</dd></div>)}</dl></div>}
 </section>;
}
