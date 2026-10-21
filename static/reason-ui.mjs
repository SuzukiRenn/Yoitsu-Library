export function renderReasonButton(button, item) {
  button.dataset.reasonId = String(item.id);
  button.dataset.enabled = String(!item.liked);
  button.dataset.writable = String(item.writable !== false);
  button.setAttribute('aria-pressed', String(Boolean(item.liked)));
  button.setAttribute('aria-label', item.liked ? '取消赞同这条推荐理由' : '赞同这条推荐理由');
  button.textContent = `${item.liked ? '已赞同' : '赞同'} · ${item.like_count}`;
  button.disabled = item.writable === false || button.dataset.busy === 'true';
}
export function reasonButton(doc, item, work, activity) {
  const button = doc.createElement('button');
  button.type='button'; button.className='reason-like'; button.dataset.reasonLike='';
  button.dataset.work=String(work); button.dataset.activity=activity || '';
  renderReasonButton(button,item); return button;
}
export function renderReasonPreview(preview, item) {
  const doc = preview.ownerDocument;
  let p=preview.querySelector('p');
  preview.querySelector('[data-reason-like]')?.remove();
  preview.querySelector('.reason-popular')?.remove();
  if (!item) {p?.remove();return;}
  if (!p) {p=doc.createElement('p');preview.prepend(p);}
  if (p.textContent!==item.reason) p.textContent=item.reason;

}
export function reasonRow(doc, item, number, work, activity) {
  const row=doc.createElement('li');row.className='reason-card';row.dataset.reasonRow=String(item.id);
  const badge=doc.createElement('span');badge.className='reason-number';badge.setAttribute('aria-hidden','true');badge.textContent=String(number);
  const content=doc.createElement('div');content.className='reason-content';
  const p=doc.createElement('p');p.className='reason-text';p.textContent=item.reason;
  const body=doc.createElement('div');body.className='reason-body';
  const clip=doc.createElement('div');clip.className='reason-clip';clip.id=`reason-excerpt-${item.id}`;clip.append(p);
  const toggle=doc.createElement('button');toggle.type='button';toggle.className='reason-expand';toggle.dataset.reasonExpand='';toggle.textContent='展开全文';toggle.hidden=true;
  toggle.setAttribute('aria-expanded','false');toggle.setAttribute('aria-controls',clip.id);
  body.append(clip,toggle);
  if(item.author){const author=doc.createElement('p');author.className='reason-author';author.textContent=item.author.display_name;content.append(author);}
  content.append(body,reasonButton(doc,item,work,activity));row.append(badge,content);return row;
}
