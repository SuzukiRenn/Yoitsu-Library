// Measure rendered lines, so wrapping, explicit newlines and narrow screens agree.
export function installReasonFolds(doc=document, win=window) {
  const observed=new WeakSet();
  const reduced=win.matchMedia('(prefers-reduced-motion: reduce)');
  function measure(body) {
    const text=body.querySelector('.reason-text'), button=body.querySelector('[data-reason-expand]');
    const line=Number.parseFloat(win.getComputedStyle(text).lineHeight);
    if (!line || !body.getBoundingClientRect().width) return;
    const overflow=text.scrollHeight>line*2+1;
    body.classList.add('reason-fold-ready');
    button.hidden=!overflow;
    if(!overflow){body.classList.remove('reason-fold-expanded');button.setAttribute('aria-expanded','false');button.textContent='展开全文';}
  }
  const observer=win.ResizeObserver ? new win.ResizeObserver(entries=>{
    for(const entry of entries){
      const body=entry.target.closest('.reason-body');if(body?.isConnected)measure(body);
    }
  }) : null;
  function initialize(root=doc) {
    for(const body of root.querySelectorAll('.reason-body')) {
      if(!observed.has(body)) {
        observed.add(body);observer?.observe(body.querySelector('.reason-text'));
      }
      measure(body);
    }
  }
  doc.addEventListener('click',event=>{
    const button=event.target.closest('[data-reason-expand]');if(!button)return;
    const body=button.closest('.reason-body'), clip=body.querySelector('.reason-clip');
    const before=clip.getBoundingClientRect().height;
    const expanded=button.getAttribute('aria-expanded')!=='true';
    // Cancel an interrupted animation before measuring its new destination.
    clip.getAnimations?.().forEach(animation=>animation.cancel());
    body.classList.toggle('reason-fold-expanded',expanded);
    button.setAttribute('aria-expanded',String(expanded));
    button.textContent=expanded?'收起':'展开全文';
    const after=clip.getBoundingClientRect().height;
    if(!reduced.matches && typeof clip.animate==='function') {
      clip.animate([{height:`${before}px`},{height:`${after}px`}],
        {duration:220,easing:'cubic-bezier(.2,.7,.3,1)'});
    }
  });
  win.addEventListener('resize',()=>initialize());
  doc.fonts?.ready.then(()=>initialize());
  initialize();
  return initialize;
}
