// Keep the native form control as a no-JavaScript fallback and submission value.
const enhancedSizes = new WeakSet();
export function initializePageSizes(scope = document) {
for (const select of scope.querySelectorAll('.page-size-form select')) {
  if (enhancedSizes.has(select)) continue;
  enhancedSizes.add(select);
  const root = document.createElement('span');
  root.className = 'page-size-picker';
  select.after(root);
  const trigger = document.createElement('button');
  trigger.type = 'button';
  trigger.className = 'page-size-trigger';
  trigger.setAttribute('role', 'combobox');
  trigger.setAttribute('aria-label', '每页作品数量');
  trigger.setAttribute('aria-haspopup', 'listbox');
  trigger.setAttribute('aria-expanded', 'false');
  const list = document.createElement('span');
  list.className = 'page-size-options';
  list.id = 'page-size-options';
  list.setAttribute('role', 'listbox');
  list.setAttribute('aria-label', '每页作品数量');
  trigger.setAttribute('aria-controls', list.id);
  list.hidden = true;
  root.append(trigger, list);
  const options = [...select.options];
  let active = select.selectedIndex;
  function highlight(index) {
    active = index;
    [...list.children].forEach((item, i) => item.classList.toggle('is-active', i === active));
    trigger.setAttribute('aria-activedescendant', list.children[active].id);
  }
  function close() {
    list.hidden = true;
    trigger.setAttribute('aria-expanded', 'false');
    trigger.removeAttribute('aria-activedescendant');
  }
  function open(index = select.selectedIndex) {
    list.hidden = false;
    trigger.setAttribute('aria-expanded', 'true');
    highlight(index);
  }
  function choose(index) {
    select.selectedIndex = index;
    trigger.textContent = options[index].textContent;
    [...list.children].forEach((item, i) => item.setAttribute('aria-selected', String(i === index)));
    select.dispatchEvent(new Event('change', {bubbles: true}));
    close();
    trigger.focus({preventScroll: true});
  }
  options.forEach((option, index) => {
    const item = document.createElement('span');
    item.id = `page-size-option-${index}`;
    item.setAttribute('role', 'option');
    item.setAttribute('aria-selected', String(option.selected));
    item.textContent = option.textContent;
    item.addEventListener('pointerdown', event => event.preventDefault());
    item.addEventListener('pointermove', () => highlight(index));
    item.addEventListener('click', () => choose(index));
    list.append(item);
  });
  trigger.textContent = options[select.selectedIndex].textContent;
  trigger.addEventListener('click', () => list.hidden ? open() : close());
  trigger.addEventListener('keydown', event => {
    if (['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) {
      event.preventDefault();
      if (list.hidden) open();
      else highlight(event.key === 'Home' ? 0 : event.key === 'End' ? options.length - 1 :
        (active + (event.key === 'ArrowDown' ? 1 : -1) + options.length) % options.length);
    } else if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      if (list.hidden) open(); else choose(active);
    } else if (event.key === 'Escape') {
      event.preventDefault(); close();
    } else if (event.key === 'Tab') close();
  });
  trigger.addEventListener('blur', close);
  document.addEventListener('pointerdown', event => { if (!root.contains(event.target)) close(); });
  select.hidden = true;
  select.labels[0]?.addEventListener('click', event => { event.preventDefault(); trigger.focus(); });
}

}
if (typeof document !== 'undefined') initializePageSizes();
