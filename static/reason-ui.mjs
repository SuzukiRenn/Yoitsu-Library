import {renderReasonText} from './reason-reference-text.mjs?v=20261024-references-1';
export function renderReasonButton(button, item) {
  button.dataset.reasonId = String(item.id);
  button.dataset.enabled = String(!item.liked);
  button.dataset.writable = String(item.writable !== false);
  button.setAttribute('aria-pressed', String(Boolean(item.liked)));
  button.setAttribute('aria-label', item.liked ? '取消赞同这条推荐理由' : '赞同这条推荐理由');
  let label=button.querySelector('[data-reason-label]'),count=button.querySelector('[data-reason-count]');
  if(!label || !count){
    label=button.ownerDocument.createElement('span');label.dataset.reasonLabel='';
    count=button.ownerDocument.createElement('span');count.dataset.reasonCount='';
    button.replaceChildren(label,count);
  }
  label.textContent=item.liked ? '已赞同' : '赞同';
  count.textContent=` · ${item.like_count}`;
  button.disabled = item.writable === false || button.dataset.busy === 'true';
}
export function reasonButton(doc, item, work, activity) {
  const button = doc.createElement('button');
  button.type='button'; button.className='reason-like'; button.dataset.reasonLike='';
  button.dataset.work=String(work); button.dataset.activity=activity || '';
  renderReasonButton(button,item); return button;
}
// A spoiler placeholder is replaced in place only after the reader clicks it.
export function reasonContent(doc, item, preview=false) {
  const p=doc.createElement('p');p.className='reason-text';renderReasonText(p,item);
  if(item.contains_spoilers) {
    const wrapper=doc.createElement('div');wrapper.className=preview?'spoiler-preview':'spoiler-reason';
    const control=doc.createElement('span');control.className='recommend-control spoiler-control';
    const trigger=doc.createElement('button');trigger.type='button';trigger.className='spoiler-trigger';trigger.textContent='含剧透';trigger.dataset.spoilerReveal='';
    trigger.setAttribute('aria-describedby',`spoiler-tip-${item.id}`);trigger.setAttribute('aria-controls',`spoiler-text-${item.id}`);
    const tip=doc.createElement('span');tip.className='control-tooltip';tip.id=`spoiler-tip-${item.id}`;tip.setAttribute('role','tooltip');tip.textContent='查看剧透';
    p.id=`spoiler-text-${item.id}`;p.dataset.spoilerText='';p.hidden=true;p.setAttribute('tabindex','-1');
    control.append(trigger,tip);wrapper.append(control,p);return wrapper;
  }
  if(preview)return p;
  const body=doc.createElement('div');body.className='reason-body';
  const clip=doc.createElement('div');clip.className='reason-clip';clip.id=`reason-excerpt-${item.id}`;clip.append(p);
  const toggle=doc.createElement('button');toggle.type='button';toggle.className='reason-expand';toggle.dataset.reasonExpand='';toggle.textContent='展开全文';toggle.hidden=true;
  toggle.setAttribute('aria-expanded','false');toggle.setAttribute('aria-controls',clip.id);
  body.append(clip,toggle);return body;
}
export function renderReasonPreview(preview, item) {
  const current=preview.querySelector('.spoiler-preview') || preview.querySelector('p');
  preview.querySelector('[data-reason-like]')?.remove();
  preview.querySelector('.reason-popular')?.remove();
  if(!item){current?.remove();return;}
  const text=current?.querySelector?.('p') || current;
  // Polls preserve explicit expansion only while identity, text and flag agree.
  if(current && preview.dataset.previewReasonId===String(item.id) && text.textContent===item.reason && Boolean(preview.querySelector('.spoiler-preview'))===Boolean(item.contains_spoilers)){renderReasonText(text,item);return;}
  const next=reasonContent(preview.ownerDocument,item,true);
  if(current)current.replaceWith(next);else preview.prepend(next);
  preview.dataset.previewReasonId=String(item.id);
}
export function updateReasonContent(row,item) {
  const content=row.querySelector('.reason-content');
  const current=content.querySelector('.spoiler-reason') || content.querySelector('.reason-body');
  const text=current?.querySelector('.reason-text');
  if(text?.textContent===item.reason && Boolean(content.querySelector('.spoiler-reason'))===Boolean(item.contains_spoilers)){renderReasonText(text,item);return;}
  const next=reasonContent(row.ownerDocument,item);
  if(current)current.replaceWith(next);else content.prepend(next);
}
export function reasonRow(doc, item, work, activity) {
  const row=doc.createElement('li');row.className='reason-card';row.dataset.reasonRow=String(item.id);
  const content=doc.createElement('div');content.className='reason-content';
  if(item.author){const author=doc.createElement('p');author.className='reason-author';author.textContent=item.author.display_name;content.append(author);}
  content.append(reasonContent(doc,item),reasonButton(doc,item,work,activity));row.append(content);return row;
}
