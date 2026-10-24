const button=document.querySelector('[data-back-to-top]');
if(button){
  const controls=[button,document.querySelector('[data-random-book]'),document.querySelector('[data-shelf-view-toggle]'),document.querySelector('[data-appearance-toggle]')].filter(Boolean);
  const header=document.querySelector('.site-header');
  const reduced=window.matchMedia('(prefers-reduced-motion: reduce)');
  let frame;
  const update=()=>{
    frame=undefined;
    const visible=window.scrollY>Math.max(320,window.innerHeight*.6);
    if(!visible && document.activeElement===button)document.querySelector('.brand')?.focus({preventScroll:true});
    button.toggleAttribute('data-visible',visible);
    button.inert=!visible;
    button.setAttribute('aria-hidden',String(!visible));
    // All pages share the header edge, even when their article widths differ.
    for(const control of controls){
      if(window.innerWidth<=700 || !header){
        control.style.removeProperty('left');control.style.removeProperty('right');
      }else{
        const width=control.getBoundingClientRect().width;
        const edge=header.getBoundingClientRect().right;
        const left=Math.max(12,Math.min(edge+12,document.documentElement.clientWidth-width-12));
        control.style.left=`${left}px`;control.style.right='auto';
      }
    }


  };
  const schedule=()=>{if(frame===undefined)frame=window.requestAnimationFrame(update);};
  document.addEventListener('booklist:floating-controls',schedule);
  window.addEventListener('scroll',schedule,{passive:true});
  window.addEventListener('resize',schedule);
  window.addEventListener('pageshow',update);
  window.addEventListener('pagehide',()=>{window.cancelAnimationFrame(frame);frame=undefined;});
  button.addEventListener('click',event=>{
    if(event.detail===0)document.querySelector('.brand')?.focus({preventScroll:true});
    window.scrollTo({top:0,behavior:reduced.matches?'instant':'smooth'});
  });
  button.setAttribute('data-reveal-ready','');
  button.hidden=false;
  update();
}
