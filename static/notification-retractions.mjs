export function removeRetracted(list, ids) {
  if(!Array.isArray(ids))return;
  const removed=new Set(ids.filter(id=>Number.isSafeInteger(id) && id>0).map(String));
  for(const row of list.querySelectorAll('[data-notification]'))if(removed.has(row.dataset.notification))row.remove();
  if(!list.querySelector('[data-notification]') && !list.querySelector('.empty-state')) {
    const empty=list.ownerDocument.createElement('li');empty.className='empty-state';empty.textContent='暂无消息';list.append(empty);
  }
}

export function installRetractionRefresh(doc,win,fetcher) {
  const list=doc.querySelector('[data-notifications]');if(!list)return;
  let timer,stopped=false,busy=false,delay=30000;
  const refresh=async()=>{
    win.clearTimeout(timer);
    if(doc.hidden || stopped || busy)return;
    const ids=[...list.querySelectorAll('[data-notification]')].map(row=>Number(row.dataset.notification)).filter(id=>Number.isSafeInteger(id) && id>0);
    if(!ids.length){timer=win.setTimeout(refresh,delay);return;}
    busy=true;
    try {
      const response=await fetcher(`/api/v1/notifications/unread/?since=${Math.min(...ids)}`,{credentials:'same-origin',cache:'no-store'});
      if(response.status===401){stopped=true;return;}
      if(!response.ok)throw Error('消息暂不可用');
      const data=await response.json();removeRetracted(list,data.retracted_ids);delay=30000;
      const badge=doc.querySelector('[data-unread-count]');if(badge){badge.textContent=String(data.unread_count);badge.hidden=!data.unread_count;}
    } catch {delay=Math.min(delay*2,120000);}
    finally {busy=false;if(!stopped && !doc.hidden)timer=win.setTimeout(refresh,delay);}
  };
  doc.addEventListener('visibilitychange',()=>{win.clearTimeout(timer);if(!doc.hidden)refresh();});
  doc.addEventListener('booklist:navigation-ready',refresh);
  win.addEventListener('pagehide',()=>{stopped=true;win.clearTimeout(timer);});
  win.addEventListener('pageshow',()=>{stopped=false;refresh();});
  refresh();
}
if(typeof document!=='undefined')installRetractionRefresh(document,window,fetch);
