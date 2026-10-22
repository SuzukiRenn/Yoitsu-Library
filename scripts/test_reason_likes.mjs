import assert from 'node:assert/strict';
import {startReasonLikes} from '../static/reason-likes.mjs';
const events={}, sent=[], attrs={};
const button={dataset:{reasonId:'21',work:'9',activity:'3',enabled:'true',writable:'true'},disabled:false,
 setAttribute:(name,value)=>{attrs[name]=value;}};
button.children=[];button.ownerDocument={createElement:()=>({dataset:{},textContent:''})};
button.querySelector=s=>button.children.find(c=>s==='[data-reason-label]'?'reasonLabel' in c.dataset:'reasonCount' in c.dataset);
button.replaceChildren=(...children)=>{button.children=children;};
Object.defineProperty(button,'textContent',{get:()=>button.children.map(c=>c.textContent).join('')});
const doc={hidden:false,querySelector:s=>s==='#action-status'?status:null,
 querySelectorAll:s=>s==='[data-reason-like]'?[button]:[],
 addEventListener:(name,fn)=>{(events[name]??=[]).push(fn);},dispatchEvent:()=>{}};
const status={textContent:''};let finish,fail=false;
const win={clearTimeout(){},setTimeout(){return 1;},addEventListener(){},CustomEvent:class{}};
startReasonLikes({doc,win,write:(url,payload)=>{sent.push({url,payload});return new Promise((resolve,reject)=>{finish=()=>fail?reject(Error('offline')):resolve({reason:{id:21,reason:'理由',like_count:payload.enabled?1:0,liked:payload.enabled,writable:true}});});}});
const event={target:{closest:s=>s==='[data-reason-like]'?button:null},preventDefault(){}};
let pending=events.click[0](event);
assert.equal(button.disabled,true);await events.click[0](event);assert.equal(sent.length,1);
assert.equal(sent[0].url,'/api/v1/activities/3/works/9/reasons/21/like/');
assert.deepEqual(sent[0].payload,{enabled:true});finish();await pending;
assert.equal(button.disabled,false);assert.equal(button.textContent,'已赞同 · 1');assert.equal(attrs['aria-pressed'],'true');
pending=events.click[0](event);assert.deepEqual(sent[1].payload,{enabled:false});finish();await pending;
assert.equal(button.textContent,'赞同 · 0');assert.equal(button.dataset.enabled,'true');
fail=true;pending=events.click[0](event);finish();await pending;
assert.equal(status.textContent,'offline');assert.equal(button.disabled,false);assert.equal(button.dataset.enabled,'true');
console.log('Reason reactions use independent endpoints, prevent duplicate clicks, cancel in place and recover from failure.');
