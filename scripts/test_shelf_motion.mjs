import assert from 'node:assert/strict';
import {createShelfMotion} from '../static/shelf-motion.mjs';
function harness(){
 const records=[],ghosts=[],events={};
 const reduced={matches:false,addEventListener:(_,fn)=>{reduced.change=fn;}};
 const win={innerHeight:800,matchMedia:()=>reduced,addEventListener:(name,fn)=>{events[name]=fn;}};
 function animate(node,frames,options){let finish;const animation={finished:new Promise(resolve=>{finish=resolve;}),cancelled:false,cancel(){this.cancelled=true;},finish:()=>finish()};records.push({node,frames,options,animation});return animation;}
 function card(id,top){const c={dataset:{shelfWork:id},top,getBoundingClientRect(){return {top:this.top,bottom:this.top+100,left:0,width:200,height:100};},animate(frames,options){return animate(this,frames,options);},cloneNode(){const g={style:{},classList:{remove(){}},removed:false,removeAttribute(){},querySelectorAll:()=>[],setAttribute(){},remove(){this.removed=true;},animate(frames,options){return animate(this,frames,options);}};return g;}};return c;}
 const a=card('1',100),b=card('2',220);let cards=[a,b];
 const grid={querySelectorAll:()=>cards};const doc={body:{append:g=>ghosts.push(g)}};
 const motion=createShelfMotion({doc,win});
 return {a,b,grid,motion,records,ghosts,reduced,events,setCards:c=>{cards=c;}};
}
let h=harness(),finish=h.motion.capture(h.grid);h.a.top=220;h.b.top=100;finish();
assert.equal(h.records.length,2);assert.equal(h.records[0].frames[0].transform,'translate(0px,-120px)');
assert.equal(h.records[1].frames[0].transform,'translate(0px,120px)');
// A card leaving the page stays as an inert, non-interactive visual snapshot until faded.
h=harness();finish=h.motion.capture(h.grid);h.setCards([h.b]);h.b.top=100;finish();
assert.equal(h.ghosts.length,1);assert.equal(h.ghosts[0].inert,true);assert.equal(h.ghosts[0].style.position,'fixed');
assert.equal(h.ghosts[0].removed,false);h.records.find(r=>r.node===h.ghosts[0]).animation.finish();await Promise.resolve();assert.equal(h.ghosts[0].removed,true);
// Cards entering the viewport must move even when their old position was offscreen.
h=harness();h.a.top=1200;finish=h.motion.capture(h.grid);h.a.top=100;finish();assert.equal(h.records.length,1);
// A subsequent refresh cancels previous movements and removes stale ghosts.
h=harness();finish=h.motion.capture(h.grid);h.setCards([h.b]);finish();h.motion.capture(h.grid);
assert.equal(h.ghosts[0].removed,true);assert.equal(h.records[0].animation.cancelled,true);
h=harness();h.reduced.matches=true;finish=h.motion.capture(h.grid);h.a.top=220;finish();assert.equal(h.records.length,0);
h=harness();finish=h.motion.capture(h.grid);h.a.top=220;finish();h.events.pagehide();assert.equal(h.records[0].animation.cancelled,true);
console.log('Live shelf reorders move retained and offscreen cards; exits fade inert snapshots; rapid updates, pagehide and reduced motion clean up safely.');

// Reading another card after an animation write forces a style/layout flush in browsers.
h=harness();finish=h.motion.capture(h.grid);
for(const card of [h.a,h.b]){const read=card.getBoundingClientRect.bind(card);card.getBoundingClientRect=()=>{assert.equal(h.records.length,0,'all new geometry must be read before any animation starts');return read();};}
h.a.top=220;h.b.top=100;finish();assert.equal(h.records.length,2);
console.log('Reordering batches all geometry reads before animation writes.');
