// FLIP retained cards; fade removed snapshots without delaying the server state.
export function createShelfMotion({doc=document,win=window}={}) {
  const reduced=win.matchMedia('(prefers-reduced-motion: reduce)');
  const animations=new Set(), ghosts=new Set();
  const visible=r=>r.bottom>0 && r.top<win.innerHeight;
  function clear(){
    for(const animation of animations)animation.cancel();
    animations.clear();
    for(const ghost of ghosts)ghost.remove();
    ghosts.clear();
  }
  function play(node,frames,options,cleanup=()=>{}){
    if(typeof node.animate!=='function'){cleanup();return;}
    let animation;
    try{animation=node.animate(frames,options);}catch{cleanup();return;}
    animations.add(animation);
    const done=()=>{animations.delete(animation);cleanup();};
    animation.finished.then(done,done);
  }
  function capture(grid){
    // Capture visual positions before cancelling an in-flight move.
    const before=new Map([...grid.querySelectorAll('[data-shelf-work]')].map(card=>
      [card.dataset.shelfWork,{card,rect:card.getBoundingClientRect()}]));
    clear();
    if(reduced.matches)return ()=>{};
    return ()=>{
      const after=new Map([...grid.querySelectorAll('[data-shelf-work]')].map(card=>[card.dataset.shelfWork,card]));
      for(const [id,{card,rect:from}] of before){
        const retained=after.get(id);
        if(retained){
          const to=retained.getBoundingClientRect();
          if(!visible(from) && !visible(to))continue;
          const x=from.left-to.left,y=from.top-to.top;
          if(x || y)play(retained,[{transform:`translate(${x}px,${y}px)`},{transform:'translate(0,0)'}],
            {duration:360,easing:'cubic-bezier(.2,.7,.3,1)'});
        }else if(visible(from) && doc.body){
          const ghost=card.cloneNode(true);
          ghost.removeAttribute('id');ghost.removeAttribute('data-shelf-work');
          for(const node of ghost.querySelectorAll('[data-work]'))node.removeAttribute('data-work');
          for(const node of ghost.querySelectorAll('[id]'))node.removeAttribute('id');
          ghost.setAttribute('aria-hidden','true');ghost.inert=true;
          ghost.classList.remove('work-opening','shelf-card-enter');
          Object.assign(ghost.style,{position:'fixed',left:`${from.left}px`,top:`${from.top}px`,
            width:`${from.width}px`,height:`${from.height}px`,margin:'0',pointerEvents:'none',zIndex:'4',
            animation:'none',transition:'none',transform:'none',viewTransitionName:'none'});
          ghosts.add(ghost);doc.body.append(ghost);
          play(ghost,[{opacity:1,transform:'scale(1)'},{opacity:0,transform:'scale(.96)'}],
            {duration:180,easing:'ease-out'},()=>{ghosts.delete(ghost);ghost.remove();});
        }
      }
    };
  }
  win.addEventListener('pagehide',clear);
  reduced.addEventListener('change',()=>{if(reduced.matches)clear();});
  return {capture};
}
