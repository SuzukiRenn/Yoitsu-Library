export function installShelfView(doc=document,win=window){
 const shelf=doc.querySelector('[data-live-shelf]'),button=doc.querySelector('[data-shelf-view-toggle]');
 if(!shelf || !button)return;
 const root=doc.documentElement,key='booklist:shelf-view:v1';
 function apply(compact){
  root.dataset.shelfView=compact?'compact':'cards';
  const label=compact?'切换为卡片视图':'切换为列表视图';
  button.setAttribute('aria-label',label);button.setAttribute('aria-pressed',String(compact));
  button.querySelector('[role="tooltip"]').textContent=label;
  for(const icon of button.querySelectorAll('[data-view-icon]'))icon.toggleAttribute('hidden',icon.dataset.viewIcon!==(compact?'cards':'list'));
 }
 function change(compact){
  const anchor=[...shelf.querySelectorAll('[data-shelf-work]')].find(card=>card.getBoundingClientRect().bottom>0);
  const top=anchor?.getBoundingClientRect().top;
  apply(compact);
  if(anchor && win.scrollY>60)win.scrollBy(0,anchor.getBoundingClientRect().top-top);
 }
 button.addEventListener('click',()=>{
  const compact=root.dataset.shelfView!=='compact';change(compact);
  try{win.localStorage.setItem(key,compact?'compact':'cards');}catch{}
 });
 win.addEventListener('storage',event=>{if(event.key===key || event.key===null)change(event.newValue==='compact');});
 apply(root.dataset.shelfView==='compact');button.hidden=false;
}
