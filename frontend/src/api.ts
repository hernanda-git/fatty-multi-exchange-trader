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
function decode(value:string):ArrayBuffer {
 const normalized=value.replace(/-/g,'+').replace(/_/g,'/');
 const padded=normalized+'='.repeat((4-normalized.length%4)%4);
 return Uint8Array.from(atob(padded),c=>c.charCodeAt(0)).buffer;
}
function encode(value:ArrayBuffer):string {let binary='';for(const byte of new Uint8Array(value))binary+=String.fromCharCode(byte);return btoa(binary).replace(/\+/g,'-').replace(/\//g,'_').replace(/=+$/,'');}
export async function passkey(kind:'authenticate'|'bootstrap'|'stepup',input:RecordData={}):Promise<RecordData>{
 if(!window.isSecureContext||!window.PublicKeyCredential||!navigator.credentials)throw new Error('Passkeys require a supported browser and a secure origin (HTTPS or localhost).');
 const start=await api<RecordData>(`/auth/${kind}/start`,input);
 const options=(start.publicKey||start.options||start) as RecordData;
 const publicKey={...options,challenge:decode(String(options.challenge))} as unknown as PublicKeyCredentialRequestOptions & PublicKeyCredentialCreationOptions;
 if(kind==='bootstrap'){
  const user=options.user as RecordData;publicKey.user={...user,id:decode(String(user.id))} as PublicKeyCredentialUserEntity;
  publicKey.excludeCredentials=((options.excludeCredentials||[]) as RecordData[]).map(c=>({...c,id:decode(String(c.id))})) as PublicKeyCredentialDescriptor[];
 }else publicKey.allowCredentials=((options.allowCredentials||[]) as RecordData[]).map(c=>({...c,id:decode(String(c.id))})) as PublicKeyCredentialDescriptor[];
 const credential=await (kind==='bootstrap'?navigator.credentials.create({publicKey}):navigator.credentials.get({publicKey})) as PublicKeyCredential|null;
 if(!credential)throw new Error('Passkey request was cancelled.');
 const response=credential.response;const serialized:RecordData={id:credential.id,rawId:encode(credential.rawId),type:credential.type,clientExtensionResults:credential.getClientExtensionResults(),response:{clientDataJSON:encode(response.clientDataJSON)}};
 if(response instanceof AuthenticatorAttestationResponse){serialized.response={...(serialized.response as RecordData),attestationObject:encode(response.attestationObject),transports:typeof response.getTransports==='function'?response.getTransports():[]};}else{const r=response as AuthenticatorAssertionResponse;serialized.response={...(serialized.response as RecordData),authenticatorData:encode(r.authenticatorData),signature:encode(r.signature),userHandle:r.userHandle?encode(r.userHandle):null};}
 const session=await api<RecordData>(`/auth/${kind}/finish`,{challenge_id:start.challenge_id,credential:serialized});
 const principal=session.principal as Principal|undefined;
 setCsrf(String(session.csrf_token||principal?.csrf_token||''));
 window.dispatchEvent(new CustomEvent('console-session',{detail:session}));
 return session;
}
