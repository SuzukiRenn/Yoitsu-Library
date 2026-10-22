import assert from 'node:assert/strict';
import {updateInteraction} from '../static/live-interactions.mjs';
const count=()=>({textContent:'1',hasAttribute:()=>true,classList:{remove(){},add(){}},offsetWidth:1});
const counts=[count(),count()];
const buttons=['recommend','like'].map(action=>{const label={textContent:''};return {dataset:{action},setAttribute(){},querySelector:()=>label,closest:()=>null,label};});
const note={textContent:'',hidden:true};
const container={dataset:{work:'1'},querySelectorAll:s=>s==='[data-action]'?buttons:counts,
 querySelector:s=>s==='[data-live-note]'?note:null,closest:()=>null};
for(const selected of [false,true,false]){
 updateInteraction(container,{available:true,writable:false,recommended:selected,liked:selected,recommendation_count:selected?12:11,like_count:selected?7:6});
 assert.deepEqual(counts.map(c=>c.textContent),selected?['12','7']:['11','6']);
 assert.deepEqual(buttons.map(b=>b.label.textContent),selected?['已推荐','已赞']:['推荐','点赞']);
 assert.deepEqual(buttons.map(b=>b.dataset.enabled),[String(!selected),String(!selected)]);
}
console.log('Combined labels and live counts survive recommend/like selection and withdrawal without replacing count elements.');

for(const value of [0,9,10,99,100,1000000000]){
 updateInteraction(container,{available:true,writable:false,recommended:false,liked:false,recommendation_count:value,like_count:value});
 assert.deepEqual(counts.map(c=>c.textContent),[value>99?'99+':String(value),value>99?'99+':String(value)]);
}
console.log('Compact counts reserve 99+; 9/10/99/100/large counts and selection stay stable.');

// A live recommendation or withdrawal must update reason access without reload.
const originalDocument = globalThis.document;
globalThis.document = {createElement: () => ({className: '', hidden: false})};
let reasonLink;
const actions = {querySelector: () => reasonLink, append: link => {reasonLink = link;}};
const reasonContainer = {dataset: {work: '17'}, querySelectorAll: () => [], closest: () => null,
  querySelector: selector => selector === '.action-buttons' ? actions : selector === '[data-live-note]' ? note : null};
for (const recommended of [false, true, false, true]) {
  updateInteraction(reasonContainer, {recommended, recommendation_count: 2, like_count: 1, own_reason: ''});
  assert.equal(Boolean(reasonLink && !reasonLink.hidden), recommended);
  if (recommended) assert.equal(reasonLink.href, '/recommend/?work=17&edit=1');
}
globalThis.document = originalDocument;
console.log('Public reason entry follows the current user recommendation, including withdrawal and re-recommendation.');
