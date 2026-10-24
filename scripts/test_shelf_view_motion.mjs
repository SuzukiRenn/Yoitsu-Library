import assert from 'node:assert/strict';
import {createShelfViewMotion} from '../static/shelf-view-motion.mjs';
const deferred=()=>{let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return {promise,resolve,reject};};
function setup({native=true,reduced=false}={}){
 const root={dataset:{}},events={},windowEvents={},jobs=[],plays=[];let compact=false,updates=0;
 const cover={style:{}},title={style:{}};
 const card={style:{},isConnected:true,querySelector:s=>s==='.book-cover'?cover:title,
  getBoundingClientRect:()=>compact?{left:0,top:20,bottom:120,width:600,height:100}:{left:300,top:20,bottom:320,width:300,height:300},
  animate(frames,options){const done=deferred();const animation={finished:done.promise,cancel(){done.reject(Error('cancelled'));}};plays.push({frames,options,animation,done});return animation;}};
 const shelf={querySelectorAll:()=>[card]},media={matches:reduced,addEventListener:(type,fn)=>events.media=fn};
 const win={innerHeight:800,matchMedia:()=>media,addEventListener:(type,fn)=>windowEvents[type]=fn};
 const doc={documentElement:root,addEventListener:(type,fn)=>events[type]=fn};
 if(native)doc.startViewTransition=callback=>{const ready=deferred(),finished=deferred();const transition={ready:ready.promise,finished:finished.promise,skipTransition(){this.skipped=true;}};jobs.push({callback,ready,finished,transition});return transition;};
 const motion=createShelfViewMotion({doc,win});
 return {root,card,cover,title,events,windowEvents,jobs,plays,shelf,media,motion,update:value=>()=>{compact=value;updates++;},get compact(){return compact;},get updates(){return updates;}};
}
const flush=async()=>{for(let n=0;n<6;n++)await Promise.resolve();};
const native=setup();native.motion.change(native.shelf,native.update(true));
assert.equal(native.card.style.viewTransitionName,'shelf-layout-card-0');
assert.equal(native.cover.style.viewTransitionName,'shelf-layout-cover-0');
native.jobs[0].callback();native.jobs[0].ready.resolve();native.jobs[0].finished.resolve();await flush();
assert.equal(native.compact,true);assert.equal(native.root.dataset.shelfViewChanging,undefined);assert.equal(native.card.style.viewTransitionName,undefined);
const fast=setup();fast.motion.change(fast.shelf,fast.update(true));fast.motion.change(fast.shelf,fast.update(false));
assert.equal(fast.jobs[0].transition.skipped,true);fast.jobs[0].callback();fast.jobs[1].callback();
assert.equal(fast.compact,false);assert.equal(fast.updates,2,'each desired layout applies once, even when skipped callbacks arrive late');
fast.windowEvents.pagehide();assert.equal(fast.root.dataset.shelfViewChanging,undefined);
const reduced=setup({reduced:true});reduced.motion.change(reduced.shelf,reduced.update(true));
assert.equal(reduced.jobs.length,0);assert.equal(reduced.compact,true);
const fallback=setup({native:false});fallback.motion.change(fallback.shelf,fallback.update(true));
assert.equal(fallback.plays.length,1);assert.match(fallback.plays[0].frames[0].transform,/scale\(0.5,3\)/);
fallback.plays[0].done.resolve();await flush();assert.equal(fallback.root.dataset.shelfViewChanging,undefined);
const rejected=setup();rejected.motion.change(rejected.shelf,rejected.update(true));rejected.jobs[0].callback();
rejected.jobs[0].ready.reject(Error('snapshot unavailable'));rejected.jobs[0].finished.resolve();await flush();
assert.equal(rejected.plays.length,1);assert.equal(rejected.updates,1);assert.equal(rejected.root.dataset.shelfViewChanging,'');
rejected.plays[0].done.resolve();await flush();assert.equal(rejected.root.dataset.shelfViewChanging,undefined);
const filtering=setup();filtering.motion.change(filtering.shelf,filtering.update(true));filtering.events['booklist:shelf-navigation']({detail:{pending:true}});
assert.equal(filtering.compact,true);assert.equal(filtering.root.dataset.shelfViewChanging,undefined);
console.log('View motion: native card/cover matches, rapid toggles, reduced motion, resize/leave cleanup, fallback geometry and rejected snapshots passed.');

// A long shelf must not allocate native snapshots for cards outside the viewport.
const bounded=setup();const hiddenCover={style:{}},hiddenTitle={style:{}};
const hiddenCard={style:{},isConnected:true,querySelector:s=>s==='.book-cover'?hiddenCover:hiddenTitle,
 getBoundingClientRect:()=>({left:0,top:2500,bottom:2800,width:300,height:300})};
bounded.shelf.querySelectorAll=()=>[bounded.card,hiddenCard];bounded.motion.change(bounded.shelf,bounded.update(true));
assert.equal(hiddenCard.style.viewTransitionName,undefined);assert.equal(hiddenCover.style.viewTransitionName,undefined);
assert.equal(bounded.card.style.viewTransitionName,'shelf-layout-card-0');
bounded.jobs[0].callback();bounded.jobs[0].ready.resolve();bounded.jobs[0].finished.resolve();await flush();
assert.equal(bounded.compact,true);assert.equal(bounded.root.dataset.shelfViewChanging,undefined);
console.log('Native layout snapshots are bounded to the visible viewport; offscreen content and cleanup stay intact.');
