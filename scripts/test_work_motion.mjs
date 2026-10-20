import assert from 'node:assert/strict';
import {installWorkMotion} from '../static/work-motion.mjs';
function harness() {
  const events = {}, windowEvents = {}, classes = new Set();
  const card = {isConnected:true, classList:{add:name=>classes.add(name),remove:name=>classes.delete(name)}};
  const reduced = {matches:false,addEventListener:(_,fn)=>{reduced.change=fn;}};
  const win = {location:{href:'http://local/',origin:'http://local'},matchMedia:()=>reduced,
    addEventListener:(name,fn)=>{windowEvents[name]=fn;}};
  installWorkMotion({addEventListener:(name,fn)=>{events[name]=fn;}},win);
  function click(href='http://local/works/12/', flags={}, overrides={}) {
    const link={href,target:'',hasAttribute:()=>false,closest:()=>card,...overrides};
    const event={button:0,defaultPrevented:false,target:{closest:()=>link},...flags};
    events.click(event); assert.equal(event.defaultPrevented,Boolean(flags.defaultPrevented));
  }
  function swap(url='http://local/works/12/') {
    let finish;
    const finished=new Promise(resolve=>{finish=resolve;});
    windowEvents.pageswap({activation:{entry:{url}},viewTransition:{finished}});
    return finish;
  }
  return {click,swap,classes,windowEvents,reduced,card};
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
