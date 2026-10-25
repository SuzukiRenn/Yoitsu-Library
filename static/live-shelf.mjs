import {syncHeatLabels} from './heat-display.mjs?v=20261025-1';
import {createShelfMotion} from './shelf-motion.mjs?v=20261025-reading-1';
import {initializeCovers} from './cover-images.mjs?v=20261020-2';
import {initializePageSizes} from './page-size.mjs?v=20261020-2';
import {initializeTagFilters, updateTagFilter} from './tag-filter.mjs?v=20261025-tags-more-1';

const ids = grid => [...grid.querySelectorAll('[data-shelf-work]')].map(card => card.dataset.shelfWork);
export function membershipChanged(before, after) {
  return before.length !== after.length || before.some((id, index) => id !== after[index]);
}
const tagSignature = form => JSON.stringify([...form?.querySelectorAll('[data-tag-name]') || []]
  .map(label => [label.querySelector('input').value, label.dataset.tagName,
    label.querySelector('.tag-count').textContent]).sort((a,b) => a[0].localeCompare(b[0])));

// Fetch the same server-rendered page: permissions, sorting, filters and pagination
// remain identical to normal navigation. Never execute scripts from that response.
export function createShelfRefresh({doc = document, win = window, fetcher = fetch} = {}) {
  const shelf = doc.querySelector('[data-live-shelf]');
  if (!shelf) return;
  const grid = shelf.querySelector('[data-shelf-grid]');
  const status = shelf.querySelector('[data-shelf-status]');
  const motion=createShelfMotion({doc,win});
  let pagerSignature;
  function sync(next, {preserveAnchor=true, animateReorder=true, syncFilters=false} = {}) {
    const focused = doc.activeElement;
    const before = ids(grid), after = ids(next.querySelector('[data-shelf-grid]'));
    const changed = membershipChanged(before, after);
    const existing = new Map([...grid.querySelectorAll('[data-shelf-work]')].map(card => [card.dataset.shelfWork, card]));
    const newCards = [...next.querySelectorAll('[data-shelf-grid] > [data-shelf-work]')];
    const finishMotion=changed && animateReorder ? motion.capture(grid) : null;
    const focusCard = focused?.closest('[data-shelf-work]');
    const focusRemoved = changed && focusCard && !after.includes(focusCard.dataset.shelfWork);
    const anchor = preserveAnchor && win.scrollY > 60 ? [...existing.values()].find(card =>
      after.includes(card.dataset.shelfWork) && card.getBoundingClientRect().bottom > 0) : null;
    const anchorTop = anchor?.getBoundingClientRect().top;
    if (changed) {
      for (const child of [...grid.children]) {
        if (!child.dataset.shelfWork || !after.includes(child.dataset.shelfWork)) child.remove();
      }
      newCards.forEach((card, index) => {
        const retained = existing.get(card.dataset.shelfWork);
        const node = retained || doc.importNode(card, true);
        if (!retained) node.classList.add('shelf-card-enter');
        if (grid.children[index] !== node) grid.insertBefore(node, grid.children[index] || null);
      });
      if (!newCards.length) {
        for (const child of next.querySelector('[data-shelf-grid]').children) grid.append(doc.importNode(child, true));
      }
      initializeCovers(grid);
      const added = after.filter(id => !before.includes(id)).length;
      status.textContent = added ? `书单已更新，本页新增 ${added} 部作品` : '书单已更新';
    }
    // The server renders matches against its own scope and frozen snapshot fields.
    // Copy only the visible field content; keep the card and its interaction state attached.
    for(const incoming of newCards){
      const current=grid.querySelector?.(`[data-shelf-work="${incoming.dataset.shelfWork}"]`);
      if(!current)continue;
      for(const selector of ['.book-info .book-title','.book-info .work-author']){
        const field=current.querySelector(selector),nextField=incoming.querySelector(selector);
        if(field && nextField && field.innerHTML!==nextField.innerHTML)field.replaceChildren(...[...nextField.childNodes].map(node=>doc.importNode(node,true)));
      }
    }
    syncHeatLabels(shelf);
    const total = shelf.querySelector('[data-shelf-total]'), nextTotal = next.querySelector('[data-shelf-total]');
    if (total && nextTotal && total.textContent !== nextTotal.textContent) total.textContent = nextTotal.textContent;
    const incomingPagerSignature = [...next.querySelectorAll('[data-shelf-pagination]')].map(slot => slot.innerHTML).join('');
    if (incomingPagerSignature !== pagerSignature) {
      for (const position of ['top', 'bottom']) {
        const slot = shelf.querySelector(`[data-shelf-pagination="${position}"]`);
        const incoming = next.querySelector(`[data-shelf-pagination="${position}"]`);
        if (!slot || !incoming) continue;
        const nav = slot.querySelector('nav'), nextNav = incoming.querySelector('nav');
        if (nav && nextNav) {
          for (const selector of ['.page-summary', '.page-links']) {
            const current = nav.querySelector(selector), incoming = nextNav.querySelector(selector);
            if (current && incoming) current.replaceWith(doc.importNode(incoming, true));
            else if (current) current.remove();
            else if (incoming) nav.prepend(doc.importNode(incoming, true));
          }
          // Keep the picker and its ancestors attached, so focus and an open menu survive.
          if (!nav.querySelector('.page-size-form') && nextNav.querySelector('.page-size-form')) {
            nav.append(doc.importNode(nextNav.querySelector('.page-size-form'), true));
          }
        } else slot.replaceChildren(...[...incoming.childNodes].map(node => doc.importNode(node, true)));
      }
      initializePageSizes(shelf);
      pagerSignature = incomingPagerSignature;
    }
    const form = shelf.querySelector('[data-tag-filter]'), nextForm = next.querySelector('[data-tag-filter]');
    if (nextForm && (syncFilters || tagSignature(form) !== tagSignature(nextForm))) {
      if (form) {
        updateTagFilter(form, nextForm, {syncSelection:syncFilters});
        if(syncFilters)form.querySelector('[name="q"]').value=nextForm.querySelector('[name="q"]').value;
      }
      else {
        shelf.insertBefore(doc.importNode(nextForm, true), shelf.querySelector('[data-shelf-pagination]'));
        initializeTagFilters(shelf);
      }
    }
    const clearTags=shelf.querySelector('[data-clear-tags]'), nextClearTags=next.querySelector('[data-clear-tags]');
    if(clearTags && nextClearTags)clearTags.setAttribute('href',nextClearTags.getAttribute('href'));
    const nextSort = next.querySelector('.shelf-sort');
    if (nextSort) {
      const links = [...nextSort.querySelectorAll('a')];
      for (const link of shelf.querySelectorAll('.shelf-sort a')) {
        const incoming = links.find(item => item.textContent === link.textContent);
        if (!incoming) continue;
        link.setAttribute('href', incoming.getAttribute('href'));
        if (incoming.hasAttribute('aria-current')) link.setAttribute('aria-current', 'true');
        else link.removeAttribute('aria-current');
      }
      const periodGroup=shelf.querySelector('.shelf-period'), incomingPeriod=nextSort.querySelector('.shelf-period');
      if(periodGroup && incomingPeriod)periodGroup.hidden=incomingPeriod.hidden;
      const periodLink=nextSort.querySelector('.shelf-period [aria-current]');
      if(periodLink)for(const input of shelf.querySelectorAll('input[name="period"]')) {
        input.value=new URL(periodLink.getAttribute('href'),win.location.href).searchParams.get('period') || 'all';
      }
      const active=nextSort.querySelector('[aria-current]');
      const sort=active ? new URL(active.getAttribute('href'), win.location.href).searchParams.get('sort') : null;
      if (sort) for (const input of shelf.querySelectorAll('input[name="sort"]')) input.value=sort;
    }
    if (focused?.isConnected && doc.activeElement !== focused) focused.focus({preventScroll: true});
    else if (focusRemoved) {
      const target = grid.querySelector('.book-title') || shelf.querySelector('h1');
      if (target) {
        if (target.tagName !== 'A') target.setAttribute('tabindex', '-1');
        target.focus({preventScroll: true});
      }
    }
    if (anchor?.isConnected) win.scrollBy(0, anchor.getBoundingClientRect().top - anchorTop);
    finishMotion?.();
  }
  const refresh = async ({signal, isCurrent}) => {
    const url = win.location.href;
    const response = await fetcher(url, {credentials: 'same-origin', cache: 'no-store', signal});
    if (!response.ok) return response;
    const html = await response.text();
    if (!isCurrent() || win.location.href !== url) return;
    const next = new win.DOMParser().parseFromString(html, 'text/html').querySelector('[data-live-shelf]');
    if (!next) throw Error('Unexpected shelf response');
    sync(next);
    if (response.redirected) {
      const destination = new URL(response.url);
      if (destination.origin === win.location.origin && destination.pathname === win.location.pathname) {
        win.history.replaceState(win.history.state, '', destination.pathname+destination.search+win.location.hash);
      }
    }
    return response;
  };
  refresh.apply = sync;
  return refresh;
}
