const STORE='booklist:page-positions:v1',PENDING='booklist:page-return:v1',TTL=86400000;
const page=/^\/(?:works\/[1-9]\d*\/(?:edit\/)?|activities\/(?:[1-9]\d*\/(?:recommend\/)?|history\/)?|recommend\/|catalog\/|feedback\/|notifications\/|account\/(?:settings\/|bind\/)?|privacy\/|guidelines\/|contact\/)?$/;
export function localReturn(value,origin,fallback='/') {
 try {
  if(typeof value!=='string' || value.length>8192)return fallback;
  if(/[\\\x00-\x1f\x7f]/.test(decodeURIComponent(value)))return fallback;
  const url=new URL(value,origin);
  return url.origin===origin && !url.username && !url.password && page.test(url.pathname) ? url.pathname+url.search+url.hash : fallback;
 } catch {return fallback;}
}
const key=(value,origin)=>{const u=new URL(value,origin);return u.pathname+u.search;};
export function returnTo(value,win=window) {
 const target=localReturn(value,win.location.origin);
 try{const pending={url:key(target,win.location.origin),at:Date.now()};win.sessionStorage.setItem(PENDING,JSON.stringify(pending));
  const shelves=JSON.parse(win.sessionStorage.getItem('booklist:shelf-positions:v1')||'null');
  if(shelves?.[pending.url])win.sessionStorage.setItem('booklist:shelf-return:v1',JSON.stringify(pending));}catch{}
 win.location.assign(target);
}
export function installNavigationContext(doc=document,win=window) {
 const load=name=>{try{return JSON.parse(win.sessionStorage.getItem(name)||'null');}catch{return null;}};
 const save=(name,value)=>{try{win.sessionStorage.setItem(name,JSON.stringify(value));}catch{}};
 const current=()=>win.location.pathname+win.location.search+win.location.hash;
 const header=doc.querySelector?.('.site-header');
 const headerEnd=Math.max(80,(header?.offsetTop||0)+(header?.offsetHeight||0));
 let reading=null,departing=null,readingTimer;
 const layoutTop=node=>{let top=0;for(let item=node;item;item=item.offsetParent)top+=item.offsetTop;return top;};
 function snapshot(){
  const shelf=doc.querySelector?.('[data-live-shelf]');
  const anchors=[...shelf?.querySelectorAll('[data-shelf-work]')||[]]
   .map(card=>({id:card.dataset.shelfWork,top:layoutTop(card)-win.scrollY}))
   .filter(anchor=>anchor.top>=-100 && anchor.top<win.innerHeight).slice(0,4);
  return {url:key(current(),win.location.origin),y:win.scrollY,anchors,at:Date.now()};
 }
 function remember(preferred=null){
  if(!localReturn(current(),win.location.origin,''))return;
  const row=preferred?.url===key(current(),win.location.origin)?preferred:snapshot();
  const previous=load(STORE),rows=previous && typeof previous==='object' && !Array.isArray(previous)?previous:{};
  rows[row.url]={y:row.y,anchors:row.anchors,at:Date.now()};
  save(STORE,Object.fromEntries(Object.entries(rows).filter(([,v])=>v && Date.now()-v.at<TTL).sort((a,b)=>b[1].at-a[1].at).slice(0,30)));
 }
 function trackReading(){
  win.clearTimeout(readingTimer);
  if(win.scrollY<=headerEnd)return;
  readingTimer=win.setTimeout(()=>{if(win.scrollY>headerEnd)reading=snapshot();},150);
 }
 function restore(event){
  if(doc.querySelector?.('[data-navigation-loading]'))return;
  const pending=load(PENDING),url=key(current(),win.location.origin);
  const explicit=pending?.url===url && Date.now()-pending.at<300000;
  const back=win.performance?.getEntriesByType?.('navigation')?.[0]?.type==='back_forward';
  if(!explicit && (!back || event?.persisted))return;
  if(explicit)save(PENDING,null);
  if(explicit && pending.context)save('booklist:shelf-return:v1',null);
  // Existing shelf-return retains its card-anchor restoration; generic scroll
  // applies only when it has no explicit shelf restoration to perform.
  const shelfPending=load('booklist:shelf-return:v1');
  if(doc.querySelector?.('[data-live-shelf]') && shelfPending?.url===url && Date.now()-shelfPending.at<15000)return;
  const snapshot=load(STORE)?.[url];
  if(!snapshot || !Number.isFinite(snapshot.y) || Date.now()-snapshot.at>TTL)return;
  doc.documentElement.setAttribute('data-shelf-position-restoring','');
  win.requestAnimationFrame(()=>{
   let y=snapshot.y;const shelf=doc.querySelector?.('[data-live-shelf]');
   for(const anchor of snapshot.anchors||[]){
    if(!/^[1-9]\d*$/.test(anchor.id) || !Number.isFinite(anchor.top))continue;
    const card=shelf?.querySelector(`[data-shelf-work="${anchor.id}"]`);
    if(card){y=layoutTop(card)-anchor.top;break;}
   }
   win.scrollTo({top:Math.max(0,Math.min(y,doc.documentElement.scrollHeight-win.innerHeight)),behavior:'instant'});
  });
 }
 doc.addEventListener('click',event=>{
  if(event.target.closest?.('[data-back-to-top]') && win.scrollY>headerEnd){reading=snapshot();return;}
  const link=event.target.closest?.('a[href]');if(!link || link.hasAttribute('download'))return;
  const url=new URL(link.href,win.location.href);if(url.origin!==win.location.origin)return;
  const source=localReturn(current(),win.location.origin);
  if(link.hasAttribute('data-context-login'))url.searchParams.set('next',source);
  else if(link.hasAttribute('data-context-notifications') && win.location.pathname!=='/notifications/')url.searchParams.set('from',source);
  else if(/^\/(?:activities\/[1-9]\d*\/)?recommend\/$/.test(url.pathname) && url.pathname!==win.location.pathname)url.searchParams.set('from',source);
  else if(link.hasAttribute('data-notification-work'))url.searchParams.set('from',source);
  link.href=url.href;
  const plain=event.button===0 && !event.ctrlKey && !event.metaKey && !event.altKey && !event.shiftKey && (!link.target || link.target==='_self');
  if(!plain)return;
  // Reaching the header must not replace the last reading viewport with zero.
  const messages=link.hasAttribute('data-context-notifications') || win.location.pathname==='/notifications/';
  departing=messages && win.scrollY<=headerEnd && reading?.url===key(current(),win.location.origin)?reading:null;
  remember(departing);
  if(link.hasAttribute('data-context-return') || link.hasAttribute('data-shelf-return'))save(PENDING,{url:key(url,win.location.origin),at:Date.now(),context:link.hasAttribute('data-context-return')});
 },true);
 doc.addEventListener('booklist:navigation-ready',()=>restore());
 win.addEventListener('scroll',trackReading,{passive:true});
 win.addEventListener('pagehide',()=>{win.clearTimeout(readingTimer);remember(departing);});
 win.addEventListener('pageshow',event=>{departing=null;restore(event);trackReading();});
 trackReading();
 if(doc.readyState==='complete')restore();
}
