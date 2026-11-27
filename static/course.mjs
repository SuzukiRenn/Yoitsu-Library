import {createReasonReferenceEditor} from './reason-reference-editor.mjs?v=20261025-keyboard-1';

const helpList = document.querySelector('.course-help-list');
const helpCards = helpList?.querySelectorAll(':scope > .course-help-card');
if (helpCards?.length && typeof ResizeObserver === 'function') {
  let frame;
  const resize = new ResizeObserver(() => {
    if (frame !== undefined) return;
    frame = requestAnimationFrame(() => {
      frame = undefined;
      const style = getComputedStyle(helpList);
      const multipleColumns = style.gridTemplateColumns.trim().split(/\s+/).length > 1;
      if (multipleColumns) {
        const row = parseFloat(style.getPropertyValue('--course-help-row'));
        const gap = parseFloat(style.columnGap);
        // Read all heights before writing spans. Grid places cards in reading order.
        const spans = Array.from(helpCards, card => Math.ceil((card.getBoundingClientRect().height + gap) / row));
        helpCards.forEach((card, index) => {
          const span = String(spans[index]);
          if (card.style.getPropertyValue('--course-help-span') !== span) card.style.setProperty('--course-help-span', span);
        });
      }
      helpList.toggleAttribute('data-help-masonry', multipleColumns);
    });
  });
  resize.observe(helpList);
  helpCards.forEach(card => resize.observe(card));
}

const examples = document.querySelector('[data-course-examples]');
if (examples) {
  const query = examples.form.elements.query;
  for (const button of examples.querySelectorAll('[data-course-example]')) {
    button.addEventListener('click', () => {
      query.value = button.dataset.courseExample;
      query.dispatchEvent(new Event('input'));
      query.focus();
    });
  }
  examples.hidden = false;
}

const search = document.querySelector('[data-course-search-form]');
if (search) {
  const token = search.dataset.courseSearchForm;
  const clear = search.querySelector('.course-find-clear');
  const query = search.elements.query;
  const conditions = document.querySelector('[data-course-search-conditions]');
  const fields = ['keywords', 'tags', 'exclude'];
  const store = 'booklist:course-search-drafts';
  const ttl = 3600000;
  const reader = document.body.dataset.readerId || '';
  const draftKey = token && `${reader}:${token}`;
  let clearing = false;
  const updateClear = () => { clear.hidden = !token && !query.value; };
  const drafts = () => {
    try { return JSON.parse(sessionStorage.getItem(store)) || {}; }
    catch { return {}; }
  };
  const updateDrafts = draft => {
    if (!draftKey) return;
    try {
      const rows = drafts();
      if (draft) rows[draftKey] = { ...draft, at: Date.now() };
      else delete rows[draftKey];
      sessionStorage.setItem(store, JSON.stringify(Object.fromEntries(
        Object.entries(rows).filter(([, row]) => Date.now() - row.at < ttl)
          .sort(([, a], [, b]) => b.at - a.at).slice(0, 12)
      )));
    } catch { /* Search works even when session storage is unavailable. */ }
  };
  if (conditions) {
    const draft = drafts()[draftKey];
    if (draft && Date.now() - draft.at < ttl) {
      conditions.open = draft.open;
      fields.forEach((name, index) => { conditions.querySelector('form').elements[name].value = draft.values[index]; });
    }
    window.addEventListener('pagehide', () => {
      if (!clearing) {
        const form = conditions.querySelector('form');
        updateDrafts({ open: conditions.open, values: fields.map(name => form.elements[name].value.slice(0, 100)) });
      }
    });
  }
  query.addEventListener('input', updateClear);
  updateClear();
  search.addEventListener('submit', event => {
    if (event.submitter !== clear) return;
    clearing = true;
    query.value = '';
    updateDrafts(null);
  });
  if (token) {
    // A cached browser document may contain a cleared search or a since-hidden work.
    // Reload only its GET. Navigation context restores a valid result viewport.
    window.addEventListener('pageshow', event => {
      if (!event.persisted) return;
      document.documentElement.setAttribute('data-course-search-check', '');
      window.location.reload();
    });
  }
}

for (const picker of document.querySelectorAll('[data-course-picker]')) {
  const query = picker.querySelector('[data-course-query]');
  const trigger = picker.querySelector('[data-course-search]');
  const status = picker.querySelector('[data-course-status]');
  const results = picker.querySelector('[data-course-results]');
  const selected = picker.querySelector('[data-course-work]');
  let previous;
  const search = async () => {
    if (!query.value.trim()) {
      status.textContent = '请输入书名或作者。';
      return;
    }
    previous?.abort();
    const controller = new AbortController();
    previous = controller;
    results.replaceChildren();
    status.textContent = '正在查找…';
    try {
      const response = await fetch('/api/v1/catalog/search/?limit=8&q=' + encodeURIComponent(query.value.trim()), {
        signal: controller.signal, cache: 'no-store'
      });
      if (!response.ok) throw new Error('Search failed');
      const payload = await response.json();
      if (controller.signal.aborted) return;
      for (const work of payload.results) {
        const item = document.createElement('li');
        const button = document.createElement('button');
        button.type = 'button';
        button.textContent = work.author ? work.title + ' · ' + work.author : work.title;
        button.addEventListener('click', () => {
          selected.value = String(work.id);
          status.textContent = '已选 ' + work.title;
          trigger.focus();
          results.replaceChildren();
        });
        item.append(button);
        results.append(item);
      }
      status.textContent = payload.results.length ? '请选择一部作品。' : '未找到匹配作品。';
    } catch (error) {
      if (!controller.signal.aborted) status.textContent = '搜索失败，请重试。';
    }
  };
  trigger.addEventListener('click', search);
  query.addEventListener('keydown', event => {
    if (event.key === 'Enter' && !event.isComposing) {
      event.preventDefault();
      search();
    }
  });
}

const form = document.querySelector('[data-course-route-editor]');
if (form) {
  const textarea = form.elements.reason;
  const editor = createReasonReferenceEditor(textarea, {exclude: () => Number(form.dataset.exclude)});
  const editable = form.querySelector('.reason-reference-editor');
  editable.setAttribute('aria-label', '阅读建议');
  const count = () => document.getElementById('reason-length').textContent = Array.from(textarea.value).length + ' / 200 字';
  textarea.addEventListener('input', count);
  count();
  form.addEventListener('submit', event => {
    if (event.submitter?.value === 'withdraw') return;
    try {
      const value = editor.value();
      textarea.value = value.text;
      form.elements.references.value = JSON.stringify(value.references);
    } catch {
      event.preventDefault();
      document.getElementById('reason-length').textContent = '阅读建议超出 200 字，请缩短后保存。';
      editor.focus();
    }
  });
}
