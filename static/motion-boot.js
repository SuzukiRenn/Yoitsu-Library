// Native return matching must run before the first rendered frame, including BFCache.
window.addEventListener('pagereveal', event => {
  if (!event.viewTransition) return;
  document.documentElement.classList.add('native-page-transition');
  if (document.documentElement.dataset?.motion==='reduce' || window.matchMedia('(prefers-reduced-motion: reduce)').matches) {event.viewTransition.skipTransition?.();return;}
  const previous = window.navigation?.activation?.from?.url;
  if (!previous) return;
  const url = new URL(previous, window.location.href);
  const match = url.origin === window.location.origin && url.pathname.match(/^\/works\/([1-9]\d*)\/$/);
  if (!match) return;
  const card = document.querySelector(`.public-card[data-shelf-work="${match[1]}"]`);
  if (!card?.isConnected) return;
  card.classList.add('work-opening');
  const rect = card.getBoundingClientRect();
  let restoring = document.documentElement.hasAttribute?.('data-shelf-position-restoring');
  try {
    const pending = JSON.parse(window.sessionStorage.getItem('booklist:shelf-return:v1') || 'null');
    restoring ||= pending?.url === window.location.pathname + window.location.search && Date.now() - pending.at < 15000;
  } catch {}
  const traversing = window.navigation?.activation?.navigationType === 'traverse';
  if (!restoring && !traversing && (rect.bottom <= 0 || rect.top >= window.innerHeight)) {
    card.scrollIntoView({block:'nearest',behavior:'instant'});
  }
  const cleanup = () => card.classList.remove('work-opening');
  event.viewTransition.finished.then(cleanup,cleanup);
});

window.addEventListener('pageswap',event=>{if(document.documentElement.dataset?.motion==='reduce')event.viewTransition?.skipTransition?.();});
