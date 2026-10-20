// Native cross-document shared elements: leave links and document loading untouched.
export function installWorkMotion(doc = document, win = window) {
  const reduced = win.matchMedia('(prefers-reduced-motion: reduce)');
  let selected = null;
  let activeCard = null;
  const clear = () => {
    activeCard?.classList.remove('work-opening');
    activeCard = null;
    selected = null;
  };
  doc.addEventListener('click', event => {
    if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey ||
        event.shiftKey || event.altKey || reduced.matches) return;
    selected = null;
    const link = event.target.closest?.('.public-card-main a[href]');
    if (!link || link.hasAttribute('download') || (link.target && link.target !== '_self')) return;
    const url = new URL(link.href, win.location.href);
    if (url.origin !== win.location.origin || !/^\/works\/[1-9]\d*\/$/.test(url.pathname) || url.hash) return;
    const card = link.closest('.public-card');
    if (card) selected = {card, url: url.href};
  });
  win.addEventListener('pageswap', event => {
    if (!event.viewTransition || reduced.matches || !selected?.card.isConnected) return;
    // Never reuse a stale click for another navigation, including failed loads/history.
    if (event.activation?.entry?.url !== selected.url) return;
    const card = selected.card;
    activeCard = card;
    card.classList.add('work-opening');
    const cleanup = () => {
      card.classList.remove('work-opening');
      if (activeCard === card) clear();
    };
    // Clean names once the old snapshot is captured, also when retained in BFCache.
    event.viewTransition.finished.then(cleanup, cleanup);
  });
  win.addEventListener('pageshow', clear);
  reduced.addEventListener('change', () => {if (reduced.matches) clear();});
}

if (typeof document !== 'undefined') installWorkMotion();
