import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import {APPEARANCE_KEY,DEFAULT_APPEARANCE,decodeAppearance,normalizeAppearance,applyAppearance,MODES,COLORS,MOTIONS} from '../static/appearance-state.mjs';
import {installAppearance} from '../static/appearance.mjs';
const boot=fs.readFileSync(new URL('../static/appearance-boot.js',import.meta.url),'utf8');
for(const raw of [null,'null','false','{}','[]','bad','{"mode":"evil","color":"#bad"}',...MODES.flatMap(mode=>COLORS.flatMap(color=>MOTIONS.flatMap(motion=>[false,true].map(hideHeat=>JSON.stringify({mode,color,motion,hideHeat})))))]){
 for(const dark of [false,true])for(const reduced of [false,true]){
  const root={dataset:{}};vm.runInNewContext(boot,{document:{documentElement:root},localStorage:{getItem:()=>raw},window:{matchMedia:query=>({matches:query.includes('color-scheme')?dark:reduced})}});
  const expected={dataset:{}};applyAppearance(expected,decodeAppearance(raw),dark,reduced);assert.deepEqual({...root.dataset},expected.dataset,'boot and settings must agree before paint');
 }
}
assert.deepEqual(normalizeAppearance({mode:'<script>',color:'url(evil)'}),DEFAULT_APPEARANCE);
function harness(){
 const events={},map=new Map(),root={dataset:{}},controls={};let blocked=false;
 function node(){return {events:{},setAttribute(k,v){this[k]=v;},addEventListener(k,v){this.events[k]=v;},focus(){this.focused=true;}};}
 const trigger=node(),status={textContent:''};
 const inputs=[...MODES.map(value=>({name:'mode',value})),...COLORS.map(value=>({name:'color',value})),...MOTIONS.map(value=>({name:'motion',value}))].map(v=>({...node(),...v,matches:()=>true}));
 for(const name of ['reset','cancel','save'])controls[name]=node();
 const heat={...node(),checked:false,matches:()=>true};
 const dialog={...node(),open:false,showModal(){this.open=true;},close(){this.open=false;this.events.close();},querySelector:s=>s==='[name=hideHeat]'?heat:s.includes('status')?status:controls[s.match(/appearance-(\w+)/)[1]],querySelectorAll:s=>s.includes('radio')?inputs:[controls.cancel]};
 const doc={documentElement:root,querySelector:s=>s==='dialog[open]'?(dialog.open?dialog:null):s==='#appearance-settings'?dialog:trigger};
 const media={matches:false,addEventListener:(k,v)=>events.media=v};
 const motionMedia={matches:false,addEventListener:(k,v)=>events.motion=v};
 const win={matchMedia:query=>query.includes('color-scheme')?media:motionMedia,addEventListener:(k,v)=>events[k]=v,localStorage:{setItem(k,v){if(blocked)throw Error('blocked');map.set(k,v);}}};
 const app=installAppearance(doc,win);
 return {root,trigger,dialog,status,controls,events,media,motionMedia,map,app,heat,set blocked(v){blocked=v;},choose(name,value){inputs.filter(i=>i.name===name).forEach(i=>i.checked=i.value===value);dialog.events.change({target:inputs[0]});}};
}
const h=harness();h.trigger.events.click();h.choose('mode','dark');h.choose('color','rose');
assert.equal(h.root.dataset.theme,'dark');assert.equal(h.map.size,0,'preview must not persist');
h.controls.cancel.events.click();assert.equal(h.root.dataset.theme,'light');assert.equal(h.root.dataset.colorTheme,'original');
h.trigger.events.click();h.choose('color','blue');h.controls.save.events.click();
assert.equal(h.dialog.open,false);assert.equal(JSON.parse(h.map.get(APPEARANCE_KEY)).color,'blue');
h.media.matches=true;h.events.media();assert.equal(h.root.dataset.theme,'dark','system preference updates live');
h.trigger.events.click();h.choose('color','rose');h.events.storage({key:APPEARANCE_KEY,newValue:'{"mode":"light","color":"brown"}'});
assert.equal(h.root.dataset.colorTheme,'rose');h.controls.cancel.events.click();assert.equal(h.root.dataset.colorTheme,'brown');assert.equal(h.root.dataset.theme,'light');
h.trigger.events.click();h.choose('mode','dark');h.blocked=true;h.controls.save.events.click();
assert.equal(h.dialog.open,true);assert.match(h.status.textContent,/不允许保存/);h.controls.cancel.events.click();assert.equal(h.root.dataset.theme,'light');
h.trigger.events.click();h.controls.reset.events.click();assert.equal(h.root.dataset.colorTheme,'original');h.events.pagehide();assert.equal(h.root.dataset.colorTheme,'brown');
h.events.storage({key:null,newValue:null});assert.deepEqual(h.app.preferences,DEFAULT_APPEARANCE);
// New preferences preview without persisting, cancel restores both, save survives cross-tab updates.
h.blocked=false;
h.trigger.events.click();h.choose('motion','reduce');h.heat.checked=true;h.dialog.events.change({target:h.heat});
assert.equal(h.root.dataset.motion,'reduce');assert.equal(h.root.dataset.hideHeat,'true');
h.controls.cancel.events.click();assert.equal(h.root.dataset.motion,'full');assert.equal(h.root.dataset.hideHeat,'false');
h.trigger.events.click();h.choose('motion','reduce');h.heat.checked=true;h.dialog.events.change({target:h.heat});h.controls.save.events.click();
assert.equal(JSON.parse(h.map.get(APPEARANCE_KEY)).hideHeat,true);assert.equal(h.app.preferences.motion,'reduce');
h.events.storage({key:APPEARANCE_KEY,newValue:'{"mode":"system","color":"original","motion":"system","hideHeat":true}'});
assert.equal(h.root.dataset.motion,'full');assert.equal(h.root.dataset.hideHeat,'true');
h.motionMedia.matches=true;h.events.motion();assert.equal(h.root.dataset.motion,'reduce');
assert.equal(decodeAppearance('{"mode":"dark","color":"blue"}').hideHeat,false,'Old saved preferences keep defaults');
const luminance=hex=>{const c=hex.match(/\w\w/g).map(n=>parseInt(n,16)/255).map(n=>n<=.04045?n/12.92:((n+.055)/1.055)**2.4);return c[0]*.2126+c[1]*.7152+c[2]*.0722;};
const contrast=(a,b)=>{const x=luminance(a),y=luminance(b);return (Math.max(x,y)+.05)/(Math.min(x,y)+.05);};
for(const color of ['222222','4e6653','50677e','805c6b','796347'])assert(contrast(color,'ffffff')>=4.5,color);
for(const color of ['b8c5d6','abc3a9','acc2d8','d6b3c1','d3c0a2'])assert(contrast(color,'0f172a')>=4.5,color);
for(const bg of ['f7f6ef','e9eddf','e5ede3','e3eaf1','f0e4e8','eee7dc'])assert(contrast('626a5e',bg)>=4.5);
for(const bg of ['1e293b','243247','2b384d','2d3b30','2b3845','42313a','3d362b'])assert(contrast('94a3b8',bg)>=4.5);
console.log('Appearance: safe boot, all modes/colors, preview/cancel/save/reset, system/cross-tab updates, storage failure, leave cleanup and text contrast passed.');
