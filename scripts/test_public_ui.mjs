import assert from 'node:assert/strict';
import {webcrypto} from 'node:crypto';
import {createPublicApi} from '../static/public-api.mjs';
if (!globalThis.crypto) globalThis.crypto = webcrypto;

const response = (body, status = 200) => ({ok: status < 400, status, json: async () => body});
const requests = [];
let calls = 0, key = 0;
const write = createPublicApi({newKey: () => `key-${++key}`, fetcher: async (url, options) => {
  requests.push({url, options});
  if (url.endsWith('/csrf/')) return response({csrf_token: 'csrf-test'});
  if (url.endsWith('/identity/')) return response({status: 'ready'});
  calls++;
  if (calls === 1) throw Error('Uncertain network result');
  return response({work: {recommendation_count: 1, like_count: 0}});
}});
await assert.rejects(write('/recommend/', {enabled: true}), /network/);
await write('/recommend/', {enabled: true});
assert.equal(requests[2].options.headers['Idempotency-Key'], requests[3].options.headers['Idempotency-Key']);
assert.equal(requests[2].options.headers['X-CSRFToken'], 'csrf-test');
assert.equal(requests[2].options.credentials, 'same-origin');
await write('/recommend/', {enabled: false});
assert.notEqual(requests[3].options.headers['Idempotency-Key'], requests[4].options.headers['Idempotency-Key']);
assert.equal(requests.filter(item => item.url.endsWith('/identity/')).length, 1);
let identityCount = 0;
const rejected = createPublicApi({fetcher: async url => {
  if (url.endsWith('/csrf/')) return response({csrf_token: 'test'});
  if (url.endsWith('/identity/')) { identityCount++; return response({status: 'ready'}); }
  return response({error: {code: 'identity_required', message: '身份失效'}}, 401);
}});
await assert.rejects(rejected('/like/', {enabled: true}), error => error.code === 'identity_required');
assert.equal(identityCount, 1); // Never silently issue a new identity and transfer the action.
console.log('Public write CSRF, identity, retry-key and explicit-state tests passed');

await import('./test_live_interactions.mjs');
