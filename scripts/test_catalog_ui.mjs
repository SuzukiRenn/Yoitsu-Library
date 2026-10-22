import assert from 'node:assert/strict';
import {createSearchController} from '../static/catalog-search.mjs';

const pending = [], outputs = [], statuses = [];
const controller = createSearchController({fetcher: (url, options) => new Promise(resolve => pending.push({url, options, resolve})), onResult: result => outputs.push(result), onStatus: (...args) => statuses.push(args)});
const first = controller.run('旧书名');
const second = controller.run('新书名');
assert.equal(pending[0].options.signal.aborted, true);
pending[1].resolve({ok: true, json: async () => ({results: [{id: 2}]})});
await second;
pending[0].resolve({ok: true, json: async () => ({results: [{id: 1}]})});
await first;
assert.deepEqual(outputs.map(payload => payload.results), [[{id: 2}]]);
const cancelled = controller.run('取消查询'); controller.cancel();
pending[2].resolve({ok: true, json: async () => ({results: [{id: 3}]})}); await cancelled;
assert.equal(outputs.length, 1);
const invalid = controller.run('📚'.repeat(301)); await invalid;
assert.equal(pending.length, 3);
const errors = [];
const failing = createSearchController({fetcher: async () => ({ok: false, json: async () => ({error: {message: '请求过于频繁'}})}), onResult: () => assert.fail('Error rendered as candidates'), onStatus: (message, busy) => errors.push([message, busy])});
await failing.run('书名'); assert.deepEqual(errors.at(-1), ['请求过于频繁', false]);
const offline = createSearchController({fetcher: async () => {throw Error('offline');}, onResult: () => assert.fail(), onStatus: (message, busy) => errors.push([message, busy])});
await offline.run('书名'); assert.equal(errors.at(-1)[1], false);
console.log('5 UI controller checks passed: stale response, cancellation, codepoint limit, HTTP error, network error');

const pages = [], rendered = [], pagingStatus = [];
const paging = createSearchController({
  fetcher: (url, options) => new Promise(resolve => pages.push({url, options, resolve})),
  onResult: (payload, mode) => rendered.push({payload, mode}),
  onStatus: (text, busy) => pagingStatus.push({text, busy})
});
const initial = paging.run('妹妹');
pages[0].resolve({ok:true,json:async()=>({query:'妹妹',results:[{id:1},{id:2}],total:4,next_offset:2,has_more:true})});
await initial;
assert.equal(pagingStatus.at(-1).text,'已展示 2 部／共匹配 4 部');
const more = paging.loadMore();
await paging.loadMore();
assert.equal(pages.length,2,'Repeated load-more click must share the pending request');
assert.equal(new URL(pages[1].url,'http://localhost').searchParams.get('offset'),'2');
pages[1].resolve({ok:true,json:async()=>({query:'妹妹',results:[{id:2},{id:3}],total:4,next_offset:4,has_more:true})});
await more;
assert.deepEqual(rendered[1].payload.results,[{id:3}]);
assert.equal(rendered[1].payload.loaded,3);
assert.equal(rendered[1].mode.append,true);
const failedMore=paging.loadMore();
pages[2].resolve({ok:false,json:async()=>({error:{message:'临时失败'}})});
await failedMore;
assert.equal(rendered.length,2);
const retry=paging.loadMore();
assert.equal(new URL(pages[3].url,'http://localhost').searchParams.get('offset'),'4');
const replacement=paging.run('新关键词');
assert.equal(pages[3].options.signal.aborted,true);
pages[4].resolve({ok:true,json:async()=>({query:'新关键词',results:[{id:8}],total:1,next_offset:null,has_more:false})});
await replacement;
pages[3].resolve({ok:true,json:async()=>({query:'妹妹',results:[{id:4}],total:4,next_offset:null,has_more:false})});
await retry;
assert.equal(rendered.length,3,'Old load-more response must not enter the new query');
assert.equal(rendered.at(-1).payload.loaded,1);
assert.deepEqual(rendered.at(-1).payload.results,[{id:8}]);
await paging.loadMore();
assert.equal(pages.length,5,'No request after the last page');
console.log('Candidate pagination checks passed: append, counts, deduplication, double click, retry, query change and exhaustion');
