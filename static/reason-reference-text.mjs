export function renderReasonText(node,item){
 const signature=JSON.stringify(item.parts||null);
 if(node.dataset.referenceParts===signature && node.textContent===item.reason)return;
 if(!item.parts?.length){node.textContent=item.reason;node.dataset.referenceParts=signature;return;}
 const doc=node.ownerDocument;
 node.replaceChildren(...item.parts.map(part=>{
  // Only server-produced relative work-detail links are accepted.
  if(typeof part.url==='string' && /^\/works\/[1-9]\d*\/$/.test(part.url)){
   const link=doc.createElement('a');link.className='reason-reference';link.href=part.url;link.textContent=part.text;return link;
  }
  return doc.createTextNode(part.text);
 }));
 node.dataset.referenceParts=signature;
}
