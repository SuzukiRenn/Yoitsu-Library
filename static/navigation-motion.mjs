import {motionPreference} from './motion-preference.mjs?v=20261025-1';
// Keep real document navigation, forms and history intact; animate only the main nav.
export function installNavigationMotion(doc = document, win = window) {
  const main = doc.querySelector('main');
  if (!main || typeof main.animate !== 'function') return;
  const reduced = motionPreference(win,doc);
  let pending;
  function reset() {
    const previous = pending;
    pending = null;
    if (!previous) return;
    win.clearTimeout(previous.timer);
    previous.animation.cancel();
  }
  function navigate(next) {
    if (pending !== next || next.navigated) return;
    next.navigated = true;
    win.clearTimeout(next.timer);
    // Leave the old document readable while the destination is loading.
    win.location.assign(next.url);
  }
  doc.addEventListener('click', event => {
    if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey ||
        event.shiftKey || event.altKey || reduced.matches) return;
    const link = event.target.closest?.('.main-nav a[href], .brand[href]');
    if (!link || link.hasAttribute('download') || (link.target && link.target !== '_self')) return;
    const url = new URL(link.href, win.location.href);
    if (url.origin !== win.location.origin || url.pathname + url.search === win.location.pathname + win.location.search) return;
    event.preventDefault();
    reset();
    const next = {url: url.href};
    pending = next;
    next.animation = main.animate([{opacity: 1, transform: 'translateY(0)'},
      {opacity: .65, transform: 'translateY(-3px)'}],
      {duration: 120, easing: 'ease-out', fill: 'forwards'});
    next.animation.finished.then(() => navigate(next), () => {});
    // A browser that suppresses animation completion must never block the link.
    next.timer = win.setTimeout(() => navigate(next), 180);
  });
  win.addEventListener('pagehide', reset);
  win.addEventListener('pageshow', reset);
  reduced.addEventListener('change', () => {
    if (!reduced.matches || !pending) return;
    const url = pending.url;
    reset();
    win.location.assign(url);
  });
}

if (typeof document !== 'undefined') installNavigationMotion();
