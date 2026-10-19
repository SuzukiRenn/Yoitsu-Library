import {createPublicApi} from './public-api.mjs';
import {createSearchController} from './catalog-search.mjs';
import {normalize} from './text.mjs';

const write = createPublicApi();
const dialog = document.querySelector('#recommend-dialog');
const form = document.querySelector('#recommend-form');
const query = document.querySelector('#recommend-query');
const reason = document.querySelector('#recommend-reason');
const candidates = document.querySelector('#candidates');
const status = document.querySelector('#recommend-status');
const submit = document.querySelector('#recommend-submit');
const distinct = document.querySelector('#distinct-work');
const distinctOption = document.querySelector('#distinct-option');
const actionStatus = document.querySelector('#action-status');
let selected = null, editing = null, timer = null, composing = false, busy = false;

function resetSelection() {
  selected = null; distinct.checked = false; submit.textContent = '推荐';
  for (const button of candidates.querySelectorAll('button')) button.setAttribute('aria-pressed', 'false');
}
const search = createSearchController({fetcher: fetch,
  onStatus: text => { document.querySelector('#candidate-status').textContent = text; },
  onResult: payload => {
    candidates.replaceChildren();
    distinctOption.hidden = !payload.results.length;
    for (const work of payload.results) {
      const button = document.createElement('button');
      button.type = 'button'; button.className = 'candidate'; button.setAttribute('aria-pressed', 'false');
      const title = document.createElement('strong'); title.textContent = work.title;
      const author = document.createElement('span'); author.textContent = work.author;
      const label = document.createElement('span'); label.textContent = work.is_public ? '+1' : '推荐';
      button.append(title, author, label);
      button.addEventListener('click', () => {
        resetSelection(); selected = work; button.setAttribute('aria-pressed', 'true');
        submit.textContent = work.is_public ? '+1' : '推荐';
      });
      candidates.append(button);
    }
  }});

function cancelSearch() { clearTimeout(timer); search.cancel(); }
function schedule() {
  cancelSearch(); resetSelection(); candidates.replaceChildren(); distinctOption.hidden = true;
  document.querySelector('#candidate-status').textContent = '';
  if (!composing && query.value.trim()) timer = setTimeout(() => search.run(query.value), 300);
}
query.addEventListener('input', schedule);
query.addEventListener('compositionstart', () => { composing = true; cancelSearch(); });
query.addEventListener('compositionend', () => { composing = false; schedule(); });
distinct.addEventListener('change', () => { if (distinct.checked) { selected = null; submit.textContent = '推荐'; for (const button of candidates.querySelectorAll('button')) button.setAttribute('aria-pressed', 'false'); } });
reason.addEventListener('input', () => { document.querySelector('#reason-length').textContent = `${Array.from(reason.value.replace(/\r\n?/g, '\n').trim()).length} / 200`; });

function open(button, edit = false) {
  cancelSearch(); form.reset(); resetSelection(); candidates.replaceChildren(); distinctOption.hidden = true;
  status.textContent = ''; document.querySelector('#candidate-status').textContent = '';
  document.querySelector('#reason-length').textContent = '0 / 200';
  editing = edit ? button.dataset.workId : null;
  query.disabled = edit;
  query.value = button.dataset.title || '';
  reason.value = button.dataset.reason || '';
  document.querySelector('#reason-length').textContent = `${Array.from(reason.value).length} / 200`;
  document.querySelector('#recommend-title').textContent = edit ? '理由' : '推荐';
  if (button.dataset.workId) selected = {id: Number(button.dataset.workId)};
  submit.textContent = edit ? '保存' : '推荐';
  dialog.showModal();
  (edit ? reason : query).focus();
}
document.addEventListener('click', async event => {
  const openButton = event.target.closest('[data-open-recommend], [data-edit-reason]');
  if (openButton) { open(openButton, openButton.hasAttribute('data-edit-reason')); return; }
  const button = event.target.closest('[data-action]');
  if (!button || button.disabled) return;
  const container = button.closest('[data-work]');
  const buttons = [...container.querySelectorAll('button')];
  buttons.forEach(item => { item.disabled = true; }); actionStatus.textContent = '';
  try {
    await write(`/api/v1/public/works/${container.dataset.work}/${button.dataset.action}/`, {enabled: button.dataset.enabled === 'true'});
    location.reload();
  } catch (error) {
    actionStatus.textContent = error.message; buttons.forEach(item => { item.disabled = false; });
  }
});
document.querySelector('[data-close-dialog]').addEventListener('click', () => { if (!busy) dialog.close(); });
dialog.addEventListener('cancel', event => { if (busy) event.preventDefault(); });
dialog.addEventListener('close', cancelSearch);
form.addEventListener('submit', async event => {
  event.preventDefault(); if (busy) return;
  let title, text;
  try { title = normalize(query.value, 'title', true); text = normalize(reason.value, 'reason'); }
  catch { status.textContent = '书名限 1—300 字，理由限 200 字'; return; }
  if (!editing && !selected && candidates.childElementCount && !distinct.checked) {
    status.textContent = '请选择作品，或确认同名不同作品'; return;
  }
  busy = true; submit.disabled = true; query.readOnly = true; reason.readOnly = true; status.textContent = '';
  cancelSearch();
  try {
    const result = editing
      ? await write(`/api/v1/public/works/${editing}/reason/`, {reason: text})
      : await write('/api/v1/public/recommendations/', {work_id: selected?.id ?? null, title, reason: text, confirmed_unmatched: distinct.checked});
    if (result.status === 'pending') { status.textContent = '待核查'; submit.hidden = true; }
    else location.assign(editing ? location.href : '/');
  } catch (error) {
    status.textContent = error.message;
    if (error.code === 'existing_candidate') { resetSelection(); await search.run(query.value); }
  } finally { busy = false; submit.disabled = false; query.readOnly = false; reason.readOnly = false; }
});
dialog.addEventListener('close', () => { submit.hidden = false; });

const more = document.querySelector('#more-reasons');
more?.addEventListener('click', async () => {
  more.disabled = true;
  try {
    const section = document.querySelector('[data-reasons-work]');
    const page = Number(more.dataset.page) + 1;
    const response = await fetch(`/api/v1/public/works/${section.dataset.reasonsWork}/reasons/?page=${page}&page_size=2`);
    const payload = await response.json(); if (!response.ok) throw Error(payload.error?.message || '请求失败');
    for (const item of payload.results) { const p = document.createElement('p'); p.className = 'reason-text'; p.textContent = item.reason; document.querySelector('#detail-reasons').append(p); }
    more.dataset.page = String(page); more.hidden = page * 2 >= payload.total;
  } catch (error) { actionStatus.textContent = error.message; }
  finally { more.disabled = false; }
});
