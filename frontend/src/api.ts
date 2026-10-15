export type RecordData = Record<string, unknown>;
export type Principal = {id:string;display_name:string;roles:string[];csrf_token:string;step_up_at:string|null;session_id?:string};
export type Envelope = {data:unknown;evidence:unknown;warnings:unknown[];scope:RecordData;event_cursor:string|number;server_time?:string};
export type Capability = {action:string;description?:string;arguments_schema?:RecordData;required_role?:string;danger_tier:number|string;preview_allowed:boolean;enqueue_allowed:boolean;reasons:unknown[];gates:unknown[];target_kinds?:string[];policy_version?:string};
export class ApiError extends Error { constructor(public status:number,public detail:RecordData){super(String(detail.message || detail.code || `HTTP ${status}`));} }
let csrf='';
export function setCsrf(value:string){csrf=value;}
export async function api<T=RecordData>(path:string,body?:unknown,headers:Record<string,string>={},signal?:AbortSignal):Promise<T>{
 const response=await fetch(`/api/v1${path}`,{credentials:'same-origin',method:body===undefined?'GET':'POST',headers:{Accept:'application/json',...(body===undefined?{}:{'Content-Type':'application/json','X-CSRF-Token':csrf}),...headers},body:body===undefined?undefined:JSON.stringify(body),signal});
 const data=await response.json().catch(()=>({code:'invalid_response',message:'Server returned an unreadable response.'}));
 if(!response.ok){
  const detail=data&&typeof data==='object'?data:{code:'invalid_response'};
  if(response.status===401&&['authentication_required','session_expired','session_revoked'].includes(String(detail.code))){
   const wasAuthenticated=csrf!=='';setCsrf('');if(wasAuthenticated)window.dispatchEvent(new Event('console-session-expired'));
  }
  throw new ApiError(response.status,detail);
 }
 return data as T;
}
export async function passwordLogin(password:string):Promise<RecordData>{
 const session=await api<RecordData>('/auth/login',{password});
 const principal=session.principal as Principal|undefined;
 setCsrf(String(session.csrf_token||principal?.csrf_token||''));
 window.dispatchEvent(new CustomEvent('console-session',{detail:session}));
 return session;
}
export async function authorize(input:{preview_digest?:string;job_id?:string}):Promise<RecordData>{
 const session=await api<RecordData>('/auth/consent',input);
 const principal=session.principal as Principal|undefined;
 setCsrf(String(session.csrf_token||principal?.csrf_token||''));
 window.dispatchEvent(new CustomEvent('console-session',{detail:session}));
 return session;
}
