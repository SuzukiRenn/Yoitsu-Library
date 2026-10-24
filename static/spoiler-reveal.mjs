export function installSpoilerReveal(doc=document) {
  doc.addEventListener('click',event=>{
    const button=event.target.closest('[data-spoiler-reveal]');
    if(!button)return;
    const wrapper=button.closest('.spoiler-preview, .spoiler-reason');
    const text=wrapper?.querySelector('[data-spoiler-text]');
    const control=button.closest('.spoiler-control');
    if(!text || !control)return;
    event.preventDefault();
    control.hidden=true;
    text.hidden=false;
    // Keep keyboard focus meaningful when its original button disappears.
    text.focus({preventScroll:true});
  });
}
