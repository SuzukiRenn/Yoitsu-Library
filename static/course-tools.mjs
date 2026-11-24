const tools = document.querySelector('[data-course-tools]');
if (tools) {
  const trigger = tools.querySelector('summary');

  function closeOutside(event) {
    if (tools.open && !tools.contains(event.target)) tools.open = false;
  }

  document.addEventListener('click', closeOutside);
  document.addEventListener('focusin', closeOutside);
  document.addEventListener('keydown', event => {
    if (event.key !== 'Escape' || !tools.open) return;
    event.preventDefault();
    tools.open = false;
    trigger.focus({preventScroll: true});
  });
  window.addEventListener('pagehide', () => { tools.open = false; });
}
