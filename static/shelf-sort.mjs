export function filterURL(form,base,Form=FormData){
  const url=new URL(base);url.search=new URLSearchParams(new Form(form)).toString();return url.href;
}
// Enhance shelf sorting, tag links and author searches. Real links remain usable without JavaScript.
export function installShelfSort(refresh, {doc=document,win=window,fetcher=fetch}={}) {
  const shelf=doc.querySelector('[data-live-shelf]');
  if(!shelf || !refresh?.apply)return;
  const controls=shelf.querySelector('.shelf-sort');
  const dropdown=controls.closest?.('[data-sort-dropdown]');
  if(dropdown){
    doc.addEventListener('pointerdown',event=>{if(dropdown.open && !dropdown.contains(event.target))dropdown.open=false;});
    dropdown.addEventListener('toggle',()=>{
      if(dropdown.open){const tags=shelf.querySelector('[data-tag-dropdown]');if(tags)tags.open=false;}
    });
    dropdown.addEventListener('keydown',event=>{
      if(event.key==='Escape' && dropdown.open){event.preventDefault();dropdown.open=false;dropdown.querySelector('summary').focus();}
    });
  }
  const status=doc.querySelector('#action-status');
  const path=win.location.pathname;
  let generation=0,request,timer,inputTimer,composing=false;
  let renderedURL=win.location.href;
  const marker='booklistShelf';
  win.history.replaceState({...win.history.state,[marker]:path},'',win.location.href);
  const announce=pending=>doc.dispatchEvent(new win.CustomEvent('booklist:shelf-navigation',{detail:{pending}}));
  async function navigate(url,push=true,loading='正在排序…') {
    win.clearTimeout(inputTimer);
    generation++;const epoch=generation;request?.abort();win.clearTimeout(timer);
    const controller=new AbortController();request=controller;
    const startingURL=win.location.href;
    let timedOut=false;
    timer=win.setTimeout(()=>{timedOut=true;controller.abort();},15000);
    controls.setAttribute('aria-busy','true');status.textContent=loading;announce(true);
    try {
      const response=await fetcher(url,{credentials:'same-origin',cache:'no-store',signal:controller.signal});
      if(!response.ok)throw Error(response.status===429?'操作较频繁，请稍后重试':'排序加载失败，请重试');
      const destination=new URL(response.url || url,startingURL);
      if(destination.origin!==win.location.origin || destination.pathname!==path)throw Error('页面已变化，请刷新后重试');
      const html=await response.text();
      if(epoch!==generation || win.location.href!==startingURL)return;
      const next=new win.DOMParser().parseFromString(html,'text/html').querySelector('[data-live-shelf]');
      if(!next || !next.querySelector('.shelf-sort') || !next.querySelector('[data-shelf-grid]'))throw Error('排序加载失败，请重试');
      refresh.apply(next,{preserveAnchor:false,animateReorder:true,syncFilters:true});
      syncSearchClear();
      if(push)win.history.pushState({...win.history.state,[marker]:path},'',destination.href);
      else if(destination.href!==win.location.href)win.history.replaceState({...win.history.state,[marker]:path},'',destination.href);
      renderedURL=destination.href;
    }catch(error){
      if(epoch===generation && win.location.href===startingURL) {
        if(!push)win.history.replaceState({...win.history.state,[marker]:path},'',renderedURL);
        if(timedOut)status.textContent='排序加载超时，请重试';
        else if(error.name!=='AbortError')status.textContent=error.message || '排序加载失败，请重试';
      }
    }finally{
      if(epoch===generation){win.clearTimeout(timer);controls.removeAttribute('aria-busy');if(status.textContent===loading)status.textContent='';announce(false);}
    }
  }
  const search=shelf.querySelector('#shelf-search');
  const searchForm=search?.closest?.('[data-tag-filter]');
  const clearSearch=searchForm?.querySelector('[data-clear-search]');
  function syncSearchClear(){if(clearSearch)clearSearch.hidden=!search.value;}
  const cancelForInput=()=>{
    win.clearTimeout(inputTimer);win.clearTimeout(timer);generation++;request?.abort();
    controls.removeAttribute('aria-busy');status.textContent='';announce(true);
  };
  const searchNow=()=>navigate(filterURL(searchForm,win.location.href,win.FormData || FormData),false,'正在筛选…');
  const scheduleSearch=event=>{
    syncSearchClear();
    cancelForInput();
    if(composing || event?.isComposing)return;
    if(!search.value.trim())return searchNow();
    inputTimer=win.setTimeout(searchNow,300);
  };
  if(searchForm){
    syncSearchClear();
    clearSearch?.addEventListener('click',()=>{composing=false;search.value='';search.focus({preventScroll:true});scheduleSearch();});
    search.addEventListener('input',scheduleSearch);
    search.addEventListener('compositionstart',()=>{composing=true;cancelForInput();});
    search.addEventListener('compositionend',()=>{composing=false;scheduleSearch();});
  }
  doc.addEventListener('submit',event=>{
    const form=event.target;
    if(!form.matches?.('[data-tag-filter]') || !shelf.contains(form))return;
    event.preventDefault();
    if(composing || event.isComposing)return;
    const url=filterURL(form,win.location.href,win.FormData || FormData);
    const dropdown=form.querySelector('[data-tag-dropdown]');if(dropdown)dropdown.open=false;
    return navigate(url,true,'正在筛选…');
  });
  doc.addEventListener('click',event=>{
    if(event.defaultPrevented || event.button!==0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey)return;
    const link=event.target.closest?.('.shelf-sort a[href], .public-card .work-tag[href], .public-card .work-author[href]');
    if(!link || !shelf.contains(link) || link.hasAttribute('download') || (link.target&&link.target!=='_self'))return;
    const url=new URL(link.href,win.location.href);
    if(url.origin!==win.location.origin || url.pathname!==path)return;
    event.preventDefault();
    if(link.hasAttribute('aria-current') && !controls.hasAttribute('aria-busy'))return;
    const filterLink=link.matches?.('.work-tag') || link.matches?.('.work-author');
    if(filterLink){for(const name of ['recommended','liked']){const input=searchForm?.querySelector(`[name="${name}"]`);if(input?.checked)url.searchParams.set(name,'1');else url.searchParams.delete(name);}}
    if(filterLink){const mode=searchForm?.querySelector('[name="tag_mode"]:checked')?.value;if(mode==='any')url.searchParams.set('tag_mode','any');else url.searchParams.delete('tag_mode');}
    if(filterLink){const panel=shelf.querySelector('[data-tag-dropdown]');if(panel)panel.open=false;}
    return navigate(url.href,true,filterLink?'正在筛选…':'正在排序…');
  });
  win.addEventListener('popstate',event=>{
    if(event.state?.[marker]===path && win.location.pathname===path)return navigate(win.location.href,false);
  });
  win.addEventListener('pagehide',()=>{
    generation++;request?.abort();win.clearTimeout(timer);win.clearTimeout(inputTimer);controls.removeAttribute('aria-busy');if(['正在排序…','正在筛选…'].includes(status.textContent))status.textContent='';announce(false);
  });
  return {navigate};
}
