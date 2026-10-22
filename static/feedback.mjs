import {createPublicApi} from './public-api.mjs';

const form = document.querySelector('#feedback-form');
if (form) {
  const write = createPublicApi();
  const button = form.querySelector('button[type=submit]');
  const status = document.querySelector('#feedback-status');
  form.addEventListener('submit', async event => {
    event.preventDefault();
    if (button.disabled) return;
    button.disabled = true;
    status.textContent = '正在提交…';
    const fields = new FormData(form);
    const payload = Object.fromEntries(['category', 'message', 'page_url'].map(key => [key, fields.get(key)]));
    try {
      await write('/api/v1/feedback/', payload);
      form.elements.message.value = '';
      status.textContent = '已提交，管理员会核查处理。刷新本页可查看处理结果。';
      document.querySelector('[data-feedback-empty]')?.remove();
      const article = document.createElement('article');
      const heading = document.createElement('strong');
      heading.textContent = '刚刚提交 · 待处理';
      const message = document.createElement('p');
      message.className = 'feedback-text'; message.textContent = payload.message;
      article.append(heading, message);
      const history = document.querySelector('#feedback-history');
      const first = history.querySelector('article');
      if (first) first.before(article); else history.append(article);
    } catch (error) {
      status.textContent = error.message || '提交失败，请稍后重试';
    } finally { button.disabled = false; }
  });
}
