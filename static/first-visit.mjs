const STORAGE_KEY = 'identity_booklist.guide.seen.v1';

export function guidePosition(target, panel, viewport) {
  const gap = 14, margin = 12;
  const left = Math.max(margin, Math.min(target.left + target.width / 2 - panel.width / 2, viewport.width - panel.width - margin));
  const below = target.bottom + gap;
  const top = below + panel.height <= viewport.height - margin ? below : target.top - gap - panel.height;
  return {left, top: Math.max(margin, Math.min(top, viewport.height - panel.height - margin))};
}

export function installFirstVisit(doc = document, win = window) {
  // Wait for a shelf with a recommendation entry; never interrupt a form.
  const recommend = doc.querySelector('.shelf-recommend');
  const activities = doc.querySelector('.main-nav a[href="/activities/"]');
  if (!recommend || !activities || !win.HTMLDialogElement) return;
  const stores = [];
  for (const name of ['localStorage', 'sessionStorage']) {
    try {
      const storage = win[name];
      if (storage.getItem(STORAGE_KEY)) return;
      stores.push(storage);
    } catch { /* Browsing remains available when storage is blocked. */ }
  }
  // Avoid repeated interruptions when neither storage mechanism is available.
  if (!stores.length || doc.querySelector('.first-visit')) return;

  const dialog = doc.createElement('dialog');
  dialog.className = 'first-visit';
  dialog.setAttribute('aria-labelledby', 'guide-title');
  dialog.setAttribute('aria-describedby', 'guide-description');
  dialog.innerHTML = `<div class="guide-spotlight" aria-hidden="true"></div>
    <section class="guide-panel">
      <span class="guide-count"></span><h2 id="guide-title"></h2>
      <p id="guide-description"></p>
      <div class="guide-actions"><button type="button" class="guide-skip">跳过</button><button type="button" class="guide-next" autofocus>下一步</button></div>
    </section>`;
  doc.body.append(dialog);
  const panel = dialog.querySelector('.guide-panel');
  const spot = dialog.querySelector('.guide-spotlight');
  const next = dialog.querySelector('.guide-next');
  const previousFocus = doc.activeElement;
  const steps = [
    {target: recommend, title: '推荐一本书', text: '点「推荐」，搜索书名即可推荐。'},
    {target: activities, title: '参加推荐活动', text: '点「推荐活动」，查看并参与本期活动。'}
  ];
  let index = 0, frame, lastGeometry;
  const position = () => {
    const rect = steps[index].target.getBoundingClientRect();
    const geometry = [index, rect.left, rect.top, rect.width, rect.height, win.innerHeight, doc.documentElement.clientWidth].join(',');
    if (geometry === lastGeometry) return;
    lastGeometry = geometry;
    Object.assign(spot.style, {left: `${rect.left - 5}px`, top: `${rect.top - 5}px`, width: `${rect.width + 10}px`, height: `${rect.height + 10}px`});
    const point = guidePosition(rect, panel.getBoundingClientRect(), {width: doc.documentElement.clientWidth, height: win.innerHeight});
    Object.assign(panel.style, {left: `${point.left}px`, top: `${point.top}px`});
  };
  // Entry/hover transforms change position without resize or scroll events.
  // Follow the actual button throughout the short, modal guide.
  const track = () => { position(); frame = win.requestAnimationFrame(track); };
  const finish = () => {
    for (const storage of stores) { try { storage.setItem(STORAGE_KEY, '1'); } catch {} }
    win.cancelAnimationFrame(frame);
    dialog.close();
    dialog.remove();
    if (previousFocus?.isConnected) previousFocus.focus({preventScroll: true});
  };
  const show = () => {
    const step = steps[index];
    dialog.querySelector('.guide-count').textContent = `${index + 1} / ${steps.length}`;
    dialog.querySelector('h2').textContent = step.title;
    dialog.querySelector('p').textContent = step.text;
    next.textContent = index === steps.length - 1 ? '知道了' : '下一步';
    step.target.scrollIntoView({block: 'nearest', behavior: 'instant'});
    position();
  };
  next.addEventListener('click', () => { if (index === steps.length - 1) finish(); else { index++; show(); } });
  dialog.querySelector('.guide-skip').addEventListener('click', finish);
  dialog.addEventListener('cancel', event => { event.preventDefault(); finish(); });
  try { dialog.showModal(); } catch { dialog.remove(); return; }
  show();
  frame = win.requestAnimationFrame(track);
}

if (typeof document !== 'undefined') installFirstVisit();
