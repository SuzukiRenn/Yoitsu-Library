const STORE='booklist:work-sequences:v1',TTL=2*60*60*1000;
export function sequenceNeighbors(original,available,current){
 const position=original.indexOf(current),valid=new Set(available);
 if(position<0)return null;
 const previous=original.slice(0,position).reverse().find(id=>valid.has(id));
 const next=original.slice(position+1).find(id=>valid.has(id));
 const remaining=original.filter(id=>valid.has(id) || id===current);
 return {previous,next,position:remaining.indexOf(current)+1,total:remaining.length};
}
export function installWorkSequence(doc=document,win=window){
 const data=doc.querySelector('#work-sequence-data'),nav=doc.querySelector('[data-work-sequence]');
 if(!data || !nav)return;
 let info;try{info=JSON.parse(data.textContent);}catch{return;}
 if(!Array.isArray(info.ids) || !info.ids.every(Number.isSafeInteger))return;
 let sequences={};try{sequences=JSON.parse(win.sessionStorage.getItem(STORE)||'{}') || {};}catch{}
 if(typeof sequences!=='object' || Array.isArray(sequences))sequences={};
 const prior=sequences[info.id],now=Date.now();
 const usable=prior && prior.source===info.source && prior.activity===info.activity && now-prior.at<TTL && Array.isArray(prior.ids) && prior.ids.every(Number.isSafeInteger) && prior.ids.includes(info.current);
 const ids=usable?prior.ids:info.ids;
 const result=sequenceNeighbors(ids,info.ids,info.current);
 if(!result){nav.hidden=true;return;}
 sequences[info.id]={ids,source:info.source,activity:info.activity,at:usable?prior.at:now};
 try{win.sessionStorage.setItem(STORE,JSON.stringify(Object.fromEntries(Object.entries(sequences).filter(([,value])=>now-value.at<TTL).sort((a,b)=>b[1].at-a[1].at).slice(0,5))));}catch{}
 function link(selector,id){
  const anchor=nav.querySelector(selector);
  if(id===undefined){anchor.removeAttribute('href');anchor.setAttribute('aria-disabled','true');return;}
  const url=new URL(`/works/${id}/`,win.location.origin);
  if(info.activity)url.searchParams.set('activity',info.activity);
  url.searchParams.set('from',info.source);url.searchParams.set('browse',info.id);
  anchor.setAttribute('href',url.pathname+url.search);anchor.removeAttribute('aria-disabled');
 }
 link('[data-work-previous]',result.previous);link('[data-work-next]',result.next);
 nav.querySelector('[data-work-position]').textContent=`${result.position} / ${result.total}`;
 nav.hidden=false;
 // Keep this sequence on reload without adding a browser history entry.
 const currentURL=new URL(win.location.href);
 if(!currentURL.searchParams.has('browse')){currentURL.searchParams.set('browse',info.id);win.history.replaceState(win.history.state,'',currentURL.href);}
}
