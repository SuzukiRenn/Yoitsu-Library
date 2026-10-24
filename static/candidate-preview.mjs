import {appendEditionBadge} from './edition-badge.mjs?v=20261024-editions-1';
// Reuse candidate metadata; hovering never sends a lookup or a contribution.
export function createCandidatePreview() {
  const panel = document.createElement('div');
  panel.id = 'candidate-preview'; panel.className = 'candidate-preview';
  panel.setAttribute('role', 'group'); panel.setAttribute('aria-label', '作品预览'); panel.hidden = true;
  document.body.append(panel);
  let active = null, showTimer = null, hideTimer = null;

  function hide() {
    clearTimeout(showTimer); clearTimeout(hideTimer);
    active?.removeAttribute('aria-describedby');
    active = null; panel.hidden = true;
  }

  function laterHide() {
    clearTimeout(showTimer); clearTimeout(hideTimer);
    hideTimer = setTimeout(() => {
      if (!panel.matches(':hover') && !panel.matches(':focus-within') && !active?.matches(':focus-visible')) hide();
    }, 150);
  }

  function position(button) {
    if (!button.isConnected) { hide(); return; }
    const bounds = button.getBoundingClientRect(), list = button.closest('#candidates').getBoundingClientRect();
    if (bounds.bottom <= Math.max(0, list.top) || bounds.top >= Math.min(window.innerHeight, list.bottom)) {
      hide(); return;
    }
    const popup = panel.getBoundingClientRect(), margin = 12, gap = 10;
    let left = bounds.left, top = bounds.bottom + gap;
    if (bounds.right + gap + popup.width <= window.innerWidth - margin) {
      left = bounds.right + gap; top = bounds.top;
    } else if (top + popup.height > window.innerHeight - margin) {
      top = bounds.top - popup.height - gap;
    }
    panel.style.left = `${Math.max(margin, Math.min(left, window.innerWidth - popup.width - margin))}px`;
    panel.style.top = `${Math.max(margin, Math.min(top, window.innerHeight - popup.height - margin))}px`;
  }

  function show(button, work) {
    hide();
    if (!button.isConnected) return;
    const cover = document.createElement('span'); cover.className = 'book-cover'; cover.setAttribute('aria-hidden', 'true');
    const placeholder = () => {
      const rule = document.createElement('span'); rule.className = 'cover-rule'; return rule;
    };
    if (work.cover_url) {
      const image = document.createElement('img'); image.src = work.cover_url;
      image.alt = ''; image.referrerPolicy = 'no-referrer';
      image.addEventListener('error', () => image.replaceWith(placeholder()), {once: true}); cover.append(image);
    } else cover.append(placeholder());
    const info = document.createElement('div'); info.className = 'candidate-preview-info';
    const title = document.createElement('strong'); title.textContent = work.title;appendEditionBadge(title,work);
    const author = document.createElement('p'); author.className = 'book-author';
    if (work.author) {
      const link = document.createElement('a'); link.className = 'work-author'; link.textContent = work.author;
      const activity = document.querySelector('#recommend-form')?.dataset.activity;
      link.href = `${activity ? `/activities/${activity}/` : '/'}?q=${encodeURIComponent(work.author)}`;
      link.setAttribute('aria-label', `搜索作者：${work.author}`); author.append(link);
    } else author.textContent = '—';
    const tags = document.createElement('div'); tags.className = 'tags';
    for (const item of work.tags || []) {
      const tag = document.createElement('a'); tag.className = 'tag work-tag'; tag.textContent = item.name;
      const activity = document.querySelector('#recommend-form')?.dataset.activity;
      tag.href = `${activity ? `/activities/${activity}/` : '/'}?tag=${encodeURIComponent(item.id)}`;
      tag.setAttribute('aria-label', `按标签筛选：${item.name}`); tags.append(tag);
    }
    if (!tags.childElementCount) {
      const empty = document.createElement('span'); empty.className = 'section-note'; empty.textContent = '暂无标签'; tags.append(empty);
    }
    info.append(title, author, tags); panel.replaceChildren(cover, info);
    active = button; button.setAttribute('aria-describedby', panel.id); panel.hidden = false;
    position(button);
  }

  panel.addEventListener('pointerenter', () => clearTimeout(hideTimer));
  panel.addEventListener('pointerleave', laterHide);
  panel.addEventListener('focusout', laterHide);
  window.addEventListener('resize', hide);
  document.addEventListener('scroll', event => {
    if (active && !panel.contains(event.target)) position(active);
  }, true);
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && active) { event.preventDefault(); hide(); }
  });

  return {
    hide,
    bind(button, work) {
      button.addEventListener('pointerenter', event => {
        if (event.pointerType !== 'mouse' || !window.matchMedia('(hover: hover)').matches) return;
        clearTimeout(showTimer); clearTimeout(hideTimer);
        showTimer = setTimeout(() => show(button, work), 220);
      });
      button.addEventListener('pointerleave', laterHide);
      button.addEventListener('focus', () => show(button, work));
      button.addEventListener('blur', laterHide);
      button.addEventListener('click', hide);
    },
  };
}
