// Native cross-document shared elements: leave links and document loading untouched.
export function installWorkMotion(doc = document, win = window) {
  const reduced = win.matchMedia('(prefers-reduced-motion: reduce)');
  const native = 'CSSViewTransitionRule' in win && 'onpageswap' in win && 'onpagereveal' in win;
  let fallback = null;
  let selected = null;
  let activeCard = null;
  const resetFallback = () => {
    const previous = fallback;
    fallback = null;
    if (!previous) return;
    win.clearTimeout(previous.timer);
    previous.animation.cancel();
  };
  const navigateFallback = next => {
    if (fallback !== next || next.navigated) return;
    next.navigated = true;
    win.clearTimeout(next.timer);
    win.location.assign(next.url);
  };
  const animateFallback = (event, surface, url, returning=false) => {
    if (!surface || typeof surface.animate !== 'function') return;
    resetFallback();
    let animation;
    try {
      animation = surface.animate([
        {transform:'translateY(0) scale(1)',opacity:1},
        {transform:returning ? 'translateY(6px) scale(.97)' : 'translateY(-4px) scale(1.04)',opacity:.92}
      ], {duration:170,easing:'cubic-bezier(.2,.7,.3,1)',fill:'forwards'});
    } catch { return; }
    event.preventDefault();
    const next = {url,animation};
    fallback = next;
    animation.finished.then(()=>navigateFallback(next),()=>{});
    next.timer = win.setTimeout(()=>navigateFallback(next),230);
  };
  const clear = () => {
    activeCard?.classList.remove('work-opening');
    activeCard = null;
    selected = null;
  };
  doc.addEventListener('click', event => {
    if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey ||
        event.shiftKey || event.altKey || reduced.matches) return;
    selected = null;
    const link = event.target.closest?.('.public-card-main a[href], .work-detail .back-link[href]');
    if (!link || link.hasAttribute('download') || (link.target && link.target !== '_self')) return;
    const url = new URL(link.href, win.location.href);
    if (url.origin !== win.location.origin) return;
    if (!native && link.matches?.('.work-detail .back-link')) {
      if (url.pathname === '/' || /^\/activities\/[1-9]\d*\/$/.test(url.pathname)) {
        animateFallback(event,link.closest('.work-detail'),url.href,true);
      }
      return;
    }
    if (!/^\/works\/[1-9]\d*\/$/.test(url.pathname) || url.hash) return;
    const card = link.closest('.public-card');
    if (card) {
      selected = {card, url: url.href};
      if (!native) animateFallback(event,card,url.href);
    }
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
  win.addEventListener('pageshow', () => {resetFallback(); clear();});
  win.addEventListener('pagehide',resetFallback);
  reduced.addEventListener('change', () => {
    if (!reduced.matches) return;
    const url = fallback?.url;
    resetFallback(); clear();
    if (url) win.location.assign(url);
  });
}

if (typeof document !== 'undefined') installWorkMotion();
