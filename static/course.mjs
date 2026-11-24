import {createReasonReferenceEditor} from './reason-reference-editor.mjs?v=20261025-keyboard-1';

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
