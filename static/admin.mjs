for (const form of document.querySelectorAll('form[data-api]')) {
  form.addEventListener('submit', async event => {
    event.preventDefault();
    const button = form.querySelector('button'), status = form.querySelector('[role=status]');
    if (button.disabled) return;
    button.disabled = true; status.textContent = '';
    try {
      const data = {}, choices = {};
      for (const field of form.querySelectorAll('[name]')) {
        if (field.name.startsWith('choice:')) choices[field.name.slice(7)] = field.value;
        else if (field.type === 'datetime-local') data[field.name] = field.dataset.original && field.defaultValue === field.value ? field.dataset.original : field.value + '+08:00';
        else data[field.name] = field.hasAttribute('data-integer') ? Number(field.value) : field.value;
      }
      if (form.dataset.api === '/api/v1/admin/merges/') data.choices = choices;
      const csrf = await fetch('/api/v1/csrf/', {credentials:'same-origin',cache:'no-store'});
      if (!csrf.ok) throw new Error('无法获取请求凭证');
      const token = (await csrf.json()).csrf_token;
      const response = await fetch(form.dataset.api, {method:'POST',credentials:'same-origin',
        headers:{'Content-Type':'application/json','X-CSRFToken':token},body:JSON.stringify(data)});
      const result = await response.json();
      if (!response.ok) throw new Error(result.error?.message || '操作失败');
      if (form.dataset.success) location.assign(form.dataset.success); else location.reload();
    } catch (error) { status.textContent = error.message; button.disabled = false; }
  });
}
