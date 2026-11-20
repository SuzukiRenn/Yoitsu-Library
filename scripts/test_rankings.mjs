import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';

const source=await readFile(new URL('../static/rankings.mjs',import.meta.url),'utf8');
const data=JSON.parse(await readFile(new URL('../static/rankings/data.json',import.meta.url),'utf8'));
const AsyncFunction=Object.getPrototypeOf(async function(){}).constructor;
async function open(search='',fixture=data){
 const elements=new Map();
 const element=id=>{if(!elements.has(id))elements.set(id,{innerHTML:'',textContent:'',hidden:true,disabled:false,attributes:{},listeners:{},
  setAttribute(k,v){this.attributes[k]=v;},removeAttribute(k){delete this.attributes[k];},
  addEventListener(k,v){this.listeners[k]=v;},querySelector(){return null;},focus(){},showModal(){this.open=true;},close(){this.open=false;}});return elements.get(id);};
 const document={getElementById:element,querySelector:()=>element('page'),addEventListener(){},dispatchEvent(){}};
 const state={url:''};
 await new AsyncFunction('document','location','history','fetch','CustomEvent',source)(document,{search,pathname:'/rankings/'},
  {replaceState(a,b,url){state.url=url;}},async()=>({ok:true,json:async()=>structuredClone(fixture)}),class {});
 return {element,state,click:(id,target={closest:()=>null})=>element(id).listeners.click({target})};
}
const latest=await open();
assert.equal(latest.element('selected-year').textContent,'2027');
assert.equal(latest.state.url,'/rankings/?year=2027&category=bunko');
assert.equal(latest.element('next-year').disabled,true);
assert.match(latest.element('rankings-panel').innerHTML,/败犬女主太多了/);
assert.equal(latest.element('list-end').textContent,'已展示 10 / 20 部');
latest.click('load-more');
assert.equal(latest.element('list-end').textContent,'已展示 20 / 20 部');
assert.equal(latest.state.url,'/rankings/?year=2027&category=bunko&shown=20');
latest.click('rankings-panel',{closest:()=>({dataset:{entry:'0'}})});
const modal=latest.element('work-dialog-content').innerHTML;
assert.match(modal,/2027 年/);assert.match(modal,/2025 年/);assert.match(modal,/2026 年/);
assert.doesNotMatch(modal,/<a\b/);assert.equal(latest.element('work-dialog').open,true);
latest.click('category-tabs',{closest:()=>({dataset:{category:'novels'}})});
assert.equal(latest.element('list-end').textContent,'已展示 10 / 10 部');
assert.match(latest.element('rankings-panel').innerHTML,/崩坏世界的魔杖匠人/);
latest.click('previous-year');assert.equal(latest.element('selected-year').textContent,'2026');
latest.click('next-year');assert.equal(latest.element('selected-year').textContent,'2027');
assert.equal((await open('?year=2026&category=bunko')).element('selected-year').textContent,'2026');
assert.equal((await open('?year=1900')).element('selected-year').textContent,'2027');
const future=structuredClone(data);future.years.push({...structuredClone(data.years.at(-1)),year:2028});
assert.equal((await open('',future)).element('selected-year').textContent,'2028');
console.log('Rankings frontend: latest year, explicit year, paging, departments, history modal and future default passed.');

assert.match((await open()).element('rankings-panel').innerHTML,/class="rank-honor"/);
const old=await open('?year=2012&category=overall');
assert.match(old.element('rankings-panel').innerHTML,/殿堂级/);
assert.match(old.element('rankings-panel').innerHTML,/2020 年入选（当前荣誉）/);
const noAwards=structuredClone(data);delete noAwards.hallOfFame;
assert.doesNotMatch((await open('',noAwards)).element('rankings-panel').innerHTML,/class="rank-honor"/);
const rowFixture=structuredClone(data);
rowFixture.hallOfFame[rowFixture.years.at(-1).sections[0].entries[3].workKey]={label:'殿堂级',inductedYear:2027};
assert.match((await open('',rowFixture)).element('rankings-panel').innerHTML,/<span class="rank-number">04<span class="rank-honor"/);
console.log('Hall of fame: featured and ordinary ranks, historical series, current-honor tooltip and absent-metadata compatibility passed.');

assert.match((await open()).element('rankings-panel').innerHTML,/2027 年达到殿堂条件（当前荣誉）/);
