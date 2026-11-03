import assert from 'node:assert/strict';
import {localReturn,returnTo,installNavigationContext} from '../static/navigation-context.mjs';
const origin='http://local';
for(const bad of ['https://evil.test/','//evil.test/','/\\evil.test/','/%5cevil.test/','javascript:alert(1)','/api/v1/csrf/','/manage/','/random-book/','/?q=%0aInjected','/account/login/'])assert.equal(localReturn(bad,origin),'/',bad);
assert.equal(localReturn('/works/7/?from=%2F%3Ftag%3D9#reasons',origin),'/works/7/?from=%2F%3Ftag%3D9#reasons');
const storage=new Map();
function fixture(href,y=700,{blocked=false,back=false}={}) {
 const events={},winEvents={},frames=[],scrolls=[],assigns=[],timers=new Map();let serial=0;
 const doc={readyState:'complete',documentElement:{scrollHeight:3000,setAttribute(){}},addEventListener:(n,f)=>events[n]=f};
 const win={location:Object.assign(new URL(href),{assign:u=>assigns.push(u)}),scrollY:y,innerHeight:600,
  performance:{getEntriesByType:()=>[{type:back?'back_forward':'navigate'}]},
  sessionStorage:{getItem:k=>{if(blocked)throw Error();return storage.get(k);},setItem:(k,v)=>{if(blocked)throw Error();storage.set(k,v);}},
  setTimeout:f=>{timers.set(++serial,f);return serial;},clearTimeout:id=>timers.delete(id),
  addEventListener:(n,f)=>winEvents[n]=f,requestAnimationFrame:f=>frames.push(f),scrollTo:x=>{scrolls.push(x);win.scrollY=x.top;}};
 installNavigationContext(doc,win);
 function click(href,attrs=[],flags={}) {
  const link={href,target:'',hasAttribute:k=>attrs.includes(k)};
  events.click({target:{closest:selector=>selector==='[data-back-to-top]'?null:link},button:0,...flags});return new URL(link.href);
 }
 return {win,click,winEvents,frames,scrolls,assigns,settle:()=>{const callbacks=[...timers.values()];timers.clear();callbacks.forEach(f=>f());}};
}
const source='http://local/activities/3/?q=小说&page=2&tag=9#reasons';
let a=fixture(source,1234);
const login=a.click(origin+'/account/login/',['data-context-login']);assert.equal(login.searchParams.get('next'),new URL(source).pathname+new URL(source).search+'#reasons');
let auth=fixture(login.href);returnTo(login.searchParams.get('next'),auth.win);
a=fixture(source,0);a.frames.forEach(f=>f());assert.equal(a.scrolls[0].top,1234);assert.equal(a.scrolls[0].behavior,'instant');
const inbox=a.click(origin+'/notifications/',['data-context-notifications']);
assert.equal(inbox.searchParams.get('from'),new URL(source).pathname+new URL(source).search+'#reasons');
let n=fixture(inbox.href,450);n.click(source,['data-context-return']);
a=fixture(source,0);a.frames.forEach(f=>f());assert.equal(a.scrolls[0].top,1234);
// Leaving through an editor captures the full detail chain, without preventing native navigation.
const detail='http://local/works/7/?from=%2F%3Fpage%3D3&browse=token#reasons';
let d=fixture(detail,800);const editor=d.click(origin+'/recommend/?work=7&edit=1');assert.equal(editor.searchParams.get('from'),new URL(detail).pathname+new URL(detail).search+'#reasons');
let e=fixture(editor.href);returnTo(editor.searchParams.get('from'),e.win);
d=fixture(detail,0);d.frames.forEach(f=>f());assert.equal(d.scrolls[0].top,800);
const ctrl=d.click(origin+'/recommend/?work=7&edit=1',[],{ctrlKey:true});assert.equal(ctrl.searchParams.get('from'),new URL(detail).pathname+new URL(detail).search+'#reasons');
assert.doesNotThrow(()=>fixture(source,0,{blocked:true}).click('/notifications/',['data-context-notifications']));
// Native back/forward outside BFCache restores a remembered page; direct entry doesn't.
d.winEvents.pagehide();let history=fixture(detail,0,{back:true});history.frames.forEach(f=>f());assert.equal(history.scrolls[0].top,800);
assert.equal(fixture('http://local/?q=direct').frames.length,0);

// Detail back links also set the older shelf marker. A non-shelf destination
// must still restore through the generic controller, after async messages load.
let inboxOrigin='http://local/notifications/?from=%2F%3Fpage%3D2&page=2';
let inboxFixture=fixture(inboxOrigin,1800);inboxFixture.winEvents.pagehide();
let workFixture=fixture('http://local/works/3/');workFixture.click(inboxOrigin,['data-shelf-return']);
storage.set('booklist:shelf-return:v1',JSON.stringify({url:new URL(inboxOrigin).pathname+new URL(inboxOrigin).search,at:Date.now()}));
let restoredInbox=fixture(inboxOrigin,0);restoredInbox.frames.forEach(f=>f());assert.equal(restoredInbox.scrolls[0].top,1800);

console.log('Navigation context: login/edit/message source chains, scroll restore, native modifier links, open redirects, storage fallback passed.');

// A reader scrolls up to reach the message link. Preserve the settled reading
// viewport through pagehide, rather than overwriting it with the header's zero.
storage.clear();let reading=fixture(source,1440);reading.settle();
reading.win.scrollY=0;reading.winEvents.scroll();
const messageLink=reading.click(origin+'/notifications/',['data-context-notifications']);
reading.winEvents.pagehide();
let messages=fixture(messageLink.href,0);messages.click(source,['data-context-return']);
// A previous detail return marker must not take precedence over this context return.
storage.set('booklist:shelf-return:v1',JSON.stringify({url:new URL(source).pathname+new URL(source).search,at:Date.now()}));
let result=fixture(source,0);result.frames.forEach(f=>f());assert.equal(result.scrolls[0].top,1440);
assert.equal(JSON.parse(storage.get('booklist:shelf-return:v1')),null);
// Changing the filter URL cannot reuse another result set's reading viewport.
reading=fixture(source,1440);reading.settle();reading.win.location=new URL(origin+'/?q=different');reading.win.scrollY=0;
const different=reading.click(origin+'/notifications/',['data-context-notifications']);reading.winEvents.pagehide();
messages=fixture(different.href,0);messages.click(origin+'/?q=different',['data-context-return']);
result=fixture(origin+'/?q=different',0);result.frames.forEach(f=>f());assert.equal(result.scrolls[0].top,0);
// A direct fresh visit at the top does not borrow an old stored scroll position.
reading=fixture(source,0);const fresh=reading.click(origin+'/notifications/',['data-context-notifications']);reading.winEvents.pagehide();
messages=fixture(fresh.href,0);messages.click(source,['data-context-return']);result=fixture(source,0);result.frames.forEach(f=>f());assert.equal(result.scrolls[0].top,0);
console.log('Message header returns preserve settled reading positions through pagehide, stale shelf markers and changed filters.');
