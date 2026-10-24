import {installReasonFolds} from './reason-folds.mjs?v=20261020-1';
import {createPublicApi} from './public-api.mjs';
import {renderReasonButton, reasonRow, updateReasonContent} from './reason-ui.mjs?v=20261024-inline-spoiler-4';
import {updateInteraction} from './live-interactions.mjs?v=20261024-section-names-1';
export function startReasonLikes({doc=document, win=window, fetcher=fetch, write=createPublicApi()}={}) {
  const status=doc.querySelector('#action-status');
  const section=doc.querySelector('[data-reasons-work]');
  const list=doc.querySelector('#detail-reasons');
  const initializeFolds=section ? installReasonFolds(doc,win) : null;
  const busy=new Set();let timer,request,generation=0,stopped=false,delay=5000;
  const prefix=activity=>activity ? `/api/v1/activities/${activity}` : '/api/v1/public';
  const buttons=()=>[...doc.querySelectorAll('[data-reason-like]')];
  function update(item) {
    for (const button of buttons().filter(b=>b.dataset.reasonId===String(item.id))) renderReasonButton(button,item);
    const row=list?.querySelector(`[data-reason-row="${item.id}"]`);
    if(row){updateReasonContent(row,item);initializeFolds?.(list);}
  }
  function plan() {win.clearTimeout(timer);if(section&&!stopped&&!doc.hidden)timer=win.setTimeout(poll,delay);}
  async function poll() {
    if(!section)return;
    if(doc.hidden||stopped||busy.size){plan();return;}
    const epoch=generation;request=new AbortController();
    try {
      const ids=[...new Set([...list.querySelectorAll('[data-reason-like]')].map(b=>b.dataset.reasonId))];
      for(let offset=0;offset<ids.length;offset+=100) {
        const response=await fetcher(`${prefix(section.dataset.activity)}/works/${section.dataset.reasonsWork}/reasons/stats/?ids=${ids.slice(offset,offset+100).join(',')}`,{credentials:'same-origin',cache:'no-store',signal:request.signal});
        if(!response.ok){delay=response.status===429?60000:Math.min(delay*2,60000);return;}
        const payload=await response.json();if(epoch!==generation||doc.hidden||stopped)return;
        payload.results.forEach(update);
        const available=new Set(payload.results.map(item=>String(item.id)));
        for(const id of ids.slice(offset,offset+100))if(!available.has(id))list.querySelector(`[data-reason-row="${id}"]`)?.remove();
      }
      delay=5000;
    }catch(error){if(error.name!=='AbortError')delay=Math.min(delay*2,60000);}finally{plan();}
  }
  doc.addEventListener('click',async event=>{
    const button=event.target.closest('[data-reason-like]');
    if(!button||button.disabled)return;
    const id=button.dataset.reasonId;if(busy.has(id))return;
    event.preventDefault();busy.add(id);generation++;request?.abort();
    for(const b of buttons().filter(b=>b.dataset.reasonId===id)){b.dataset.busy='true';b.disabled=true;}
    status.textContent='';
    try {
      const result=await write(`${prefix(button.dataset.activity)}/works/${button.dataset.work}/reasons/${id}/like/`,{enabled:button.dataset.enabled==='true'});
      update(result.reason);
      for(const container of doc.querySelectorAll('.public-actions[data-work]'))if(container.dataset.work===button.dataset.work&& (container.dataset.activity||'')===(button.dataset.activity||''))updateInteraction(container,result.work);
      doc.dispatchEvent(new win.CustomEvent('booklist:reason-updated'));
    }catch(error){status.textContent=error.message;}
    finally {
      busy.delete(id);generation++;
      for(const b of buttons().filter(b=>b.dataset.reasonId===id)){delete b.dataset.busy;b.disabled=b.dataset.writable==='false';}
      plan();
    }
  });
  doc.addEventListener('click',async event=>{
    const more=event.target.closest('#more-reasons');if(!more||more.disabled)return;
    more.disabled=true;generation++;request?.abort();status.textContent='';
    try {
      const page=Number(more.dataset.page)+1;
      const response=await fetcher(`${prefix(section.dataset.activity)}/works/${section.dataset.reasonsWork}/reasons/?page=${page}&page_size=2`,{credentials:'same-origin',cache:'no-store'});
      const payload=await response.json();if(!response.ok)throw Error(payload.error?.message||'请求失败');
      for(const item of payload.results)if(!list.querySelector(`[data-reason-row="${item.id}"]`))list.append(reasonRow(doc,item,list.children.length+1,section.dataset.reasonsWork,section.dataset.activity));
      initializeFolds?.(list);
      section.querySelector('[data-reason-total]').textContent=`${payload.total} 条`;
      more.dataset.page=String(page);more.hidden=page*2>=payload.total;
    }catch(error){status.textContent=error.message;}finally{more.disabled=false;plan();}
  });
  doc.addEventListener('visibilitychange',()=>{generation++;request?.abort();win.clearTimeout(timer);if(!doc.hidden)poll();});
  win.addEventListener('pagehide',()=>{stopped=true;generation++;request?.abort();win.clearTimeout(timer);});
  win.addEventListener('pageshow',()=>{if(stopped){stopped=false;plan();}});
  plan();
}
