import {createPublicApi} from './public-api.mjs?v=20261021-identity1';
import {readMetadata} from './manual-metadata.mjs?v=20261020-5';
const form = document.querySelector('#metadata-form');
if (form) {
  const write = createPublicApi();
  const status = document.querySelector('#metadata-status');
  let busy = false;
  form.addEventListener('submit', async event => {
    event.preventDefault(); if (busy) return;
    let metadata;
    try { metadata = readMetadata(form); }
    catch (error) { status.textContent = error.message || '简介最多 2000 字'; return; }
    busy = true;
    const controls = [...form.querySelectorAll('input, textarea, button')];
    controls.forEach(control => { control.disabled = true; });
    status.textContent = '';
    try {
      await write(`/api/v1/catalog/works/${form.dataset.work}/metadata/`, {...metadata, revision: Number(form.dataset.revision)});
      location.assign(`/works/${form.dataset.work}/`);
    } catch (error) { status.textContent = error.message; }
    finally { busy = false; controls.forEach(control => { control.disabled = false; }); }
  });
}
