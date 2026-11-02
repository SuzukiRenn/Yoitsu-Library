const GUEST_KEY='identity_booklist.activity-notice.guest.v1';
const PERIOD=60000;
export function validState(value) {
  const ids=value?.activity_ids, unseen=value?.unseen_ids;
  return Array.isArray(ids) && ids.length<=100 && ids.every(id=>Number.isSafeInteger(id)&&id>0)
    && Array.isArray(unseen) && unseen.every(id=>ids.includes(id))
    && /^(guest|[1-9][0-9]*)$/.test(value.account_key)
    && (value.account_key==='guest' || Number.isSafeInteger(Number(value.account_key)));
}
export function guestSeen(win) {
  const all=new Set();
  for(const name of ['localStorage','sessionStorage'])try {
    const value=JSON.parse(win[name].getItem(GUEST_KEY)||'[]');
    if(Array.isArray(value))for(const id of value.slice(-500))if(Number.isSafeInteger(id)&&id>0)all.add(id);
  }catch{}
  return all;
}
export function rememberGuest(win,ids) {
  const value=JSON.stringify([...new Set([...guestSeen(win),...ids])].slice(-500));
  for(const name of ['localStorage','sessionStorage'])try{win[name].setItem(GUEST_KEY,value);}catch{}
}
export function installActivityNotice(doc=document,win=window) {
  const hint=doc.querySelector('[data-activity-notice]'), entry=doc.querySelector('[data-activity-entry]');
  const seed=doc.querySelector('#activity-notice-state');
  if(!hint || !entry || !seed || !win.fetch)return;
  let initial;try{initial=JSON.parse(seed.textContent);}catch{return;}
  if(!validState(initial))return;
  const key=initial.account_key, viewing=entry.dataset.activitySection==='true';
  let current=initial, acknowledged=false, busy=false, timer, stopped=false;
  const position=()=>{
    if(hint.hidden || stopped)return;
    const box=hint.getBoundingClientRect(), target=entry.getBoundingClientRect();
    const center=target.left+target.width/2-box.left;
    hint.style.setProperty('--activity-notice-pointer',`${Math.max(12,Math.min(center,box.width-12))}px`);
  };
  win.addEventListener('resize',position);
  const render=()=>{
    const seen=key==='guest'?guestSeen(win):new Set();
    hint.hidden=doc.hidden || !current.unseen_ids.some(id=>!seen.has(id)
      && !(viewing && !acknowledged && initial.activity_ids.includes(id)));
    position();
  };
  const accept=value=>{
    if(!validState(value))throw Error('Invalid notice state');
    if(value.account_key!==key){stopped=true;hint.hidden=true;win.clearTimeout(timer);return false;}
    current=value;render();return true;
  };
  const get=async(url,options={})=>{
    const response=await win.fetch(url,{credentials:'same-origin',cache:'no-store',...options});
    if(!response.ok)throw Error('Notice unavailable');
    return response.json();
  };
  const schedule=()=>{win.clearTimeout(timer);if(!stopped && !doc.hidden)timer=win.setTimeout(refresh,PERIOD);};
  const refresh=async()=>{
    if(stopped || busy || doc.hidden)return;
    busy=true;
    try{
      if(!accept(await get('/api/v1/activities/notices/')) || stopped)return;
      if(viewing && !acknowledged){
        // Only acknowledge the publication snapshot rendered on this successful page.
        const ids=initial.activity_ids.filter(id=>current.activity_ids.includes(id));
        if(key==='guest') {rememberGuest(win,ids);acknowledged=true;render();}
        else if(ids.length){
          const {csrf_token}=await get('/api/v1/csrf/');
          if(stopped)return;
          const value=await get('/api/v1/activities/notices/seen/',{method:'POST',
            headers:{'Content-Type':'application/json','X-CSRFToken':csrf_token},
            body:JSON.stringify({ids,account_id:Number(key)})});
          if(stopped)return;
          if(accept(value))acknowledged=true;
        }else acknowledged=true;
      }
    }catch{/* This optional cue never interrupts browsing or marks a failed request as seen. */}
    finally{busy=false;schedule();}
  };
  // Entering the section hides its current cue; no click handler marks a failed navigation as seen.
  if(viewing && key==='guest'){rememberGuest(win,initial.activity_ids);acknowledged=true;}
  render();if(viewing)hint.hidden=true;
  refresh();
  doc.addEventListener('visibilitychange',()=>{win.clearTimeout(timer);if(!doc.hidden)refresh();else hint.hidden=true;});
  win.addEventListener('pagehide',()=>{stopped=true;win.clearTimeout(timer);});
  win.addEventListener('pageshow',event=>{if(event.persisted){hint.hidden=true;stopped=false;refresh();}});
  win.addEventListener('storage',event=>{if(key==='guest' && event.key===GUEST_KEY)render();});
  return {refresh,stop(){stopped=true;win.clearTimeout(timer);}};
}
if(typeof document!=='undefined')installActivityNotice();
