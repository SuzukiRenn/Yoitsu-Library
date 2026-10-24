import assert from 'node:assert/strict';
import {installShelfSort,filterURL} from '../static/shelf-sort.mjs';
function harness(liveSearch=false) {
 const events={},windowEvents={},requests=[],applied=[],pushed=[],notices=[],attrs=new Set();
 const status={textContent:''},controls={setAttribute:n=>attrs.add(n),removeAttribute:n=>attrs.delete(n),hasAttribute:n=>attrs.has(n)};
 const inputEvents={},timers=new Map();let timerId=0;
 const form={querySelector:()=>null};
 const search={value:'',closest:()=>form,addEventListener:(name,fn)=>{inputEvents[name]=fn;}};
 const shelf={querySelector:selector=>selector==='#shelf-search'?(liveSearch?search:null):controls,contains:()=>true};
 const doc={querySelector:s=>s==='[data-live-shelf]'?shelf:status,addEventListener:(name,fn)=>{events[name]=fn;},dispatchEvent:e=>notices.push(e.detail.pending)};
 const win={location:{href:'http://local/?tag=4&page=2',origin:'http://local',pathname:'/'},
 history:{state:null,replaceState(state,_,url){this.state=state;win.location.href=url;},pushState(state,_,url){this.state=state;win.location.href=url;pushed.push(url);}},
 setTimeout:(fn,delay)=>{const id=++timerId;timers.set(id,{fn,delay});return id;},clearTimeout(id){timers.delete(id);},addEventListener:(name,fn)=>{windowEvents[name]=fn;},
 FormData:class{constructor(){return [['q',search.value],['tag','4'],['sort','likes'],['page_size','20']];}},
 CustomEvent:class{constructor(name,opts){this.detail=opts.detail;}},
 DOMParser:class{parseFromString(html){return {querySelector:()=>({html,querySelector:()=>true})};}}};
 const refresh={apply:(next,options)=>applied.push({next,options})};
 const controller=installShelfSort(refresh,{doc,win,fetcher:(url,options)=>new Promise(resolve=>{requests.push({url,options,resolve});})});
 const reply=(index,ok=true)=>requests[index].resolve({ok,status:ok?200:503,url:requests[index].url,text:async()=>`page${index}`});
 function click(flags={}) {
  const event={button:0,defaultPrevented:false,target:{closest:()=>({href:'http://local/?tag=4&sort=likes',target:'',hasAttribute:()=>false})},preventDefault(){this.defaultPrevented=true;},...flags};
  const done=events.click(event);return {event,done};
 }
 return {controller,reply,requests,applied,pushed,notices,attrs,status,win,windowEvents,click,form,search,inputEvents,timers,flushInput:()=>{const item=[...timers].find(([,v])=>v.delay===300);assert.ok(item);timers.delete(item[0]);return item[1].fn();}};
}
let h=harness(),p=h.click();assert.equal(p.event.defaultPrevented,true);assert.equal(h.pushed.length,0);assert.equal(h.status.textContent,'正在排序…');
h.reply(0);await p.done;assert.equal(h.applied.length,1);assert.deepEqual(h.applied[0].options,{preserveAnchor:false,animateReorder:true,syncFilters:true});
assert.equal(h.win.location.href,'http://local/?tag=4&sort=likes');assert.deepEqual(h.notices,[true,false]);
assert.equal(h.attrs.has('aria-busy'),false);assert.equal(h.status.textContent,'');
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
p=h.controller.navigate('http://local/?sort=newest');h.windowEvents.pagehide();assert.equal(h.status.textContent,'');h.reply(2);await p;assert.equal(h.applied.length,2);
console.log('In-page sorting preserves native modified clicks, filters and history; cancels stale requests; keeps old content on failure and coordinates polling.');

const submitted=filterURL({},'http://local/?page=8&tag=old&q=old',class{constructor(){return [['q','Alice'],['tag','1'],['tag','2'],['sort','likes'],['page_size','20']];}});
const filtered=new URL(submitted);assert.equal(filtered.searchParams.get('q'),'Alice');assert.deepEqual(filtered.searchParams.getAll('tag'),['1','2']);assert.equal(filtered.searchParams.has('page'),false);assert.equal(filtered.searchParams.get('sort'),'likes');
console.log('Search and multi-tag submissions preserve sort/size, replace old filters and reset pagination.');

const tagTarget={closest(selector){
 assert.match(selector, /\.public-card \.work-tag/);
 return {href:'http://local/?tag=12',target:'',hasAttribute:()=>false,matches:selector=>selector==='.work-tag'};
}};
h=harness();p=h.click({target:tagTarget});
assert.equal(p.event.defaultPrevented,true);assert.equal(h.status.textContent,'正在筛选…');
h.reply(0);await p.done;
assert.equal(h.applied.length,1);assert.equal(h.applied[0].options.animateReorder,true);
assert.equal(h.applied[0].options.syncFilters,true);assert.equal(h.win.location.href,'http://local/?tag=12');
for(const flags of [{ctrlKey:true},{metaKey:true},{shiftKey:true},{altKey:true},{button:1}]){
 h=harness();h.click({target:tagTarget,...flags});assert.equal(h.requests.length,0);
}
console.log('Card tag links reuse animated shelf filtering and preserve native modified clicks.');

const authorTarget={closest(selector){
 assert.match(selector, /\.public-card \.work-author/);
 return {href:'http://local/?q=Alice%20%26%20Bob',target:'',hasAttribute:()=>false,matches:selector=>selector==='.work-author'};
}};
h=harness();p=h.click({target:authorTarget});
assert.equal(p.event.defaultPrevented,true);assert.equal(h.status.textContent,'正在筛选…');
h.reply(0);await p.done;
assert.equal(h.applied[0].options.animateReorder,true);assert.equal(h.applied[0].options.syncFilters,true);
assert.equal(new URL(h.win.location.href).searchParams.get('q'),'Alice & Bob');
for(const flags of [{ctrlKey:true},{metaKey:true},{shiftKey:true},{altKey:true},{button:1}]){
 h=harness();h.click({target:authorTarget,...flags});assert.equal(h.requests.length,0);
}
console.log('Card author links reuse animated shelf filtering and retain the encoded search query.');

h=harness(true);h.search.value='我的';h.inputEvents.input({});assert.equal(h.requests.length,0);
h.search.value='我的妹妹';h.inputEvents.input({});assert.equal([...h.timers.values()].filter(t=>t.delay===300).length,1);
p=h.flushInput();assert.equal(new URL(h.requests[0].url).searchParams.get('q'),'我的妹妹');
assert.equal(new URL(h.requests[0].url).searchParams.has('page'),false);
h.search.value='我的妹妹不可能';h.inputEvents.input({});assert.equal(h.requests[0].options.signal.aborted,true);
h.reply(0);await p;assert.equal(h.applied.length,0);
p=h.flushInput();h.reply(1);await p;assert.equal(h.applied.length,1);assert.equal(h.pushed.length,0);
h.search.value='';p=h.inputEvents.input({});assert.equal(h.requests.length,3);h.reply(2);await p;
assert.equal(new URL(h.win.location.href).searchParams.get('q'),'');assert.deepEqual(new URL(h.win.location.href).searchParams.getAll('tag'),['4']);
h=harness(true);h.inputEvents.compositionstart();h.search.value='wo';h.inputEvents.input({isComposing:true});
assert.equal(h.requests.length,0);assert.equal([...h.timers.values()].filter(t=>t.delay===300).length,0);
h.search.value='我的';h.inputEvents.compositionend();p=h.flushInput();h.reply(0);await p;
assert.equal(new URL(h.win.location.href).searchParams.get('q'),'我的');
h=harness(true);h.search.value='我的';h.inputEvents.input({});h.windowEvents.pagehide();
assert.equal([...h.timers.values()].filter(t=>t.delay===300).length,0);
console.log('Live search debounces input, waits for IME, rejects stale responses, clears immediately and preserves tags without per-keystroke history entries.');

for (const target of [tagTarget, authorTarget]) {
 h=harness(true);h.form.querySelector=selector=>selector==='[name="tag_mode"]:checked'?{value:'any'}:null;
 p=h.click({target});assert.equal(new URL(h.requests[0].url).searchParams.get('tag_mode'),'any');
 h.reply(0);await p.done;assert.equal(new URL(h.win.location.href).searchParams.get('tag_mode'),'any');
}
const modeURL=filterURL({},'http://local/?page=9',class{constructor(){return [['tag','1'],['tag','2'],['tag_mode','any'],['q','Alice'],['liked','1']];}});
assert.equal(new URL(modeURL).searchParams.get('tag_mode'),'any');assert.equal(new URL(modeURL).searchParams.get('liked'),'1');
assert.equal(new URL(modeURL).searchParams.has('page'),false);
console.log('Any-tag matching survives card tag/author navigation and combined form submission.');
