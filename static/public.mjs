import {createPublicApi} from './public-api.mjs';
const write = createPublicApi();
const status = document.querySelector('#action-status');
document.addEventListener('click', async event => {
  const button=event.target.closest('[data-action]');
  if(!button || button.disabled)return;
  const container=button.closest('[data-work]'), buttons=[...container.querySelectorAll('button')];
  buttons.forEach(item=>{item.disabled=true;}); status.textContent='';
  const prefix=container.dataset.activity ? `/api/v1/activities/${container.dataset.activity}` : '/api/v1/public';
  const payload=button.dataset.action==='withdraw' ? {} : {enabled:button.dataset.enabled==='true'};
  try {await write(`${prefix}/works/${container.dataset.work}/${button.dataset.action}/`,payload);location.reload();}
  catch(error){status.textContent=error.message;buttons.forEach(item=>{item.disabled=false;});}
});
document.addEventListener('click',async event=>{
  const more=event.target.closest('[data-more-reasons], #more-reasons');
  if(!more || more.disabled)return;
  more.disabled=true;status.textContent='';
  try {
    const page=Number(more.dataset.page)+1, section=document.querySelector('[data-reasons-work]');
    const url=more.dataset.url || `/api/v1/public/works/${section.dataset.reasonsWork}/reasons/`;
    const response=await fetch(`${url}?page=${page}&page_size=2`),payload=await response.json();
    if(!response.ok)throw Error(payload.error?.message || '请求失败');
    const container=more.closest('[data-reason-container]') || document.querySelector('#detail-reasons');
    for(const item of payload.results){const p=document.createElement('p');p.className='reason-text';p.textContent=item.reason;container.insertBefore(p,container.contains(more)?more:null);}
    more.dataset.page=String(page);more.hidden=page*2>=payload.total;
  }catch(error){status.textContent=error.message;}finally{more.disabled=false;}
});
