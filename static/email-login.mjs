import {returnTo} from './navigation-context.mjs?v=20261104-rankings-1';
export function installEmailLogin(post, next, doc=document, win=window){
 const form=doc.querySelector('[data-email-login]');if(!form)return;
 const email=form.querySelector('[name=email]'),code=form.querySelector('[name=code]');
 const send=form.querySelector('[data-send-code]'),submit=form.querySelector('[type=submit]'),status=form.querySelector('[role=status]');
 let challenge='',sentEmail='',sending=false,busy=false,until=0,timer;
 const update=()=>{const seconds=Math.max(0,Math.ceil((until-Date.now())/1000));send.disabled=sending||busy||seconds>0;send.textContent=seconds?`${seconds} 秒后重发`:'获取验证码';if(!seconds){win.clearInterval(timer);timer=undefined;}};
 email.addEventListener('input',()=>{challenge='';sentEmail='';code.value='';});
 send.addEventListener('click',async()=>{
  if(send.disabled||!email.reportValidity())return;
  sending=true;update();status.textContent='正在发送…';
  const address=email.value.trim().toLowerCase();
  try{const result=await post('/api/v1/account/email/code/',{email:address,purpose:form.dataset.emailLogin});
   if(email.value.trim().toLowerCase()===address){challenge=result.challenge;sentEmail=address;status.textContent='验证码已发送，5 分钟内有效';code.focus();}
   else status.textContent='邮箱已更改，请重新获取验证码';
   until=Date.now()+result.retry_after*1000;win.clearInterval(timer);timer=win.setInterval(update,1000);
  }catch(error){status.textContent=error.message;}finally{sending=false;update();}
 });
 form.addEventListener('submit',async event=>{
  event.preventDefault();if(busy||sending)return;
  if(!challenge||sentEmail!==email.value.trim().toLowerCase()){status.textContent='请先获取当前邮箱的验证码';return;}
  busy=true;submit.disabled=true;update();status.textContent='';
  try{await post(`/api/v1/account/email/${form.dataset.emailLogin==='bind'?'bind':'login'}/`,{email:sentEmail,challenge,code:code.value.trim()});returnTo(next,win);}
  catch(error){status.textContent=error.message;}finally{busy=false;submit.disabled=false;update();}
 });
 win.addEventListener('pagehide',()=>win.clearInterval(timer));
 win.addEventListener('pageshow',()=>{win.clearInterval(timer);timer=win.setInterval(update,1000);update();});
}
