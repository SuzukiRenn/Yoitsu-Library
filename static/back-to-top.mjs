const button=document.querySelector('[data-back-to-top]');
if(button){
  const reduced=window.matchMedia('(prefers-reduced-motion: reduce)');
  const content=document.querySelector('[data-shelf-grid]') || document.querySelector('.work-detail') || document.querySelector('main');
  let frame;
  const update=()=>{
    frame=undefined;
    button.hidden=window.scrollY<=Math.max(320,window.innerHeight*.6);
    if(window.innerWidth<=700){
      button.style.removeProperty('left');
      button.style.removeProperty('right');
    }else if(!button.hidden && content){
      const edge=content.getBoundingClientRect().right;
      const limit=document.documentElement.clientWidth-button.getBoundingClientRect().width-12;
      button.style.left=`${Math.min(edge+12,limit)}px`;
      button.style.right='auto';
    }
  };
  const schedule=()=>{if(frame===undefined)frame=window.requestAnimationFrame(update);};
  window.addEventListener('scroll',schedule,{passive:true});
  window.addEventListener('resize',schedule);
  window.addEventListener('pageshow',update);
  window.addEventListener('pagehide',()=>{window.cancelAnimationFrame(frame);frame=undefined;});
  button.addEventListener('click',event=>{
    if(event.detail===0)document.querySelector('.brand')?.focus({preventScroll:true});
    window.scrollTo({top:0,behavior:reduced.matches?'instant':'smooth'});
  });
  update();
}
