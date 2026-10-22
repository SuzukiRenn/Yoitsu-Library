import assert from 'node:assert/strict';
import {installWorkMotion} from '../static/work-motion.mjs';
function harness(native=true) {
  const events = {}, windowEvents = {}, classes = new Set(), rootClasses=new Set(), navigations=[], timers=[];
  const animations=[];
  let scrolled = false;
  const card = {getBoundingClientRect:()=>({top:100,bottom:300}),scrollIntoView:()=>{scrolled=true;},isConnected:true, classList:{add:name=>classes.add(name),remove:name=>classes.delete(name)}};
  const reduced = {matches:false,addEventListener:(_,fn)=>{reduced.change=fn;}};
  const win = {location:{href:'http://local/',origin:'http://local',assign:url=>navigations.push(url)},setTimeout:fn=>{timers.push(fn);return timers.length;},clearTimeout(){},matchMedia:()=>reduced,
    innerHeight:800,navigation:{activation:{from:{url:'http://local/works/12/'}}},
    addEventListener:(name,fn)=>{windowEvents[name]=fn;}};
  if(native)Object.assign(win,{CSSViewTransitionRule:class{},onpageswap:null,onpagereveal:null});
  installWorkMotion({documentElement:{classList:{add:name=>rootClasses.add(name)}},querySelector:selector=>selector === '.public-card[data-shelf-work="12"]' ? card : null,addEventListener:(name,fn)=>{events[name]=fn;}},win);
  function click(href='http://local/works/12/', flags={}, overrides={}) {
    const link={href,target:'',hasAttribute:()=>false,closest:()=>card,...overrides};
    const event={preventDefault(){this.defaultPrevented=true;},button:0,defaultPrevented:false,target:{closest:()=>link},...flags};
    events.click(event); if(native)assert.equal(event.defaultPrevented,Boolean(flags.defaultPrevented));return event;
  }
  function swap(url='http://local/works/12/') {
    let finish;
    const finished=new Promise(resolve=>{finish=resolve;});
    windowEvents.pageswap({activation:{entry:{url}},viewTransition:{finished}});
    return finish;
  }
  return {click,swap,classes,windowEvents,reduced,card,win,rootClasses,navigations,timers,animations,get scrolled(){return scrolled;}};
}
let h=harness();h.click();assert.equal(h.classes.size,0);
let finish=h.swap();assert.equal(h.classes.has('work-opening'),true);
finish();await Promise.resolve();assert.equal(h.classes.size,0);
for (const flags of [{ctrlKey:true},{metaKey:true},{shiftKey:true},{altKey:true},{button:1},{defaultPrevented:true}]) {
 h=harness();h.click(undefined,flags);h.swap();assert.equal(h.classes.size,0);
}
for (const href of ['https://other/works/12/','http://local/recommend/?work=12','http://local/works/12/#reasons']) {
 h=harness();h.click(href);h.swap(href);assert.equal(h.classes.size,0);
}
h=harness();h.click(undefined,{}, {target:'_blank'});h.swap();assert.equal(h.classes.size,0);
h=harness();h.reduced.matches=true;h.click();h.swap();assert.equal(h.classes.size,0);
h=harness();h.click();h.card.isConnected=false;h.swap();assert.equal(h.classes.size,0);
h=harness();h.click();h.swap('http://local/works/13/');assert.equal(h.classes.size,0);
h=harness();h.click('http://local/works/12/?activity=3');h.swap('http://local/works/12/?activity=3');
assert.equal(h.classes.size,1);h.windowEvents.pageshow();assert.equal(h.classes.size,0);
h=harness();h.click();h.swap();h.reduced.matches=true;h.reduced.change();assert.equal(h.classes.size,0);
console.log('Card shared elements preserve native navigation, activity context, modified clicks, reduced motion, removed cards and history cleanup.');

function fallbackHarness(){
 const h=harness(false);
 h.card.animate=()=>{let finish;const animation={finished:new Promise(resolve=>{finish=resolve;}),cancel(){},finish:()=>finish()};h.animations.push(animation);return animation;};
 return h;
}
h=fallbackHarness();assert.equal(h.click().defaultPrevented,true);assert.equal(h.navigations.length,0);
h.animations[0].finish();await Promise.resolve();assert.deepEqual(h.navigations,['http://local/works/12/']);h.timers[0]();assert.equal(h.navigations.length,1);
h=fallbackHarness();h.click();h.timers[0]();assert.equal(h.navigations.length,1);
h=fallbackHarness();h.click();h.windowEvents.pagehide();h.animations[0].finish();await Promise.resolve();assert.equal(h.navigations.length,0);
h=fallbackHarness();h.click(undefined,{ctrlKey:true});assert.equal(h.animations.length,0);
h=fallbackHarness();h.reduced.matches=true;assert.equal(h.click().defaultPrevented,false);assert.equal(h.animations.length,0);
h=fallbackHarness();h.card.animate=()=>{throw Error('unsupported');};assert.equal(h.click().defaultPrevented,false);
h=fallbackHarness();h.click();h.reduced.matches=true;h.reduced.change();assert.equal(h.navigations.length,1);
h=fallbackHarness();h.click('http://local/',{}, {matches:()=>true});h.animations[0].finish();await Promise.resolve();assert.deepEqual(h.navigations,['http://local/']);
console.log('Native transitions suppress duplicate entry for the entire document; non-native entry/return fallback preserves navigation, timeout, cancellation and reduced motion.');

const {readFileSync}=await import('node:fs');
const {runInNewContext}=await import('node:vm');
function earlyHarness(previous='http://local/works/12/'){
 const h=harness(), earlyEvents={};
 runInNewContext(readFileSync(new URL('../static/motion-boot.js',import.meta.url),'utf8'),{
  URL,
  window:{...h.win,addEventListener:(name,fn)=>{earlyEvents[name]=fn;},navigation:{activation:{from:{url:previous}}}},
  document:{documentElement:{classList:{add:name=>h.rootClasses.add(name)}},querySelector:selector=>selector === '.public-card[data-shelf-work="12"]' ? h.card : null}
 });
 return {...h,reveal:earlyEvents.pagereveal,get scrolled(){return h.scrolled;}};
}
h=earlyHarness();h.reveal({viewTransition:null});assert.equal(h.rootClasses.size,0);
let finishReturn;
const returnFinished=new Promise(resolve=>{finishReturn=resolve;});
h.reveal({viewTransition:{finished:returnFinished}});
assert.equal(h.rootClasses.has('native-page-transition'),true);
assert.equal(h.classes.has('work-opening'),true);assert.equal(h.scrolled,false);
finishReturn();await Promise.resolve();assert.equal(h.classes.size,0);
assert.equal(h.rootClasses.has('native-page-transition'),true);
h=earlyHarness();h.card.getBoundingClientRect=()=>({top:1000,bottom:1300});
h.reveal({viewTransition:{finished:Promise.resolve()}});assert.equal(h.scrolled,true);await Promise.resolve();
for(const url of ['http://local/works/99/','http://local/recommend/','https://other/works/12/']){
 h=earlyHarness(url);h.reveal({viewTransition:{finished:Promise.resolve()}});assert.equal(h.classes.size,0);
}
h=earlyHarness();h.reduced.matches=true;h.reveal({viewTransition:{finished:Promise.resolve()}});assert.equal(h.classes.size,0);
h=earlyHarness();h.reveal({viewTransition:{finished:Promise.reject(Error('skip'))}});await Promise.resolve();assert.equal(h.classes.size,0);
console.log('Early return setup matches before deferred modules, keeps the document marker, handles offscreen cards, missing cards, reduced motion and skipped transitions.');
