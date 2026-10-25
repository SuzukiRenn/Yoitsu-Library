import {appendEditionBadge} from './edition-badge.mjs?v=20261024-editions-1';
import {createSearchController} from './catalog-search.mjs';

import {installCandidateKeyboard} from './candidate-keyboard.mjs?v=20261025-1';

const form = document.querySelector('#search-form');
const input = document.querySelector('#book-query');
const results = document.querySelector('#search-results');
const status = document.querySelector('#search-status');
const heading = document.querySelector('#results-title');
const initial = [...results.childNodes].map(node => node.cloneNode(true));
let composing = false, timer;

function element(tag, className, text) {
  const node = document.createElement(tag); node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}
function render(payload) {
  heading.textContent = '搜索结果'; results.replaceChildren();
  if (!payload.results.length) {
    const empty = element('div', 'empty-state');
    empty.append(element('h3', '', '无结果'));
    results.append(empty); return;
  }
  for (const work of payload.results) {
    const card = element('article', 'book-card');
    const open = element('a', 'card-open'); open.href = `/works/${work.id}/`; open.tabIndex = -1; open.setAttribute('aria-label', work.title);
    const title = element('a', 'book-title', work.title);appendEditionBadge(title,work); title.href = open.href; card.append(open);
    const cover = element('span', 'book-cover'); cover.setAttribute('aria-hidden', 'true');
    if (work.cover_url) {
      const image = document.createElement('img'); image.src = work.cover_url; image.alt = ''; image.loading = 'lazy'; image.referrerPolicy = 'no-referrer';
      image.addEventListener('error', () => image.replaceWith(element('span', 'cover-rule')), {once: true}); cover.append(image);
    } else cover.append(element('span', 'cover-rule'));
    const info = element('span', 'book-info');
    const author = element('span', 'book-author');
    if (work.author) {
      const link = element('a', 'work-author', work.author); link.href = `/?q=${encodeURIComponent(work.author)}`;
      link.setAttribute('aria-label', `搜索作者：${work.author}`); author.append(link);
    } else author.textContent = '—';
    info.append(element('span', 'book-status', work.is_public ? '已收录' : '未收录'), title, author);
    const tags = element('span', 'tags');
    for (const tag of work.tags) {
      const link = element('a', 'tag work-tag', tag.name); link.href = `/?tag=${encodeURIComponent(tag.id)}`;
      link.setAttribute('aria-label', `按标签筛选：${tag.name}`); tags.append(link);
    }
    info.append(tags); card.append(cover, info); results.append(card);
  }
}
const controller = createSearchController({fetcher: (...args) => fetch(...args), onResult: render, onStatus: (message, busy) => { status.textContent = message; results.setAttribute('aria-busy', String(busy)); }});
function update() {
  clearTimeout(timer); controller.cancel();
  if (composing) return;
  if (!input.value.trim()) {
    if (input.defaultValue) { results.replaceChildren(); heading.textContent = '搜索结果'; status.textContent = ''; }
    else { results.replaceChildren(...initial.map(node => node.cloneNode(true))); heading.textContent = '最近入库'; status.textContent = `${results.querySelectorAll('.book-card').length} 条`; }
    results.setAttribute('aria-busy', 'false'); return;
  }
  timer = setTimeout(() => controller.run(input.value), 300);
}
installCandidateKeyboard({input,list:results,selector:'.book-title',isComposing:()=>composing,onEnter:()=>{clearTimeout(timer);controller.run(input.value);}});
input.addEventListener('input', update);
input.addEventListener('compositionstart', () => { composing = true; clearTimeout(timer); controller.cancel(); status.textContent = ''; results.setAttribute('aria-busy', 'false'); });
input.addEventListener('compositionend', () => { composing = false; update(); });
form.addEventListener('submit', event => { event.preventDefault(); clearTimeout(timer); if (!composing) controller.run(input.value); });
window.addEventListener('pagehide', () => { clearTimeout(timer); controller.cancel(); });
