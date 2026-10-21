export function safeNext(value, origin) {
  try {const url = new URL(value || '/', origin); return url.origin === origin && url.pathname.startsWith('/') ? url.pathname + url.search + url.hash : '/';}
  catch {return '/';}
}
export async function accountPost(url, payload = {}) {
  const tokenResponse = await fetch('/api/v1/csrf/', {credentials:'same-origin'});
  const {csrf_token} = await tokenResponse.json();
  const response = await fetch(url, {method:'POST', credentials:'same-origin',
    headers:{'Content-Type':'application/json', 'X-CSRFToken':csrf_token}, body:JSON.stringify(payload)});
  const data = await response.json();
  if (!response.ok) throw Error(data.error?.message || '请求失败');
  return data;
}
export function renderRecommenders(container, work) {
  if (!Array.isArray(work.recommenders)) return;
  const slot = container.querySelector('[data-recommenders]');
  if (!slot) return;
  const doc = slot.ownerDocument, first = work.first_recommender;
  const signature = JSON.stringify([first, work.co_recommenders]);
  if(slot.dataset.people === signature)return;
  slot.dataset.people=signature;
  const expanded=Boolean(slot.querySelector('details')?.open);
  slot.replaceChildren();
  const label = person => person.display_name;
  if (!first) return;
  const p = doc.createElement('p'); p.textContent = `首次推荐：${label(first)}${first.withdrawn ? '（已撤回）' : ''}`; slot.append(p);
  if (work.co_recommenders?.length) {
    const details=doc.createElement('details'), summary=doc.createElement('summary'), ul=doc.createElement('ul');
    details.open=expanded;
    ul.tabIndex=0;
    ul.setAttribute('aria-label','共同推荐者');
    summary.textContent=`共同推荐 · ${work.co_recommenders.length} 人`;
    for (const person of work.co_recommenders) {const li=doc.createElement('li');li.textContent=label(person);ul.append(li);}
    details.append(summary,ul);slot.append(details);
  }
}
if (typeof document !== 'undefined') {
  const unread = document.querySelector('[data-unread-count]');
  let unreadTimer, unreadStopped=false, unreadDelay=30000;
  const refreshUnread = async () => {
    if(!unread || document.hidden || unreadStopped)return;
    try {
      const response=await fetch('/api/v1/notifications/unread/',{credentials:'same-origin',cache:'no-store'});
      if(response.status===401){unreadStopped=true;return;}
      if(!response.ok)throw Error('消息暂不可用');
      const data=await response.json();unread.textContent=String(data.unread_count);unread.hidden=!data.unread_count;unreadDelay=30000;
    } catch {unreadDelay=Math.min(unreadDelay*2,120000);}
    finally {if(!document.hidden && !unreadStopped)unreadTimer=setTimeout(refreshUnread,unreadDelay);}
  };
  if(unread){
    unreadTimer=setTimeout(refreshUnread,unreadDelay);
    document.addEventListener('visibilitychange',()=>{clearTimeout(unreadTimer);if(!document.hidden)refreshUnread();});
    window.addEventListener('pagehide',()=>{unreadStopped=true;clearTimeout(unreadTimer);});
    window.addEventListener('pageshow',()=>{if(unreadStopped){unreadStopped=false;refreshUnread();}});
  }
  const next = safeNext(new URLSearchParams(location.search).get('next'), location.origin);
  const switchLink=document.querySelector('[data-auth-switch]');
  if(switchLink)switchLink.href += `?next=${encodeURIComponent(next)}`;
  for (const form of document.querySelectorAll('[data-account-form]')) form.addEventListener('submit', async event => {
    event.preventDefault(); const status=form.querySelector('[role=status]'), button=form.querySelector('button');
    const data=Object.fromEntries(new FormData(form));
    if (data.confirmation !== undefined && data.confirmation !== (data.password || data.new_password)) {status.textContent='两次输入的密码不一致';return;}
    delete data.confirmation;button.disabled=true;status.textContent='';
    try {
      await accountPost(`/api/v1/account/${form.dataset.accountForm}/`, data);
      if(form.dataset.accountForm==='password') location.assign('/account/login/');
      else if(form.dataset.accountForm==='profile') location.reload();
      else location.assign(next);
    } catch(error) {status.textContent=error.message;} finally {button.disabled=false;}
  });
  const status = document.querySelector('#action-status');
  document.addEventListener('keydown', event => {
    if(event.key!=='Escape')return;
    for(const details of document.querySelectorAll('[data-recommenders] details[open]')) {
      details.open=false;
      if(details.contains(document.activeElement))details.querySelector('summary').focus();
    }
  });
  document.addEventListener('click', async event => {
    for(const details of document.querySelectorAll('[data-recommenders] details[open]')) {
      if(!details.contains(event.target))details.open=false;
    }
    const button=event.target.closest('[data-account-logout],[data-read],[data-read-all]');
    if(!button || button.disabled)return;button.disabled=true;
    try {
      if(button.hasAttribute('data-account-logout')) {await accountPost('/api/v1/account/logout/');location.assign('/');}
      else {await accountPost(button.hasAttribute('data-read-all') ? '/api/v1/notifications/read-all/' : '/api/v1/notifications/read/', button.dataset.read ? {ids:[Number(button.dataset.read)]}:{});location.reload();}
    } catch(error) {if(status)status.textContent=error.message;} finally {button.disabled=false;}
  });
  let page=1;
  const more=document.querySelector('[data-more-notifications]');
  if(more) {
    const load=async append => {
      const response=await fetch(`/api/v1/notifications/?page=${page}`,{credentials:'same-origin',cache:'no-store'});
      if(!response.ok)throw Error('消息加载失败，请重试');
      const data=await response.json();
      if(append)for(const item of data.results) {
        const li=document.createElement('li');li.className=`notification${item.read ? '' : ' unread'}`;
        const p=document.createElement('p');p.textContent=`${item.actor.display_name} 赞了你推荐的《${item.work_title}》`;
        const note=document.createElement('p');note.className='section-note';note.textContent=`${item.activity_title || '公共书单'} · ${new Date(item.created_at).toLocaleString()}`;
        const a=document.createElement('a');a.textContent='查看作品';a.href=item.url;
        li.append(p,note,a);
        if(!item.read){const b=document.createElement('button');b.type='button';b.dataset.read=String(item.id);b.textContent='标为已读';li.append(b);}
        document.querySelector('[data-notifications]').append(li);
      }
      more.hidden=page*50>=data.total;
    };
    load(false).catch(error=>{status.textContent=error.message;more.hidden=false;});
    more.addEventListener('click',async()=>{more.disabled=true;page++;try{await load(true);}catch(error){page--;status.textContent=error.message;}finally{more.disabled=false;}});
  }
}
