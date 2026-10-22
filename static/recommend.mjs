import {createPublicApi} from './public-api.mjs?v=20261022-identity-sync';
import {createSearchController} from './catalog-search.mjs?v=20261022-1';
import {normalize} from './text.mjs';
import {createCandidatePreview} from './candidate-preview.mjs';
import {readMetadata} from './manual-metadata.mjs?v=20261020-5';

const form = document.querySelector('#recommend-form');
if (form) {
  const preview = createCandidatePreview();
  const write = createPublicApi();
  const query = document.querySelector('#recommend-query'), reason = document.querySelector('#recommend-reason');
  const candidates = document.querySelector('#candidates'), status = document.querySelector('#recommend-status');
  const searchStatus = document.querySelector('#candidate-status'), selectedStatus = document.querySelector('#selected-work');
  const changeWork = document.querySelector('#change-work');
  const selectedCard = document.querySelector('#selected-work-card');
  const existingNotice = document.querySelector('#existing-recommendation');
  const submitTip = document.querySelector('#recommend-submit-tip');
  const submit = document.querySelector('#recommend-submit');
  const manual = document.querySelector('#manual-metadata');
  function manualState(show) { if (manual) { manual.hidden = !show; manual.disabled = !show; } }
  const editing = form.dataset.editing === 'true', activity = form.dataset.activity;
  let selected = form.dataset.work ? {id: Number(form.dataset.work), in_scope: form.dataset.inScope === 'true', recommended: form.dataset.recommended === 'true', recommendation_count: Number(form.dataset.recommendationCount || 0), own_reason: reason.value} : null;
  let timer = null, composing = false, busy = false, searchState = selected || editing ? 'ready' : 'idle';
  let manualNew = false;
  let candidateQuery = query.value;
  let candidateSummary = '';
  function candidateContent(work) {
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
    state.className='candidate-recommendation-state';
    state.textContent = `${work.recommended ? '我已推荐 · ' : work.recommendation_count > 0 ? '已被推荐 · ' : ''}推荐 ${work.recommendation_count}`;
    info.append(title,author,state);
    return [cover,info];
  }
  function hideSelectedCard() { selectedCard.hidden=true; selectedCard.replaceChildren(); }
  function candidateList(show) {
    candidates.hidden = !show;
    searchStatus.hidden = !show;
    if (changeWork) changeWork.hidden = show;
  }
  function label() {
    const plusOne = !editing && !selected?.recommended && Boolean(selected?.in_scope);
    submit.textContent = editing || selected?.recommended ? '保存' : plusOne ? '+1' : '推荐';
    submitTip.hidden = !plusOne;
    if (plusOne) submit.setAttribute('aria-describedby',submitTip.id);
    else submit.removeAttribute('aria-describedby');
    submit.closest('.recommend-control').removeAttribute('data-tooltip-dismissed');
    existingNotice.hidden = !selected || !(selected.recommendation_count > 0);
    existingNotice.textContent = existingNotice.hidden ? '' : selected.recommended
      ? `你已${activity ? '在本活动中' : '在公共书单中'}推荐过这本书，${selected.own_reason?.trim() ? '可修改推荐理由' : '可补充推荐理由'}。`
      : `这本书已${activity ? '在本活动中被推荐' : '被推荐到公共书单'}，点击「+1」表示你也推荐。`;
  }
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
    onStatus: (text, pending, {append=false}={}) => { searchStatus.textContent = text; if (!append) { if (pending) searchState='searching'; else if (searchState!=='ready') searchState='error'; } },
    onResult: (payload, {append=false}={}) => {
      const scrollTop = append ? candidates.scrollTop : 0;
      const moveFocus = append && document.activeElement === candidates.querySelector('[data-load-more]');
      let firstNewCandidate;
      searchState='ready'; manualNew=false; preview.hide();
      if (append) {
        candidates.querySelector('[data-load-more]')?.remove();
        candidates.querySelector('[data-manual-new]')?.remove();
      } else candidates.replaceChildren();
      candidateSummary = `已展示 ${payload.loaded} 部／共匹配 ${payload.total} 部`;
      candidateQuery = payload.query || query.value;
      candidateList(true);
      if (!selected) {selectedStatus.textContent=''; hideSelectedCard();}
      manualState(!editing && !selected && !payload.loaded);
      for (const work of payload.results) {
        const button = document.createElement('button'); button.type='button'; button.className='candidate'; button.setAttribute('aria-pressed','false');
        firstNewCandidate ||= button;
        button.append(...candidateContent(work));
        button.addEventListener('click',()=>{
          if (busy) return;
          search.cancel(); searchStatus.textContent=candidateSummary;
          for (const item of candidates.querySelectorAll('button')) item.setAttribute('aria-pressed','false');
          selected=work; query.value=work.title; reason.value=work.own_reason || ''; count();
          manualNew=false;
          manualState(false);
          const manualChoice=candidates.querySelector('[data-manual-new]');
          if (manualChoice) manualChoice.hidden=true;
          button.setAttribute('aria-pressed','true');
          candidateList(false); changeWork.textContent='重新选择作品';
          selectedCard.replaceChildren(...candidateContent(work)); selectedCard.hidden=false;
          selectedStatus.textContent='已选择作品'; status.textContent=''; label();
          reason.focus({preventScroll:true});
        });
        preview.bind(button, work); candidates.append(button);
      }
      if (payload.has_more) {
        const more=document.createElement('button');
        more.type='button'; more.className='candidate-load-more'; more.dataset.loadMore='';
        more.textContent='加载更多';
        more.addEventListener('click',async()=>{
          if (busy || more.disabled) return;
          more.disabled=true; more.textContent='正在加载…';
          try { await search.loadMore(); }
          finally { if (more.isConnected) {more.disabled=false;more.textContent='加载更多';} }
        });
        candidates.append(more);
      }
      if (!editing && payload.loaded && payload.strategy === 'local') {
        const choice=document.createElement('button');
        choice.type='button'; choice.className='manual-candidate'; choice.dataset.manualNew='';
        choice.setAttribute('aria-pressed','false');
        const title=document.createElement('strong'); title.textContent='都不是？手动填写新作品';
        const hint=document.createElement('span'); hint.textContent='保留当前书名，自行补充标签和简介';
        choice.append(title,hint);
        choice.addEventListener('click',()=>{
          if (busy) return;
          search.cancel(); searchStatus.textContent=candidateSummary;
          preview.hide(); selected=null; manualNew=true;
          hideSelectedCard();
          for (const item of candidates.querySelectorAll('button')) item.setAttribute('aria-pressed','false');
          choice.setAttribute('aria-pressed','true'); manualState(true);
          candidateList(false); changeWork.textContent='返回候选作品';
          selectedStatus.textContent=`将新建《${query.value.trim()}》`; status.textContent=''; label();
          manual.querySelector('input').focus({preventScroll:true});
        });
        candidates.append(choice);
      }
      candidates.scrollTop=scrollTop;
      if (moveFocus) firstNewCandidate?.focus({preventScroll:true});
    }
  });
  function invalidate() {
    hideSelectedCard();
    manualNew=false;
    manualState(false);
    preview.hide();
    if (selected && reason.value === selected.own_reason) { reason.value=''; count(); }
    clearTimeout(timer); search.cancel(); selected=null; searchState='idle'; candidates.replaceChildren(); selectedStatus.textContent='';
    candidateSummary='';
    searchStatus.textContent=''; status.textContent=''; label();
    candidateList(true);
    if (!composing && query.value.trim()) { searchState='pending'; timer=setTimeout(()=>search.run(query.value),300); }
  }
  if (!editing) {
    changeWork.addEventListener('click',()=>{
      if (busy) return;
      if (selected && reason.value === selected.own_reason) { reason.value=''; count(); }
      preview.hide(); selected=null; manualNew=false;
      hideSelectedCard();
      manualState(false); candidateList(true);
      query.value=candidateQuery;
      for (const item of candidates.querySelectorAll('button')) {
        item.hidden=false; item.setAttribute('aria-pressed','false');
      }
      selectedStatus.textContent=''; status.textContent=''; label();
      query.focus({preventScroll:true});
      if (!candidates.childElementCount && query.value.trim()) {searchState='pending'; search.run(query.value);}
    });
    query.addEventListener('input',invalidate);
    query.addEventListener('compositionstart',()=>{composing=true; invalidate(); searchState='pending';});
    query.addEventListener('compositionend',()=>{composing=false; invalidate();});
  }
  if (selected) {candidateList(false); if (changeWork) changeWork.textContent='重新选择作品';}
  reason.addEventListener('input',count); count(); label();
  form.addEventListener('submit',async event=>{
    event.preventDefault(); if (busy || composing) return;
    preview.hide();
    let title,text;
    try {title=normalize(query.value,'title',true); text=normalize(reason.value,'reason');}
    catch {status.textContent='书名限 1—300 字，理由限 200 字';return;}
    busy=true; submit.disabled=true; query.readOnly=true; reason.readOnly=true; status.textContent='';
    try {
      if (!editing && !selected) {
        clearTimeout(timer);
        if(searchState!=='ready') {await search.run(query.value);}
        if(searchState!=='ready'){status.textContent='候选查询失败，请重试';return;}
        if(candidates.querySelector('.candidate') && !manualNew){
          status.textContent=candidates.querySelector('[data-manual-new]') ? '请选择已有作品，或点击列表底部手动填写新作品' : '请选择已有作品';return;
        }
      }
      const payload=editing ? {reason:text} : {work_id:selected?.id ?? null,title,reason:text,confirmed_unmatched:manualNew};
      if (!editing && !selected) {
        try { payload.metadata = readMetadata(form); }
        catch (error) { status.textContent = error.message || '简介最多 2000 字'; return; }
      }
      if(activity && !editing)payload.copy_public_reason=document.querySelector('#copy-public-reason').checked;
      if (manual) manual.disabled = true;
      const result=await write(form.dataset.target,payload);
      if(result.status==='pending'){status.textContent='待核查'; submit.hidden=true;}
      else location.assign(form.dataset.success);
    } catch(error){
      status.textContent=error.message;
      if(error.code==='existing_candidate' || error.code==='resubmission_required'){selected=null; searchState='pending'; await search.run(query.value);}
    } finally {busy=false; submit.disabled=false; query.readOnly=editing; reason.readOnly=false; if (manual) manual.disabled = manual.hidden;}
  });
}
