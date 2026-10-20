import assert from 'node:assert/strict';
import {installReasonFolds} from '../static/reason-folds.mjs';
const events={},animations=[];
function body(height) {
 const names=new Set(),attrs={'aria-expanded':'false'};
 const text={scrollHeight:height},button={hidden:true,textContent:'展开全文',setAttribute:(k,v)=>{attrs[k]=v;},getAttribute:k=>attrs[k]};
 const clip={getBoundingClientRect:()=>({height:names.has('reason-fold-expanded')?text.scrollHeight:40}),
  getAnimations:()=>[],animate:(frames,options)=>animations.push({frames,options})};
 const row={isConnected:true,classList:{add:n=>names.add(n),remove:n=>names.delete(n),toggle:(n,v)=>v?names.add(n):names.delete(n)},
  getBoundingClientRect:()=>({width:500}),querySelector:s=>s==='.reason-text'?text:s==='[data-reason-expand]'?button:clip};
 button.closest=()=>row;text.closest=()=>row;
 return {row,text,button,names,attrs};
}
const long=body(100),short=body(20),reduced={matches:false};
const doc={querySelectorAll:()=>[long.row,short.row],addEventListener:(name,fn)=>{events[name]=fn;}};
const win={matchMedia:()=>reduced,getComputedStyle:()=>({lineHeight:'20px'}),addEventListener:()=>{}};
const initialize=installReasonFolds(doc,win);
assert.equal(long.button.hidden,false);assert.equal(short.button.hidden,true);
assert.equal(long.names.has('reason-fold-ready'),true);assert.equal(short.names.has('reason-fold-ready'),true);
const click=()=>events.click({target:{closest:()=>long.button}});
click();assert.equal(long.attrs['aria-expanded'],'true');assert.equal(long.button.textContent,'收起');
assert.equal(animations[0].frames[1].height,'100px');
click();assert.equal(long.attrs['aria-expanded'],'false');assert.equal(animations[1].frames[1].height,'40px');
reduced.matches=true;click();assert.equal(animations.length,2);
long.text.scrollHeight=20;initialize();assert.equal(long.button.hidden,true);
assert.equal(long.attrs['aria-expanded'],'false');assert.equal(long.button.textContent,'展开全文');
long.text.scrollHeight=100;initialize();assert.equal(long.button.hidden,false);
console.log('Reason folding handles actual line overflow, long/short initialization, expand/collapse, responsive reset and reduced motion.');
