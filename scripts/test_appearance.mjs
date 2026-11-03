import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import {APPEARANCE_KEY,DEFAULT_APPEARANCE,decodeAppearance,normalizeAppearance,applyAppearance,MODES,COLORS,MOTIONS,READING_SIZES} from '../static/appearance-state.mjs';
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
 const inputs=[...MODES.map(value=>({name:'mode',value})),...COLORS.map(value=>({name:'color',value})),...MOTIONS.map(value=>({name:'motion',value})),...READING_SIZES.map(value=>({name:'reading',value}))].map(v=>({...node(),...v,matches:()=>true}));
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
// Check actual stylesheet tokens rather than a second, easily stale palette table.
const css=fs.readFileSync(new URL('../static/appearance.css',import.meta.url),'utf8');
const declarations=selector=>{
 const start=css.indexOf(`${selector}{`);assert(start>=0,selector);
 return Object.fromEntries(css.slice(start+selector.length+1,css.indexOf('}',start)).split(';').filter(part=>part.startsWith('--')).map(part=>{const i=part.indexOf(':');return [part.slice(0,i),part.slice(i+1)];}));
};
const dark=declarations(':root[data-theme=dark]');
const resolve=(name,values)=>{
 const value=values[name];assert(value,name);
 const variable=value.match(/^var\((--[\w-]+)(?:,(#[a-f0-9]{6}))?\)$/i);
 if(variable)return values[variable[1]]?resolve(variable[1],values):variable[2];
 assert.match(value,/^#[a-f0-9]{6}$/i,name);return value;
};
const mix=(a,b,weight)=>'#'+[1,3,5].map(i=>Math.round(parseInt(a.slice(i,i+2),16)*weight+parseInt(b.slice(i,i+2),16)*(1-weight)).toString(16).padStart(2,'0')).join('');
const mixture=(name,values)=>{
 const parts=values[name].match(/^color-mix\(in srgb,var\((--[\w-]+)\) (\d+)%,var\((--[\w-]+)\)\)$/);assert(parts,name);
 return mix(resolve(parts[1],values),resolve(parts[3],values),Number(parts[2])/100);
};
for(const theme of COLORS){
 const values={...(theme==='original'?{}:declarations(`:root[data-color-theme=${theme}]`)),...dark};
 for(const bg of ['--paper','--surface','--field','--wash','--tag-bg']){
  for(const fg of ['--ink','--muted','--prose','--accent'])assert(contrast(resolve(fg,values),resolve(bg,values))>=4.5,`${theme} ${fg} on ${bg}`);
 }
 assert(contrast(resolve('--on-solid',values),resolve('--solid',values))>=4.5,`${theme} primary button`);
 assert(contrast(resolve('--on-solid',values),mixture('--solid-hover',values))>=4.5,`${theme} primary hover`);
 assert(contrast(resolve('--muted',values),mixture('--wash-hover',values))>=4.5,`${theme} muted hover text`);
 assert(contrast(resolve('--check-bg',values),resolve('--field',values))>=3,`${theme} checked control`);
 assert(contrast(resolve('--strong-line',values),resolve('--field',values))>=3,`${theme} control outline`);
}
for(const bg of ['f7f6ef','e9eddf','e5ede3','e3eaf1','f0e4e8','eee7dc'])assert(contrast('626a5e',bg)>=4.5);

console.log('Appearance: safe boot, all modes/colors, preview/cancel/save/reset, system/cross-tab updates, storage failure, leave cleanup and text contrast passed.');

h.motionMedia.matches=false;h.events.motion();
h.trigger.events.click();h.choose('reading','larger');assert.equal(h.root.dataset.readingSize,'larger');
h.controls.cancel.events.click();assert.equal(h.root.dataset.readingSize,'normal');
h.trigger.events.click();h.choose('reading','large');h.controls.save.events.click();
assert.equal(JSON.parse(h.map.get(APPEARANCE_KEY)).reading,'large');
assert.equal(h.app.preferences.reading,'large');
h.events.storage({key:APPEARANCE_KEY,newValue:'{"reading":"larger"}'});assert.equal(h.root.dataset.readingSize,'larger');
for(const reading of READING_SIZES){
 const root={dataset:{}};vm.runInNewContext(boot,{document:{documentElement:root},localStorage:{getItem:()=>JSON.stringify({reading})},window:{matchMedia:()=>({matches:false})}});
 assert.equal(root.dataset.readingSize,reading);
}
assert.equal(decodeAppearance('{"reading":"999px"}').reading,'normal');
console.log('Reading sizes: pre-paint defaults, preview/cancel/save and cross-tab persistence passed.');
