import {accountPost} from './account.mjs?v=20261103-retract-1';
import {returnTo} from './navigation-context.mjs?v=20261103-retract-1';

export function installRecentViews(doc=document,win=window,post=accountPost){
 const owner=Number(doc.body?.dataset.readerId);if(!Number.isSafeInteger(owner)||owner<=0)return;
 const detail=doc.querySelector('[data-recent-work]');let recorded=false;
 async function record(){
  if(!detail||recorded||doc.hidden)return;
  const work=Number(detail.dataset.recentWork),activity=detail.dataset.recentActivity?Number(detail.dataset.recentActivity):null;
  if(!Number.isSafeInteger(work)||work<=0||(activity!==null&&(!Number.isSafeInteger(activity)||activity<=0)))return;
  recorded=true;
  try{await post('/api/v1/account/recent/visit/',{account_id:owner,work_id:work,activity_id:activity});}
  catch{recorded=false;} // Browsing remains usable if optional history cannot be saved.
 }
 record();doc.addEventListener('visibilitychange',record);
 win.addEventListener('pageshow',event=>{if(event.persisted){recorded=false;record();}});
 const status=doc.querySelector('#action-status');
 doc.addEventListener('click',async event=>{
  const button=event.target.closest?.('[data-recent-remove],[data-recent-clear]');
  if(!button||button.disabled)return;
  const payload={account_id:owner,action:button.hasAttribute('data-recent-clear')?'clear':'remove'};
  if(payload.action==='remove')payload.work_id=Number(button.dataset.recentRemove);
  button.disabled=true;if(status)status.textContent='';
  try{await post('/api/v1/account/recent/manage/',payload);
   if(payload.action==='clear')win.location.assign('/account/?tab=recent');
   else returnTo(win.location.pathname+win.location.search,win);
  }catch(error){if(status)status.textContent=error.message;button.disabled=false;}
 });
 const toggle=doc.querySelector('[data-recent-tracking]');
 toggle?.addEventListener('change',async()=>{
  const enabled=toggle.checked;toggle.disabled=true;if(status)status.textContent='';
  try{await post('/api/v1/account/recent/manage/',{account_id:owner,action:'tracking',enabled});}
  catch(error){toggle.checked=!enabled;if(status)status.textContent=error.message;}
  finally{toggle.disabled=false;}
 });
}
if(typeof document!=='undefined')installRecentViews();
