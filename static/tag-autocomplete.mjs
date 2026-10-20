const separator = /[,，\n]/u;
export const tagKey = value => value.normalize('NFKC').toLowerCase().trim();

export function tagInputContext(value, caret = value.length) {
  let start = caret, end = caret;
  while (start > 0 && !separator.test(value[start-1])) start--;
  while (end < value.length && !separator.test(value[end])) end++;
  const others = (value.slice(0,start)+value.slice(end)).split(separator).map(tagKey).filter(Boolean);
  return {start, end, query: value.slice(start,end).trim(), others};
}

export function completeTag(value, caret, name) {
  const token = tagInputContext(value, caret);
  const before = value.slice(0,token.start), after = value.slice(token.end);
  return {value: before+name+(after || '，'), caret: before.length+name.length+1};
}

function initialize(root) {
  const input = root.querySelector('input'), panel = root.querySelector('.tag-suggestions');
  const list = root.querySelector('[role="listbox"]'), empty = root.querySelector('.tag-suggestions-empty');
  const status = root.querySelector('[data-tag-status]');
  const pager = root.querySelector('.tag-suggestions-pagination'), pageInfo = root.querySelector('[data-tag-page-info]');
  const previous = root.querySelector('[data-tag-previous]'), next = root.querySelector('[data-tag-next]');
  let timer, request, generation = 0, items = [], active = -1, composing = false, choosing = false;
  let currentPage = 1, pages = 1, loading = false;
  function hide() {
    clearTimeout(timer); request?.abort(); generation++;
    panel.hidden = true; active = -1;
    input.setAttribute('aria-expanded','false'); input.removeAttribute('aria-activedescendant');
    status.textContent = '';
    loading = false;
    panel.removeAttribute('aria-busy');
  }
  function highlight(index, scroll = false) {
    active = index;
    [...list.children].forEach((button,i) => button.setAttribute('aria-selected',String(i === active)));
    if (active >= 0) {
      input.setAttribute('aria-activedescendant',list.children[active].id);
      if (scroll) list.children[active].scrollIntoView({block:'nearest'});
    }
  }
  function choose(name) {
    if (loading) return;
    const result = completeTag(input.value,input.selectionStart ?? input.value.length,name);
    input.value=result.value; hide();
    choosing=true; input.dispatchEvent(new Event('input',{bubbles:true})); choosing=false;
    input.focus({preventScroll:true}); input.setSelectionRange(result.caret,result.caret);
  }
  async function lookup(page = 1) {
    if (composing || input.matches(':disabled') || document.activeElement !== input) return;
    const token = tagInputContext(input.value,input.selectionStart ?? input.value.length);
    if (Array.from(token.query).length > 30) return;
    request?.abort();
    const current = ++generation; request = new AbortController();
    loading = true; previous.disabled=true; next.disabled=true;
    panel.setAttribute('aria-busy','true');
    const params = new URLSearchParams({q:token.query, page:String(page), exclude:JSON.stringify(token.others)});
    if (root.dataset.work) params.set('work',root.dataset.work);
    try {
      const response = await fetch('/api/v1/catalog/tags/?'+params,{signal:request.signal});
      const payload = await response.json();
      if (current !== generation || document.activeElement !== input || input.matches(':disabled')) return;
      if (!response.ok) { hide(); return; }
      items = payload.results;
      currentPage=payload.page; pages=payload.pages;
      list.replaceChildren(); active=-1;
      for (const [index,tag] of items.entries()) {
        const button=document.createElement('button'); button.type='button'; button.tabIndex=-1;
        button.id=`manual-tag-option-${tag.id}`; button.setAttribute('role','option');
        button.setAttribute('aria-selected','false'); button.textContent=tag.name;
        button.addEventListener('pointerdown',event=>event.preventDefault());
        button.addEventListener('pointermove',()=>highlight(index));
        button.addEventListener('click',()=>choose(tag.name)); list.append(button);
      }
      empty.hidden=items.length>0; panel.hidden=false;
      pager.hidden=payload.total===0;
      pageInfo.textContent=`共 ${payload.total} 个 · 第 ${currentPage} / ${pages} 页`;
      previous.disabled=currentPage<=1; next.disabled=currentPage>=pages;
      panel.classList.remove('opens-up');
      const bounds=panel.getBoundingClientRect();
      if (bounds.bottom > window.innerHeight-12 && input.getBoundingClientRect().top > bounds.height+12) panel.classList.add('opens-up');
      input.setAttribute('aria-expanded','true'); input.removeAttribute('aria-activedescendant');
      status.textContent=items.length ? `共 ${payload.total} 个已有标签，第 ${currentPage} / ${pages} 页，可用方向键选择，PageUp 和 PageDown 翻页` : '可填写新标签';
    } catch(error) { if (current === generation && error.name !== 'AbortError') hide(); }
    finally {if (current === generation) {loading=false; panel.removeAttribute('aria-busy');}}
  }
  function schedule() {
    if (choosing) return;
    hide(); if (!composing) timer=setTimeout(()=>lookup(1),180);
  }
  for (const button of [previous,next]) button.addEventListener('pointerdown',event=>event.preventDefault());
  previous.addEventListener('click',()=>{if(!loading && currentPage>1)lookup(currentPage-1);});
  next.addEventListener('click',()=>{if(!loading && currentPage<pages)lookup(currentPage+1);});
  input.addEventListener('input',schedule);
  input.addEventListener('focus',()=>{if(!choosing)schedule();});
  input.addEventListener('click',schedule);
  input.addEventListener('blur',hide);
  input.addEventListener('compositionstart',()=>{composing=true;hide();});
  input.addEventListener('compositionend',()=>{composing=false;schedule();});
  input.addEventListener('keydown',event=>{
    if (composing || event.isComposing) return;
    if (event.key==='Escape') {event.preventDefault();hide();return;}
    if (!panel.hidden && ['PageUp','PageDown'].includes(event.key)) {
      event.preventDefault();
      const page=currentPage+(event.key==='PageDown'?1:-1);
      if (!loading && page>=1 && page<=pages) lookup(page);
      return;
    }
    if (event.key==='Enter') {
      event.preventDefault();
      if (loading) return;
      const token=tagInputContext(input.value,input.selectionStart ?? input.value.length);
      if (!panel.hidden && active>=0 && !loading) choose(items[active].name);
      else if (token.query) choose(items.find(tag=>tagKey(tag.name)===tagKey(token.query))?.name || token.query);
      else hide();
      return;
    }
    if (!panel.hidden && !loading && items.length && ['ArrowDown','ArrowUp'].includes(event.key)) {
      event.preventDefault();
      highlight(active<0 ? event.key==='ArrowDown' ? 0 : items.length-1 : (active+(event.key==='ArrowDown'?1:-1)+items.length)%items.length,true);
    }
  });
  input.form?.addEventListener('submit',hide);
}

if (typeof document !== 'undefined') {
  for (const root of document.querySelectorAll('[data-tag-autocomplete]')) initialize(root);
}
