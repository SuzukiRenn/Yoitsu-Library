import assert from 'node:assert/strict';
import {webcrypto} from 'node:crypto';
import {createPublicApi} from '../static/public-api.mjs';
import {startLiveInteractions,updateInteraction} from '../static/live-interactions.mjs';
if (!globalThis.crypto) globalThis.crypto = webcrypto;
const response = (body, status = 200) => ({ok: status < 400, status, json: async () => body});
const events = {}, requests = [], status = {textContent: ''};
const button = {dataset: {action: 'recommend', enabled: 'true'}, disabled: false};
const container = {dataset: {activity: '7', work: '11'}, querySelectorAll: () => [button],
  setAttribute() {}, removeAttribute() {}};
button.closest = () => container;
const doc = {hidden: false, querySelector: () => status, querySelectorAll: () => [container],
  addEventListener: (event, handler) => {events[event] = handler;}};
globalThis.location = {reload() {throw Error('Unexpected reload');}, assign() {throw Error('Unexpected navigation');}};
const fetcher = async (url, options) => {
  requests.push({url, options});
  if (url.endsWith('/csrf/')) return response({csrf_token: 'csrf-test'});
  if (url.endsWith('/identity/')) return response({status: 'ready'});
  return response({work: {id: 11, recommended: JSON.parse(options.body).enabled, like_count: 9}});
};
const renders = [], delays = [];
let finishPoll, failPoll = false;
const live = startLiveInteractions({doc, write: createPublicApi({fetcher}),
  render: (card, work) => {renders.push(work); button.dataset.enabled = String(!work.recommended); button.disabled = false;},
  fetcher: async () => failPoll ? response({}, 429) : new Promise(resolve => {finishPoll = resolve;}),
  schedule: (callback, ms) => {delays.push(ms);return 1;}, cancel() {}});
const click = {target: {closest: selector => selector === '[data-action]' ? button : null}};
await events.click(click);
assert.equal(requests.at(-1).url, '/api/v1/activities/7/works/11/recommend/');
assert.deepEqual(JSON.parse(requests.at(-1).options.body), {enabled: true});
assert.equal(requests.at(-1).options.headers['X-CSRFToken'], 'csrf-test');
assert.equal(renders.at(-1).like_count, 9);
await events.click(click);
assert.deepEqual(JSON.parse(requests.at(-1).options.body), {enabled: false});
const stale = live.poll();
await events.click(click);
const rendered = renders.length;
finishPoll(response({results: [{id: 11, recommended: false, like_count: 1}]}));
await stale;
assert.equal(renders.length, rendered, 'Old polling response must not undo a completed write');
const poll = live.poll();
finishPoll(response({results: [{id: 11, recommended: true, like_count: 20}]}));
await poll;
assert.equal(renders.at(-1).like_count, 20);
doc.hidden = true;
const before = renders.length;
await live.poll();
assert.equal(renders.length, before);
doc.hidden = false; failPoll = true;
await live.poll();
assert.equal(delays.at(-1), 60000);
const failedEvents = {};
startLiveInteractions({doc: {...doc, addEventListener: (name, handler) => {failedEvents[name] = handler;}},
  write: async () => {throw Error('Temporary failure');}, render() {throw Error('Must not render an unconfirmed write');},
  schedule() {return 1;}, cancel() {}});
await failedEvents.click(click);
assert.equal(status.textContent, 'Temporary failure');
assert.equal(button.disabled, false);
console.log('In-place writes, cancellation, polling, hidden tabs, stale-response guards and failure recovery passed');

// An empty shelf must keep polling; cards added later must join stats and writes.
const shelfEvents = {}, dynamicCards = [], refreshes = [];
const shelf = startLiveInteractions({
  doc: {...doc, hidden: false, querySelectorAll: () => dynamicCards,
    addEventListener: (name, handler) => {shelfEvents[name] = handler;}},
  refresh: async context => {refreshes.push(context.isCurrent()); return {ok:true};},
  fetcher: async url => {requests.push({url}); return response({results:[{id:11, like_count:42}]});},
  write: async () => ({work:{id:11, recommended:true}}),
  render: (card, work) => renders.push(work), schedule: () => 1, cancel() {}
});
await shelf.poll(); assert.deepEqual(refreshes, [true]);
dynamicCards.push(container);
await shelf.poll(); assert.equal(renders.at(-1).like_count, 42);
assert.equal(requests.at(-1).url, '/api/v1/activities/7/stats/?ids=11');
await shelfEvents.click(click); assert.equal(renders.at(-1).recommended, true);
const {membershipChanged} = await import('../static/live-shelf.mjs');
assert.equal(membershipChanged(['1','2'], ['2','1']), true, 'Live shelf must follow the selected server ordering');
assert.equal(membershipChanged(['1','2'], ['3','1']), true);
assert.equal(membershipChanged([], ['3']), true);
assert.equal(membershipChanged(['3'], []), true);
console.log('Empty shelves, dynamic card interactions and server-ordered live updates passed');

// A nested book citation must never be mistaken for the card's reason-list link.
for(const activity of ['', '7']){
 const item={id:38,reason:'如果喜欢龙王的工作！的话也可以看这本',parts:[{text:'如果喜欢'},{text:'龙王的工作！',url:'/works/195/'},{text:'的话也可以看这本'}]};
 const reference={textContent:'龙王的工作！',href:'/works/195/'};
 const reasonLink={textContent:'查看理由（1）',href:`/works/947/${activity?'?activity='+activity:''}#reasons`};
 const paragraph={textContent:item.reason,dataset:{referenceParts:JSON.stringify(item.parts)},querySelector:()=>null};
 const preview={dataset:{previewReasonId:'38'},querySelector(selector){
  if(selector==='p')return paragraph;
  if(selector==='a')return reference;
  if(selector===':scope > a.reason-link')return reasonLink;
  return null;
 }};
 const card={dataset:{work:'947',activity},querySelector:()=>null,querySelectorAll:()=>[],closest:()=>({querySelector:()=>preview})};
 for(let i=0;i<3;i++)updateInteraction(card,{available:true,writable:true,top_reason:item,reason_count:2});
 assert.equal(reference.textContent,'龙王的工作！','Stats polling must preserve nested citation text');
 assert.equal(reference.href,'/works/195/','Stats polling must preserve nested citation target');
 assert.equal(reasonLink.textContent,'查看理由（2）','Only the direct reason-list link should receive counts');
}
console.log('Public and theme stats polling keep citation labels/targets separate from the reason-list link.');
