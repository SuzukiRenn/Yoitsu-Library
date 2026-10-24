export function appendEditionBadge(title,work,doc=document){
 if(work.edition!=='web')return;
 const badge=doc.createElement('span');badge.className='edition-badge';badge.textContent='Web';badge.setAttribute('aria-label','Web版');title.append(badge);
}
