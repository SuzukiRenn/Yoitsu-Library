import assert from 'node:assert/strict';
import {installShelfReturn} from '../static/shelf-return.mjs';
const storage=new Map();const source='http://local/?page=3&tag=11&q=book&sort=likes';
function setup(href,{shelf=true,removed=false,y=0,height=3000,blocked=false}={}) {
 const events={},windowEvents={},frames=[],scrolls=[];
 const card={offsetTop:1000,offsetParent:null,dataset:{shelfWork:'42'},getBoundingClientRect:()=>({top:1000-y})};
 const list={querySelectorAll:()=>removed?[]:[card],querySelector:()=>removed?null:card};
 const doc={readyState:'complete',documentElement:{scrollHeight:height},querySelector:()=>shelf?list:null,addEventListener:(name,fn)=>{events[name]=fn;}};
 const win={location:{href,origin:'http://local'},scrollY:y,innerHeight:600,
  sessionStorage:{getItem:k=>{if(blocked)throw Error('blocked');return storage.get(k);},setItem:(k,v)=>{if(blocked)throw Error('blocked');storage.set(k,v);}},
  requestAnimationFrame:fn=>frames.push(fn),scrollTo:options=>scrolls.push(options),addEventListener:(name,fn)=>{windowEvents[name]=fn;}};
 installShelfReturn(doc,win);
 function click(href,back=false,flags={}) {
  const link={href,target:'',hasAttribute:()=>false,closest:()=>shelf?card:null,matches:()=>back};
  const event={target:{closest:()=>link},button:0,preventDefault(){throw Error('native navigation must remain intact');},...flags};
  events.click(event);return link;
 }
 return {click,frames,scrolls,windowEvents};
}
let h=setup(source,{y:900});
const link=h.click('http://local/works/42/?activity=3#reasons');
const url=new URL(link.href);assert.equal(url.searchParams.get('from'),new URL(source).pathname+new URL(source).search);
assert.equal(url.searchParams.get('activity'),'3');assert.equal(url.hash,'#reasons');
const detail=setup(link.href,{shelf:false});detail.click(source,true);
h=setup(source);h.frames.forEach(fn=>fn());assert.equal(h.scrolls[0].top,900);
assert.equal(h.scrolls[0].behavior,'instant');
// Removed anchors fall back to a clamped position; nothing recreates a removed card.
detail.click(source,true);h=setup(source,{removed:true,height:800});h.frames.forEach(fn=>fn());assert.equal(h.scrolls[0].top,200);
// Direct opening / native history without an explicit return stays under browser control.
h=setup(source);assert.equal(h.frames.length,0);
// Browser storage restrictions must not break ordinary links.
h=setup(source,{blocked:true});assert.equal(new URL(h.click('http://local/works/42/').href).searchParams.get('from'),new URL(source).pathname+new URL(source).search);
console.log('Shelf returns keep source filters, preserve native navigation, restore anchors and clamp removed-content positions; storage failure degrades safely.');
