// CSS hides only work counts. Keep accessible action names in sync without touching statistics.
export function syncHeatLabels(scope){
 const doc=scope.ownerDocument || scope,hidden=doc.documentElement?.dataset.hideHeat==='true';
 for(const button of scope.querySelectorAll('[data-action]')){
  const label=button.getAttribute?.('aria-label');if(!label)continue;
  if(label.includes('，'))button.dataset.heatLabel=label;
  const full=button.dataset.heatLabel || label;
  button.setAttribute('aria-label',hidden?full.split('，')[0]:full);
 }
}
export function installHeatDisplay(doc=document){
 const sync=()=>syncHeatLabels(doc);sync();doc.addEventListener('booklist:preferences',sync);
}
