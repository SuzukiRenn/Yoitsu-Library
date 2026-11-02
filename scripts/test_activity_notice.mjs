import assert from 'node:assert/strict';
import {validState,guestSeen,rememberGuest,installActivityNotice} from '../static/activity-notice.mjs';
const tick=()=>new Promise(resolve=>setImmediate(resolve));
function fixture({key='3',viewing=false,ids=[2,1],seen=[],blocked=false}={}) {
  const listeners={},events={},calls=[],timers=new Map(),values=new Map();let counter=0;
  const store={getItem:key=>values.get(key)||null,setItem:(key,value)=>values.set(key,value)};
  const win={localStorage:store,sessionStorage:store,addEventListener:(name,fn)=>events[name]=fn,
    setTimeout:fn=>{timers.set(++counter,fn);return counter;},clearTimeout:id=>timers.delete(id),
    async fetch(url,options){calls.push({url,options});if(blocked)throw Error('offline');
      if(url==='/api/v1/csrf/')return {ok:true,json:async()=>({csrf_token:'synthetic-token'})};
      if(options.method==='POST')for(const id of JSON.parse(options.body).ids)seen.push(id);
      return {ok:true,json:async()=>({account_key:key,activity_ids:ids,unseen_ids:ids.filter(id=>!seen.includes(id))})};
    }};
  const hint={hidden:true,style:{setProperty(){}},getBoundingClientRect:()=>({left:200,width:80})},
    entry={dataset:{activitySection:String(viewing)},getBoundingClientRect:()=>({left:210,width:60})};
  const seed={textContent:JSON.stringify({account_key:key,activity_ids:[...ids],unseen_ids:ids.filter(id=>!seen.includes(id))})};
  const doc={hidden:false,querySelector:selector=>({'[data-activity-notice]':hint,'[data-activity-entry]':entry,'#activity-notice-state':seed}[selector]),
    addEventListener:(name,fn)=>listeners[name]=fn};
  const controller=installActivityNotice(doc,win);
  return {hint,entry,doc,win,calls,events,listeners,timers,controller,ids,seen};
}
assert.equal(validState({account_key:'guest',activity_ids:[1],unseen_ids:[1]}),true);
for(const bad of [{},{account_key:'guest',activity_ids:[-1],unseen_ids:[]},
  {account_key:'3',activity_ids:[1],unseen_ids:[2]}])assert.equal(validState(bad),false);
let f=fixture();await tick();assert.equal(f.hint.hidden,false);assert.equal(f.calls.length,1);assert.equal(f.timers.size,1);
assert.equal(f.calls.some(call=>call.options.method==='POST'),false,'No acknowledgement on public shelf or click');
f.doc.hidden=true;f.listeners.visibilitychange();assert.equal(f.hint.hidden,true);assert.equal(f.timers.size,0);
f.doc.hidden=false;f.listeners.visibilitychange();await tick();assert.equal(f.hint.hidden,false);assert.equal(f.timers.size,1);
f.events.pagehide();assert.equal(f.timers.size,0);await f.controller.refresh();assert.equal(f.timers.size,0);
f=fixture({viewing:true});await tick();assert.equal(f.hint.hidden,true);
assert.equal(f.calls.filter(call=>call.options.method==='POST').length,1);
assert.deepEqual(JSON.parse(f.calls.at(-1).options.body),{ids:[2,1],account_id:3});
await f.controller.refresh();assert.equal(f.calls.filter(call=>call.options.method==='POST').length,1,'Poll never acknowledges new publications');
f.ids.unshift(4);await f.controller.refresh();assert.equal(f.hint.hidden,false);
f=fixture({key:'guest',viewing:true});await tick();assert.equal(f.hint.hidden,true);assert.equal(guestSeen(f.win).size,2);
assert.equal(f.calls.some(call=>call.options.method==='POST'),false);
f=fixture({ids:[]});await tick();assert.equal(f.hint.hidden,true);
f=fixture({blocked:true});await tick();assert.equal(f.hint.hidden,false);assert.equal(f.calls.length,1);assert.equal(f.timers.size,1);
const blocked={get localStorage(){throw Error('blocked');},get sessionStorage(){throw Error('blocked');}};
assert.equal(guestSeen(blocked).size,0);rememberGuest(blocked,[1]);
f=fixture();await tick();f.win.fetch=async()=>({ok:true,json:async()=>({account_key:'4',activity_ids:[2,1],unseen_ids:[2,1]})});
await f.controller.refresh();assert.equal(f.hint.hidden,true);assert.equal(f.timers.size,0,'Do not acknowledge another account in stale tab');
console.log('Activity cue: first visit, per-account isolation, successful entry, new publication, hidden tabs, storage and offline recovery passed.');
