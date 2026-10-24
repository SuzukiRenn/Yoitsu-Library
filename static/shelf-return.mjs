const STORE='booklist:shelf-positions:v1', PENDING='booklist:shelf-return:v1';
const key=url=>url.pathname+url.search;
// Layout coordinates ignore hover and page-transition transforms.
const layoutTop=node=>{let top=0;for(let current=node;current;current=current.offsetParent)top+=current.offsetTop;return top;};
export function installShelfReturn(doc=document,win=window) {
  const shelf=doc.querySelector('[data-live-shelf]');
  const load=name=>{try{return JSON.parse(win.sessionStorage.getItem(name)||'null');}catch{return null;}};
  const save=(name,value)=>{try{win.sessionStorage.setItem(name,JSON.stringify(value));}catch{}};
  function remember() {
    if(!shelf)return;
    const url=new URL(win.location.href), positions=load(STORE)||{};
    const anchors=[...shelf.querySelectorAll('[data-shelf-work]')].map(card=>({id:card.dataset.shelfWork,top:layoutTop(card)-win.scrollY}))
      .filter(anchor=>anchor.top>=-100 && anchor.top<win.innerHeight).slice(0,4);
    positions[key(url)]={y:win.scrollY,anchors,at:Date.now()};
    save(STORE,Object.fromEntries(Object.entries(positions).sort((a,b)=>b[1].at-a[1].at).slice(0,20)));
  }
  function restore() {
    if(!shelf)return;
    const pending=load(PENDING),url=key(new URL(win.location.href));
    if(!pending || pending.url!==url || Date.now()-pending.at>15000)return;
    save(PENDING,null);
    const snapshot=load(STORE)?.[url];
    if(!snapshot || Date.now()-snapshot.at>86400000 || !Number.isFinite(snapshot.y))return;
    doc.documentElement.setAttribute?.('data-shelf-position-restoring','');
    win.requestAnimationFrame(()=>{
      let y=snapshot.y;
      for(const anchor of snapshot.anchors||[]) {
        if(!/^[1-9]\d*$/.test(anchor.id) || !Number.isFinite(anchor.top))continue;
        const card=shelf.querySelector(`[data-shelf-work="${anchor.id}"]`);
        if(card){y=layoutTop(card)-anchor.top;break;}
      }
      const max=Math.max(0,doc.documentElement.scrollHeight-win.innerHeight);
      win.scrollTo({top:Math.max(0,Math.min(y,max)),behavior:'instant'});
    });
  }
  function click(event) {
    const link=event.target.closest?.('a[href]');
    if(!link || link.hasAttribute('download'))return;
    const destination=new URL(link.href,win.location.href);
    if(destination.origin!==win.location.origin)return;
    if(shelf && link.closest('.public-card') && /^\/works\/[1-9]\d*\/$/.test(destination.pathname)) {
      remember();
      destination.searchParams.set('from',key(new URL(win.location.href)));
      link.href=destination.href;
    } else if(link.matches('[data-shelf-return]') && (!link.target || link.target==='_self') && event.button===0 && !event.ctrlKey && !event.metaKey && !event.shiftKey && !event.altKey) {
      save(PENDING,{url:key(destination),at:Date.now()});
    }
  }
  // Capture the source before the existing card transition chooses its destination.
  doc.addEventListener('click',click,true);
  doc.addEventListener('auxclick',click,true);
  win.addEventListener('pagehide',remember);
  win.addEventListener('pageshow',restore);
  if(doc.readyState==='complete')restore();
}
