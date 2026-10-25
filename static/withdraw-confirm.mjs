// Only a server-detected final withdrawal needs a dialog; retries keep their API key.
export async function writeWithConfirmation(write,url,payload,confirm){
  for(let attempt=0;attempt<3;attempt++){
    try{return await write(url,payload);}
    catch(error){
      if(error.code!=='withdrawal_confirmation_required' || !error.confirmation?.token)throw error;
      if(!await confirm(error.confirmation.message))return null;
      payload={...payload,confirmation:error.confirmation.token};
    }
  }
  throw Error('推荐状态仍在变化，请稍后重试');
}
export function confirmWithdrawal(message,doc=document){
  if(doc.querySelector('dialog[open]'))return Promise.resolve(false);
  return new Promise(resolve=>{
    const dialog=doc.createElement('dialog');dialog.className='withdraw-confirm';
    dialog.setAttribute('aria-labelledby','withdraw-title');dialog.setAttribute('aria-describedby','withdraw-message');
    const title=doc.createElement('h2');title.id='withdraw-title';title.textContent='撤回推荐？';
    const text=doc.createElement('p');text.id='withdraw-message';text.textContent=message;
    const actions=doc.createElement('div');actions.className='form-actions';
    const cancel=doc.createElement('button');cancel.type='button';cancel.textContent='保留推荐';
    const accept=doc.createElement('button');accept.type='button';accept.className='primary';accept.textContent='确认撤回';
    actions.append(cancel,accept);dialog.append(title,text,actions);doc.body.append(dialog);
    let accepted=false;
    cancel.addEventListener('click',()=>dialog.close());
    accept.addEventListener('click',()=>{accepted=true;dialog.close();});
    const leave=()=>dialog.close();
    doc.defaultView?.addEventListener('pagehide',leave,{once:true});
    dialog.addEventListener('close',()=>{doc.defaultView?.removeEventListener('pagehide',leave);dialog.remove();resolve(accepted);},{once:true});
    if(typeof dialog.showModal!=='function'){dialog.remove();resolve(false);return;}
    dialog.showModal();cancel.focus({preventScroll:true});
  });
}
