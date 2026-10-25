// User reduction supplements the system preference; data refresh never depends on this policy.
export function motionPreference(win=window,doc=win.document){
 const media=win.matchMedia?.('(prefers-reduced-motion: reduce)') || {matches:false};
 if(!doc?.documentElement?.dataset || !doc.addEventListener)return media;
 const handlers=new Map();
 const preference={get matches(){return doc.documentElement.dataset.motionPreference==='reduce' || media.matches;},
  addEventListener(type,listener){
   if(type!=='change' || handlers.has(listener))return;
   const notify=()=>listener({matches:preference.matches});handlers.set(listener,notify);
   media.addEventListener?.('change',notify);doc.addEventListener('booklist:preferences',notify);
  },
  removeEventListener(type,listener){
   const notify=handlers.get(listener);if(type!=='change' || !notify)return;
   media.removeEventListener?.('change',notify);doc.removeEventListener('booklist:preferences',notify);handlers.delete(listener);
  }};
 return preference;
}
