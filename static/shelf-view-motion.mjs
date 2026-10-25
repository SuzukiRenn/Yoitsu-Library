import {motionPreference} from './motion-preference.mjs?v=20261025-1';
// Match the same book across layouts; keep the live DOM, focus and scroll anchor.
export function createShelfViewMotion({doc=document,win=window}={}){
 const root=doc.documentElement,reduced=motionPreference(win,doc);
 let active;
 function finish(run){
  if(active!==run)return;
  active=null;
  for(const restore of run.restore)restore();
  delete root.dataset.shelfViewChanging;
 }
 function cancel(){
  const run=active;if(!run)return;
  // A skipped native transition still invokes its callback later. Apply it once now.
  run.update();run.transition?.skipTransition();
  for(const animation of run.animations)animation.cancel();
  finish(run);
 }
 function fallback(run,before){
  run.update();
  // Read every new rectangle before starting animations (writes flush style).
  const after=before.map(item=>({...item,to:item.card.getBoundingClientRect()}));
  for(const {card,rect:from,to} of after){
   if(typeof card.animate!=='function' || card.isConnected===false)continue;
   if((from.bottom<=0 || from.top>=win.innerHeight) && (to.bottom<=0 || to.top>=win.innerHeight))continue;
   if(!from.width || !from.height || !to.width || !to.height)continue;
   const x=from.left-to.left,y=from.top-to.top,sx=from.width/to.width,sy=from.height/to.height;
   try{
    const animation=card.animate([
     {transformOrigin:'top left',transform:`translate(${x}px,${y}px) scale(${sx},${sy})`,opacity:.55},
     {transformOrigin:'top left',transform:'translate(0,0) scale(1,1)',opacity:1}
    ],{duration:340,easing:'cubic-bezier(.22,.75,.25,1)'});
    run.animations.push(animation);
   }catch{}
  }
  Promise.allSettled(run.animations.map(animation=>animation.finished)).then(()=>finish(run));
 }
 function change(shelf,update){
  cancel();
  if(win.CustomEvent)doc.dispatchEvent?.(new win.CustomEvent('booklist:shelf-view-change'));
  if(reduced?.matches){update();return;}
  const cards=[...shelf.querySelectorAll('[data-shelf-work]')];
  if(!cards.length){update();return;}
  const before=cards.map(card=>({card,rect:card.getBoundingClientRect()}));
  const run={restore:[],animations:[],applied:false,update(){if(!run.applied){run.applied=true;update();}}};
  active=run;root.dataset.shelfViewChanging='';
  if(typeof doc.startViewTransition!=='function'){fallback(run,before);return;}
  function name(node,value,group){
   if(!node?.style)return;
   const previous=node.style.viewTransitionName,previousClass=node.style.viewTransitionClass;
   node.style.viewTransitionName=value;node.style.viewTransitionClass=group;
   run.restore.push(()=>{node.style.viewTransitionName=previous;node.style.viewTransitionClass=previousClass;});
  }
  // Offscreen cards need no raster snapshots. Keep the live layout for every card.
  before.filter(({rect})=>rect.bottom>0 && rect.top<win.innerHeight).forEach(({card},index)=>{
   name(card,`shelf-layout-card-${index}`,'shelf-layout-card');
   name(card.querySelector('.book-cover'),`shelf-layout-cover-${index}`,'shelf-layout-cover');
   name(card.querySelector('.book-title'),`shelf-layout-title-${index}`,'shelf-layout-title');
  });
  try{
   const transition=doc.startViewTransition(()=>run.update());run.transition=transition;
   const ready=transition.ready.then(()=>true,()=>{
    if(active!==run)return false;
    // Unsupported snapshots must still commit the preference and show a fallback.
    run.update();transition.skipTransition();
    for(const restore of run.restore)restore();run.restore=[];
    fallback(run,before);return false;
   });
   const settled=()=>{ready.then(ok=>{if(ok)finish(run);});};
   transition.finished.then(settled,settled);
  }catch{
   for(const restore of run.restore)restore();run.restore=[];fallback(run,before);
  }
 }
 win.addEventListener('pagehide',cancel);
 win.addEventListener('resize',cancel);
 reduced?.addEventListener?.('change',()=>{if(reduced.matches)cancel();});
 doc.addEventListener?.('booklist:shelf-reorder',cancel);
 doc.addEventListener?.('booklist:shelf-navigation',event=>{if(event.detail?.pending)cancel();});
 return {change,cancel};
}
