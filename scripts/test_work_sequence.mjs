import assert from 'node:assert/strict';
import {sequenceNeighbors,installWorkSequence} from '../static/work-sequence.mjs';
assert.deepEqual(sequenceNeighbors([1,2,3,4],[4,2,1],2),{previous:1,next:4,position:2,total:3});
assert.deepEqual(sequenceNeighbors([1,2,3],[3,1],2),{previous:1,next:3,position:2,total:3});
assert.equal(sequenceNeighbors([1,2],[1,2],9),null);
assert.equal(sequenceNeighbors([1,2],[2,1],1).previous,undefined);
assert.equal(sequenceNeighbors([1,2],[2,1],2).next,undefined);
const store=new Map();
function setup(ids,current,{blocked=false,source='/?q=book&page=2',activity=''}={}){
 const nodes=Object.fromEntries(['previous','next','position'].map(k=>[k,{attrs:{},setAttribute(k,v){this.attrs[k]=v;},removeAttribute(k){delete this.attrs[k];}}]));
 const nav={hidden:true,querySelector:selector=>nodes[selector.replace('[data-work-','').replace(']','')]};
 const info={id:'c9111e96-5304-438c-aeb5-a71322d4f5ea',source,activity,current,ids};
 const doc={querySelector:s=>s==='#work-sequence-data'?{textContent:JSON.stringify(info)}:nav};
 const replacements=[];
 const win={location:{origin:'http://local',href:`http://local/works/${current}/?from=x`},history:{state:{},replaceState:(...args)=>replacements.push(args)},sessionStorage:{getItem:k=>{if(blocked)throw Error();return store.get(k);},setItem:(k,v)=>{if(blocked)throw Error();store.set(k,v);}}};
 installWorkSequence(doc,win);return {nav,nodes,replacements};
}
let page=setup([1,2,3,4],2);assert.equal(page.nav.hidden,false);assert.equal(new URL(page.nodes.next.attrs.href,'http://local').pathname,'/works/3/');
page=setup([4,2,1],2);assert.equal(new URL(page.nodes.next.attrs.href,'http://local').pathname,'/works/4/');assert.equal(page.nodes.position.textContent,'2 / 3');assert.equal(page.replacements.length,1);
page=setup([2,1],1);assert.equal(page.nodes.previous.attrs['aria-disabled'],'true');
page=setup([8,9],8,{activity:'3',source:'/activities/3/'});assert.equal(new URL(page.nodes.next.attrs.href,'http://local').searchParams.get('activity'),'3');
page=setup([8,9],8,{blocked:true,activity:'3',source:'/activities/3/'});assert.equal(page.nav.hidden,false);
console.log('Frozen order survives ranking changes; removed candidates are skipped; endpoints, scope, reload and restricted storage passed.');
