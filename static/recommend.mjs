import {createPublicApi} from './public-api.mjs';
import {createSearchController} from './catalog-search.mjs';
import {normalize} from './text.mjs';

const form = document.querySelector('#recommend-form');
if (form) {
  const write = createPublicApi();
  const query = document.querySelector('#recommend-query'), reason = document.querySelector('#recommend-reason');
  const candidates = document.querySelector('#candidates'), status = document.querySelector('#recommend-status');
  const searchStatus = document.querySelector('#candidate-status'), selectedStatus = document.querySelector('#selected-work');
  const submit = document.querySelector('#recommend-submit');
  const editing = form.dataset.editing === 'true', activity = form.dataset.activity;
  let selected = form.dataset.work ? {id: Number(form.dataset.work), in_scope: form.dataset.inScope === 'true', recommended: form.dataset.recommended === 'true', own_reason: reason.value} : null;
  let timer = null, composing = false, busy = false, searchState = selected || editing ? 'ready' : 'idle';
  function label() { submit.textContent = editing || selected?.recommended ? '保存' : selected?.in_scope ? '+1' : '推荐'; }
  function count() {
    try { document.querySelector('#reason-length').textContent = `${Array.from(normalize(reason.value, 'reason')).length} / 200`; }
    catch { document.querySelector('#reason-length').textContent = `${Array.from(reason.value.trim()).length} / 200`; }
  }
  const search = createSearchController({
    fetcher: (url, options) => {
      const params = new URL(url, location.origin).searchParams;
      params.delete('limit'); if (activity) params.set('activity',activity);
      return fetch('/api/v1/recommend/candidates/?'+params,options);
    },
    onStatus: (text, pending) => { searchStatus.textContent = text; if (pending) searchState='searching'; else if (searchState!=='ready') searchState='error'; },
    onResult: payload => {
      searchState='ready'; candidates.replaceChildren();
      for (const work of payload.results) {
        const button = document.createElement('button'); button.type='button'; button.className='candidate'; button.setAttribute('aria-pressed','false');
        const cover = document.createElement('span'); cover.className='book-cover candidate-cover'; cover.setAttribute('aria-hidden','true');
        const placeholder = () => { const rule=document.createElement('span'); rule.className='cover-rule'; return rule; };
        if (work.cover_url) {
          const image=document.createElement('img'); image.src=work.cover_url; image.alt=''; image.loading='lazy'; image.referrerPolicy='no-referrer';
          image.addEventListener('error',()=>image.replaceWith(placeholder()),{once:true}); cover.append(image);
        } else cover.append(placeholder());
        const info=document.createElement('span'); info.className='candidate-info';
        const title=document.createElement('strong'); title.textContent=work.title;
        const author=document.createElement('span'); author.textContent=work.author;
        const state=document.createElement('span');
        state.textContent = `${work.recommended ? '我已推荐 · ' : ''}推荐 ${work.recommendation_count}`;
        info.append(title,author,state); button.append(cover,info);
        button.addEventListener('click',()=>{
          if (busy) return;
          for (const item of candidates.querySelectorAll('button')) item.setAttribute('aria-pressed','false');
          selected=work; query.value=work.title; reason.value=work.own_reason || ''; count();
          button.setAttribute('aria-pressed','true'); selectedStatus.textContent=''; status.textContent=''; label();
        });
        candidates.append(button);
      }
    }
  });
  function invalidate() {
    if (selected && reason.value === selected.own_reason) { reason.value=''; count(); }
    clearTimeout(timer); search.cancel(); selected=null; searchState='idle'; candidates.replaceChildren(); selectedStatus.textContent='';
    searchStatus.textContent=''; status.textContent=''; label();
    if (!composing && query.value.trim()) { searchState='pending'; timer=setTimeout(()=>search.run(query.value),300); }
  }
  if (!editing) {
    query.addEventListener('input',invalidate);
    query.addEventListener('compositionstart',()=>{composing=true; invalidate(); searchState='pending';});
    query.addEventListener('compositionend',()=>{composing=false; invalidate();});
  }
  reason.addEventListener('input',count); count(); label();
  form.addEventListener('submit',async event=>{
    event.preventDefault(); if (busy || composing) return;
    let title,text;
    try {title=normalize(query.value,'title',true); text=normalize(reason.value,'reason');}
    catch {status.textContent='书名限 1—300 字，理由限 200 字';return;}
    busy=true; submit.disabled=true; query.readOnly=true; reason.readOnly=true; status.textContent='';
    try {
      if (!editing && !selected) {
        clearTimeout(timer);
        if(searchState!=='ready') {await search.run(query.value);}
        if(searchState!=='ready'){status.textContent='候选查询失败，请重试';return;}
        if(candidates.childElementCount){status.textContent='请选择已有作品';return;}
      }
      const payload=editing ? {reason:text} : {work_id:selected?.id ?? null,title,reason:text,confirmed_unmatched:false};
      if(activity && !editing)payload.copy_public_reason=document.querySelector('#copy-public-reason').checked;
      const result=await write(form.dataset.target,payload);
      if(result.status==='pending'){status.textContent='待核查'; submit.hidden=true;}
      else location.assign(form.dataset.success);
    } catch(error){
      status.textContent=error.message;
      if(error.code==='existing_candidate'){selected=null; searchState='pending'; await search.run(query.value);}
    } finally {busy=false; submit.disabled=false; query.readOnly=editing; reason.readOnly=false;}
  });
}
