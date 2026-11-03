import assert from 'node:assert/strict';
import {removeRetracted,installRetractionRefresh} from '../static/notification-retractions.mjs';
function fixture(){
  const list={rows:[],empty:null,querySelectorAll(){return [...this.rows];},querySelector(selector){return selector==='.empty-state'?this.empty:this.rows[0];},append(node){this.empty=node;}};
  list.ownerDocument={createElement(){return {};}};
  for(const id of [11,12,13])list.rows.push({dataset:{notification:String(id)},remove(){list.rows=list.rows.filter(row=>row!==this);}});
  return list;
}
const list=fixture();removeRetracted(list,[12,13]);assert.deepEqual(list.rows.map(row=>row.dataset.notification),['11']);
removeRetracted(list,[11]);assert.equal(list.empty.textContent,'暂无消息');const empty=list.empty;removeRetracted(list,[11]);assert.equal(list.empty,empty);
const preserved=fixture();removeRetracted(preserved,null);removeRetracted(preserved,['11',0,-12]);assert.equal(preserved.rows.length,3);

const live=fixture(),events={},windowEvents={},timers=new Map();let timerId=0,calls=0;const badge={};
const doc={hidden:false,querySelector:s=>s==='[data-notifications]'?live:badge,addEventListener:(name,fn)=>events[name]=fn};
const win={setTimeout(fn,delay){assert.ok(delay>=30000);timers.set(++timerId,fn);return timerId;},clearTimeout(id){timers.delete(id);},addEventListener:(name,fn)=>windowEvents[name]=fn};
const fetcher=async(url,options)=>{calls++;assert.equal(url,'/api/v1/notifications/unread/?since=11');assert.equal(options.cache,'no-store');return {ok:true,status:200,json:async()=>({unread_count:0,retracted_ids:[12]})};};
const settle=()=>new Promise(resolve=>setImmediate(resolve));
installRetractionRefresh(doc,win,fetcher);await settle();assert.equal(calls,1);assert.equal(live.rows.length,2);assert.equal(badge.hidden,true);assert.equal(timers.size,1);
doc.hidden=true;events.visibilitychange();assert.equal(timers.size,0);assert.equal(calls,1);
doc.hidden=false;events.visibilitychange();await settle();assert.equal(calls,2);
windowEvents.pagehide();assert.equal(timers.size,0);events['booklist:navigation-ready']();await settle();assert.equal(calls,2);
windowEvents.pageshow();await settle();assert.equal(calls,3);assert.equal(timers.size,1);
console.log('Notification retractions: precise removal, empty state, private polling, visibility, page return and cleanup passed.');
