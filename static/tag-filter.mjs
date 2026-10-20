// Progressive enhancement: without JS every label remains available for GET filtering.
const tagFilters = new WeakMap();
export function initializeTagFilters(scope = document) {
for (const form of scope.querySelectorAll('[data-tag-filter]')) {
  if (tagFilters.has(form)) continue;
  const list = form.querySelector('#filter-tags');
  let labels = [...list.querySelectorAll('[data-tag-name]')];
  const selected = form.querySelector('[data-selected-tags]');
  const selectedSection = selected.parentElement;
  const toggle = form.querySelector('[data-expand-tags]');
  const searchSection = form.querySelector('.tag-filter-search');
  const search = searchSection.querySelector('input');
  const empty = form.querySelector('.tag-filter-empty');
  let expanded = false;
  const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)');
  const animations = new Set();
  const ghosts = new Set();
  const caption = selectedSection.querySelector('.tag-filter-caption');
  const controls = [...form.querySelector('.tag-filter-actions').children];
  let targets = [searchSection, caption, empty, ...labels, ...controls];
  const key = value => value.normalize('NFKC').toLocaleLowerCase().trim();

  function render() {
    const focused = document.activeElement;
    // Move original labels rather than cloning inputs: selections submit exactly once.
    for (const destination of [selected, list]) {
      const desired = labels.filter(label => label.querySelector('input').checked === (destination === selected));
      desired.forEach((label, index) => {
        if (destination.children[index] !== label) destination.insertBefore(label, destination.children[index] || null);
        label.hidden = false;
      });
    }
    selectedSection.hidden = !selected.children.length;
    searchSection.hidden = !expanded;
    const available = labels.filter(label => label.parentElement === list);
    if (expanded) {
      const query = key(search.value);
      for (const label of available) label.hidden = !key(label.dataset.tagName).includes(query);
    } else {
      // Measure actual wrapped rows, including responsive widths and translated names.
      const rows = [...new Set(available.map(label => label.offsetTop))].sort((a, b) => a - b);
      const lastTop = rows[1] ?? rows[0];
      const extra = available.filter(label => label.offsetTop > lastTop);
      for (const label of extra) label.hidden = true;
    }
    toggle.hidden = !expanded && !available.length;
    toggle.textContent = expanded ? '收起标签' : '展开全部';
    toggle.setAttribute('aria-expanded', String(expanded));
    empty.hidden = !expanded || available.some(label => !label.hidden) || !search.value.trim();
    if (form.contains(focused) && document.activeElement !== focused) focused.focus({preventScroll: true});
  }
  function stopMotion() {
    for (const animation of animations) animation.cancel();
    animations.clear();
    for (const ghost of ghosts) ghost.remove();
    ghosts.clear();
  }
  function play(element, frames, options, cleanup = () => {}) {
    const animation = element.animate(frames, options);
    animations.add(animation);
    animation.finished.then(() => { animations.delete(animation); cleanup(); },
      () => { animations.delete(animation); cleanup(); });
  }
  function visibleRects() {
    return new Map(targets.filter(element => element.getClientRects().length)
      .map(element => [element, element.getBoundingClientRect()]));
  }
  function update(change = () => {}, animate = true) {
    // Capture the current visual positions before cancelling an interrupted motion.
    // Each label then travels from its old position to its new layout position.
    const before = visibleRects();
    const previousHeight = form.getBoundingClientRect().height;
    stopMotion();
    change();
    render();
    if (!animate || reducedMotion.matches || typeof form.animate !== 'function') return;
    const after = visibleRects();
    const formRect = form.getBoundingClientRect();
    const timing = {duration: 280, easing: 'cubic-bezier(.22,.7,.25,1)'};
    if (Math.abs(previousHeight - formRect.height) > .5) {
      play(form, [{height: `${previousHeight}px`, overflow: 'hidden'},
        {height: `${formRect.height}px`, overflow: 'hidden'}], timing);
    }
    for (const [element, rect] of after) {
      const old = before.get(element);
      if (old) {
        const x = old.left - rect.left, y = old.top - rect.top;
        if (Math.abs(x) + Math.abs(y) > .5) {
          play(element, [{transform: `translate(${x}px,${y}px)`}, {transform: 'translate(0,0)'}], timing);
        }
      } else {
        play(element, [{opacity: 0, transform: 'translateY(-4px)'},
          {opacity: 1, transform: 'translateY(0)'}], {...timing, duration: 200});
      }
    }
    // A disappearing search area remains as an inert visual copy long enough to
    // fade out. The real controls immediately leave keyboard and form submission.
    for (const [element, rect] of before) {
      if (after.has(element)) continue;
      const ghost = element.cloneNode(true);
      ghost.hidden = false;
      ghost.classList.add('tag-filter-ghost');
      ghost.setAttribute('aria-hidden', 'true');
      ghost.inert = true;
      for (const node of [ghost, ...ghost.querySelectorAll('*')]) {
        node.removeAttribute('id'); node.removeAttribute('name'); node.removeAttribute('for');
        if ('disabled' in node) node.disabled = true;
      }
      Object.assign(ghost.style, {left: `${rect.left - formRect.left - form.clientLeft}px`,
        top: `${rect.top - formRect.top - form.clientTop}px`, width: `${rect.width}px`,
        height: `${rect.height}px`, margin: '0'});
      form.append(ghost); ghosts.add(ghost);
      play(ghost, [{opacity: 1}, {opacity: 0, transform: 'translateY(-5px)'}],
        {duration: 120, easing: 'ease-out', fill: 'forwards'}, () => {ghost.remove(); ghosts.delete(ghost);});
    }
  }
  toggle.addEventListener('click', () => {
    update(() => {expanded = !expanded; if (!expanded) search.value = '';});
    if (expanded) search.focus({preventScroll: true});
  });
  reducedMotion.addEventListener('change', () => {
    if (reducedMotion.matches) {
      stopMotion();
    }
  });
  search.addEventListener('input', () => update());
  form.addEventListener('change', () => update());
  // Height changes are caused by our own layout; only width changes need remeasurement.
  let width;
  const observer = new ResizeObserver(entries => {
    const next = entries[0].contentRect.width;
    if (next !== width) { width = next; update(() => {}, false); }
  });
  observer.observe(form);
  document.fonts?.ready.then(() => update(() => {}, false));
  tagFilters.set(form, source => update(() => {
    const existing = new Map(labels.map(label => [label.querySelector('input').value, label]));
    const next = [];
    for (const sourceLabel of source.querySelectorAll('[data-tag-name]')) {
      const id = sourceLabel.querySelector('input').value;
      const label = existing.get(id) || document.importNode(sourceLabel, true);
      if (existing.has(id)) {
        const input = label.querySelector('input');
        label.dataset.tagName = sourceLabel.dataset.tagName;
        label.replaceChildren(input, document.createTextNode(sourceLabel.dataset.tagName+' '),
          document.importNode(sourceLabel.querySelector('.tag-count'), true));
      } else list.append(label);
      next.push(label); existing.delete(id);
    }
    for (const label of existing.values()) {
      if (label.querySelector('input').checked) {
        label.querySelector('.tag-count').textContent = '0'; next.push(label);
      } else label.remove();
    }
    labels = next;
    targets = [searchSection, caption, empty, ...labels, ...controls];
  }, false));
  render();
}
}
export function updateTagFilter(form, source) {tagFilters.get(form)?.(source);}
if (typeof document !== 'undefined') initializeTagFilters();
