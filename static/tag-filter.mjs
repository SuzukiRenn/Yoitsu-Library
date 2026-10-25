import {motionPreference} from './motion-preference.mjs?v=20261025-1';
// Native details keeps tag filtering usable without JavaScript; the panel is out of flow.
const filters=new WeakMap();
const TAG_BATCH_SIZE=12;
export function initializeTagFilters(scope=document){
 for(const form of scope.querySelectorAll('[data-tag-filter]')){
  if(filters.has(form))continue;
  const dropdown=form.querySelector('[data-tag-dropdown]');
  const list=form.querySelector('#filter-tags'),search=form.querySelector('#tag-search');
  const count=form.querySelector('[data-tag-selection-count]'),empty=form.querySelector('.tag-filter-empty');
  const more=form.querySelector('[data-tag-load-more]');
  let visibleLimit=TAG_BATCH_SIZE;
  const picked=form.querySelector('[data-picked-tags]'),pickedSection=form.querySelector('[data-picked-section]');
  const win=form.ownerDocument.defaultView,reduced=motionPreference(win,form.ownerDocument);
  const animations=new Set();
  let labels=[...list.querySelectorAll('[data-tag-name]')];
  const key=value=>value.normalize('NFKC').toLocaleLowerCase().trim();
  function fitPanel(){
   if(!dropdown.open)return;
   const panel=form.querySelector('.tag-dropdown-panel');
   if(win.innerWidth>700){panel.style.maxHeight='';return;}
   const viewport=win.visualViewport;
   const bottom=viewport ? viewport.offsetTop+viewport.height : win.innerHeight;
   const available=Math.max(80,Math.min(460,bottom-panel.getBoundingClientRect().top-12));
   panel.style.maxHeight=`${Math.floor(available)}px`;
  }
  win.addEventListener('resize',fitPanel);
  win.addEventListener('scroll',fitPanel,{passive:true});
  win.visualViewport?.addEventListener('resize',fitPanel);
  win.visualViewport?.addEventListener('scroll',fitPanel);
  function clearMotion(){for(const animation of animations)animation.cancel();animations.clear();}
  function render(){
   const focused=form.ownerDocument.activeElement;
   const query=key(search.value);let selected=0,visible=0,matching=0;
   for(const destination of [picked,list]){
    const desired=labels.filter(label=>label.querySelector('input').checked===(destination===picked));
    desired.forEach((label,index)=>{
     if(destination.children[index]!==label)destination.insertBefore(label,destination.children[index] || null);
     const checked=label.querySelector('input').checked;
     const matches=!query || key(label.dataset.tagName).includes(query);
     if(!checked && matches)matching++;
     label.hidden=!checked && (!matches || matching>visibleLimit);
     if(checked)selected++;else if(!label.hidden)visible++;
    });
   }
   pickedSection.hidden=!selected;
   if(more)more.hidden=matching<=visibleLimit;
   const total=selected+[...form.querySelectorAll('[data-personal-filter]')].filter(input=>input.checked).length;
   count.textContent=String(total);count.hidden=!total;empty.hidden=!!visible;
   if(form.contains(focused) && focused.isConnected && form.ownerDocument.activeElement!==focused)focused.focus({preventScroll:true});
  }
  function update(){
   // Move real labels/inputs; serialize every checked tag exactly once.
   const before=new Map(labels.filter(label=>label.getClientRects().length).map(label=>[label,label.getBoundingClientRect()]));
   clearMotion();render();
   if(reduced.matches || !dropdown.open)return;
   for(const label of labels){
    if(!label.getClientRects().length || typeof label.animate!=='function')continue;
    const from=before.get(label),to=label.getBoundingClientRect();
    if(!from)continue;
    const x=from.left-to.left,y=from.top-to.top;
    if(!x && !y)continue;
    const animation=label.animate([{transform:`translate(${x}px,${y}px)`},{transform:'translate(0,0)'}],{duration:260,easing:'cubic-bezier(.22,.7,.25,1)'});
    animations.add(animation);const done=()=>animations.delete(animation);animation.finished.then(done,done);
   }
  }
  search.addEventListener('input',()=>{visibleLimit=TAG_BATCH_SIZE;update();});
  more?.addEventListener('click',()=>{
   const restoreFocus=form.ownerDocument.activeElement===more;
   visibleLimit+=TAG_BATCH_SIZE;update();
   // The final batch hides the control; keep keyboard focus inside the newly revealed list.
   if(more.hidden && restoreFocus){
    [...list.querySelectorAll('[data-tag-name]')].filter(label=>!label.hidden).at(-1)?.querySelector('input').focus({preventScroll:true});
   }
   fitPanel();
  });
  form.addEventListener('change',update);
  form.querySelector('[data-clear-tag-selection]').addEventListener('click',()=>{
   labels.forEach(label=>{label.querySelector('input').checked=false;});
   for(const input of form.querySelectorAll('[data-tag-mode]'))input.checked=input.value==='all';
   update();
   form.requestSubmit();
  });
  form.querySelector('[data-clear-filter-selection]')?.addEventListener('click',()=>{
   for(const input of form.querySelectorAll('input[type=checkbox]'))input.checked=false;
   for(const input of form.querySelectorAll('[data-tag-mode]'))input.checked=input.value==='all';
   update();form.requestSubmit();
  });
  win.addEventListener('pagehide',clearMotion);
  reduced.addEventListener('change',()=>{if(reduced.matches)clearMotion();});
  dropdown.addEventListener('toggle',()=>{if(!dropdown.open)clearMotion();else fitPanel();});
  form.addEventListener('keydown',event=>{
   if(event.key==='Escape' && !event.isComposing && event.keyCode!==229 && !event.defaultPrevented && dropdown.open){event.preventDefault();dropdown.open=false;dropdown.querySelector('summary').focus();}
  });
  form.ownerDocument.addEventListener('pointerdown',event=>{if(dropdown.open && !dropdown.contains(event.target))dropdown.open=false;});
  filters.set(form,(source,{syncSelection=false}={})=>{
   const focused=form.ownerDocument.activeElement;
   clearMotion();
   const existing=new Map(labels.map(label=>[label.querySelector('input').value,label]));
   const next=[];
   for(const incoming of source.querySelectorAll('[data-tag-name]')){
    const id=incoming.querySelector('input').value;
    const label=existing.get(id) || form.ownerDocument.importNode(incoming,true);
    label.dataset.tagName=incoming.dataset.tagName;
    label.querySelector('.tag-count').textContent=incoming.querySelector('.tag-count').textContent;
    if(syncSelection)label.querySelector('input').checked=incoming.querySelector('input').checked;
    list.append(label);next.push(label);existing.delete(id);
   }
   for(const label of existing.values()){
    if(!syncSelection && label.querySelector('input').checked){label.querySelector('.tag-count').textContent='0';next.push(label);}
    else label.remove();
   }
   if(syncSelection)for(const input of form.querySelectorAll('[data-personal-filter]'))input.checked=!!source.querySelector(`[name="${input.name}"]`)?.checked;
   if(syncSelection)for(const input of form.querySelectorAll('[data-tag-mode]'))input.checked=input.value===(source.querySelector('[name="tag_mode"]:checked')?.value || 'all');
   labels=next;render();
   if(form.contains(focused) && focused.isConnected && form.ownerDocument.activeElement!==focused)focused.focus({preventScroll:true});
  });
  render();
 }
}
export function updateTagFilter(form,source,options){filters.get(form)?.(source,options);}
if(typeof document!=='undefined')initializeTagFilters();
