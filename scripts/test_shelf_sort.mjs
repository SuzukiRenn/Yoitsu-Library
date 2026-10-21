import assert from 'node:assert/strict';
import {installShelfSort} from '../static/shelf-sort.mjs';
function harness() {
 const events={},windowEvents={},requests=[],applied=[],pushed=[],notices=[],attrs=new Set();
 const status={textContent:''},controls={setAttribute:n=>attrs.add(n),removeAttribute:n=>attrs.delete(n),hasAttribute:n=>attrs.has(n)};
 const shelf={querySelector:()=>controls,contains:()=>true};
 const doc={querySelector:s=>s==='[data-live-shelf]'?shelf:status,addEventListener:(name,fn)=>{events[name]=fn;},dispatchEvent:e=>notices.push(e.detail.pending)};
 const win={location:{href:'http://local/?tag=4&page=2',origin:'http://local',pathname:'/'},
 history:{state:null,replaceState(state,_,url){this.state=state;win.location.href=url;},pushState(state,_,url){this.state=state;win.location.href=url;pushed.push(url);}},
 setTimeout:()=>1,clearTimeout(){},addEventListener:(name,fn)=>{windowEvents[name]=fn;},
 CustomEvent:class{constructor(name,opts){this.detail=opts.detail;}},
 DOMParser:class{parseFromString(html){return {querySelector:()=>({html,querySelector:()=>true})};}}};
 const refresh={apply:(next,options)=>applied.push({next,options})};
 const controller=installShelfSort(refresh,{doc,win,fetcher:(url,options)=>new Promise(resolve=>{requests.push({url,options,resolve});})});
 const reply=(index,ok=true)=>requests[index].resolve({ok,status:ok?200:503,url:requests[index].url,text:async()=>`page${index}`});
 function click(flags={}, href='http://local/?tag=4&sort=likes') {
  const event={button:0,defaultPrevented:false,target:{closest:selector=>{
    assert.match(selector,/\.shelf-sort a\[href\]/);
    assert.match(selector,/\.shelf-period a\[href\]/);
    return {href,target:'',hasAttribute:()=>false};
  }},preventDefault(){this.defaultPrevented=true;},...flags};
  const done=events.click(event);return {event,done};
 }
 return {controller,reply,requests,applied,pushed,notices,attrs,status,win,windowEvents,click};
}
let h=harness(),p=h.click();assert.equal(p.event.defaultPrevented,true);assert.equal(h.pushed.length,0);
h.reply(0);await p.done;assert.equal(h.applied.length,1);assert.deepEqual(h.applied[0].options,{preserveAnchor:false,animateReorder:true});
assert.equal(h.win.location.href,'http://local/?tag=4&sort=likes');assert.deepEqual(h.notices,[true,false]);
assert.equal(h.attrs.has('aria-busy'),false);
for(const flags of [{ctrlKey:true},{metaKey:true},{shiftKey:true},{altKey:true},{button:1},{defaultPrevented:true}]){
 h=harness();h.click(flags);assert.equal(h.requests.length,0);
}
h=harness();let old=h.controller.navigate('http://local/?sort=likes'),latest=h.controller.navigate('http://local/?sort=newest');
assert.equal(h.requests[0].options.signal.aborted,true);h.reply(1);await latest;h.reply(0);await old;
assert.equal(h.applied.length,1);assert.equal(h.win.location.href,'http://local/?sort=newest');
h=harness();p=h.controller.navigate('http://local/?sort=likes');h.reply(0,false);await p;
assert.equal(h.applied.length,0);assert.equal(h.pushed.length,0);assert.match(h.status.textContent,/失败/);
h=harness();p=h.controller.navigate('http://local/?sort=likes');h.reply(0);await p;
h.win.location.href='http://local/?tag=4&page=2';p=h.windowEvents.popstate({state:{booklistShelf:'/'}});h.reply(1);await p;
assert.equal(h.applied.length,2);assert.equal(h.pushed.length,1);
p=h.controller.navigate('http://local/?sort=newest');h.windowEvents.pagehide();h.reply(2);await p;assert.equal(h.applied.length,2);
console.log('In-page sorting preserves native modified clicks, filters and history; cancels stale requests; keeps old content on failure and coordinates polling.');

h=harness();p=h.click({},'http://local/?tag=4&sort=likes&period=week');
h.reply(0);await p.done;
assert.equal(h.win.location.href,'http://local/?tag=4&sort=likes&period=week');
assert.equal(h.applied.length,1);
h.win.location.href='http://local/?tag=4&sort=likes&period=month';
p=h.windowEvents.popstate({state:{booklistShelf:'/'}});h.reply(1);await p;
assert.equal(h.applied.length,2);assert.equal(h.pushed.length,1);
assert.equal(h.requests.at(-1).url,'http://local/?tag=4&sort=likes&period=month');
p=h.click({},'http://local/?tag=4&sort=newest&period=all');h.reply(2);await p.done;
assert.equal(h.win.location.href,'http://local/?tag=4&sort=newest&period=all');
console.log('Public period switching and back/forward restoration keep the chosen period and tags without reloading the page.');
