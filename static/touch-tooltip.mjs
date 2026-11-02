// Touch focus can persist until the next tap. Time-limit its visual tooltip
// without blurring the button or changing keyboard and mouse behavior.
export function installTouchTooltip(doc=document,win=window){
 const button=doc.querySelector('[data-shelf-view-toggle]');
 if(!button)return;
 let timer;
 const reset=()=>{
  win.clearTimeout(timer);timer=undefined;
  button.removeAttribute('data-touch-tooltip');
 };
 button.addEventListener('pointerup',event=>{
  if(!['touch','pen'].includes(event.pointerType))return;
  reset();button.removeAttribute('data-tooltip-dismissed');
  button.setAttribute('data-touch-tooltip','visible');
  timer=win.setTimeout(()=>{
   button.setAttribute('data-touch-tooltip','hidden');timer=undefined;
  },1500);
 });
 for(const type of ['pointerenter','pointerdown'])button.addEventListener(type,event=>{
  if(event.pointerType==='mouse')reset();
 });
 button.addEventListener('keydown',reset);
 button.addEventListener('click',event=>{if(event.detail===0)reset();});
 button.addEventListener('pointercancel',reset);
 button.addEventListener('focusout',reset);
 win.addEventListener('pagehide',reset);
}
