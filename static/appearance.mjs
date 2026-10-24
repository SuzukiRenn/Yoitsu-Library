import {APPEARANCE_KEY,DEFAULT_APPEARANCE,decodeAppearance,applyAppearance,normalizeAppearance} from './appearance-state.mjs?v=20261024-appearance-1';

export function installAppearance(doc=document,win=window){
 const trigger=doc.querySelector('[data-appearance-toggle]'),dialog=doc.querySelector('#appearance-settings');
 if(!trigger || !dialog || typeof dialog.showModal!=='function')return;
 const root=doc.documentElement,media=win.matchMedia('(prefers-color-scheme: dark)');
 const status=dialog.querySelector('[data-appearance-status]');
 let committed=normalizeAppearance({mode:root.dataset.appearanceMode,color:root.dataset.colorTheme}),draft={...committed};
 const show=prefs=>applyAppearance(root,prefs,media.matches);
 const choices=()=>[...dialog.querySelectorAll('input[type=radio]')];
 function sync(){for(const input of choices())input.checked=draft[input.name]===input.value;}
 function preview(){draft=normalizeAppearance({mode:choices().find(input=>input.name==='mode' && input.checked)?.value,color:choices().find(input=>input.name==='color' && input.checked)?.value});show(draft);status.textContent='';}
 trigger.addEventListener('click',()=>{
  if(dialog.open || doc.querySelector('dialog[open]'))return;
  draft={...committed};sync();status.textContent='';
  dialog.showModal();trigger.setAttribute('aria-expanded','true');
  choices().find(input=>input.name==='mode' && input.checked)?.focus({preventScroll:true});
 });
 dialog.addEventListener('change',event=>{if(event.target.matches?.('input[type=radio]'))preview();});
 dialog.querySelector('[data-appearance-reset]').addEventListener('click',()=>{draft={...DEFAULT_APPEARANCE};sync();show(draft);status.textContent='';});
 for(const button of dialog.querySelectorAll('[data-appearance-cancel]'))button.addEventListener('click',()=>dialog.close());
 dialog.querySelector('[data-appearance-save]').addEventListener('click',()=>{
  try{win.localStorage.setItem(APPEARANCE_KEY,JSON.stringify(draft));}
  catch{status.textContent='浏览器不允许保存设置。当前仍是预览，关闭后将恢复原设置。';return;}
  committed={...draft};dialog.close();
 });
 dialog.addEventListener('close',()=>{show(committed);trigger.setAttribute('aria-expanded','false');trigger.focus({preventScroll:true});});
 // Clicking actual backdrop closes; clicks on the dialog's padding do not.
 dialog.addEventListener('click',event=>{
  if(event.target!==dialog)return;const r=dialog.getBoundingClientRect();
  if(event.clientX<r.left || event.clientX>r.right || event.clientY<r.top || event.clientY>r.bottom)dialog.close();
 });
 win.addEventListener('storage',event=>{
  if(event.key!==APPEARANCE_KEY && event.key!==null)return;
  committed=decodeAppearance(event.newValue);
  if(dialog.open){status.textContent='另一页面更新了设置。保存将使用当前选择，关闭则采用最新设置。';}
  else show(committed);
 });
 media.addEventListener('change',()=>show(dialog.open?draft:committed));
 win.addEventListener('pagehide',()=>{show(committed);if(dialog.open)dialog.close();});
 show(committed);trigger.hidden=false;
 if(win.CustomEvent)doc.dispatchEvent?.(new win.CustomEvent('booklist:floating-controls'));
 return {preview,get preferences(){return {...committed};}};
}
