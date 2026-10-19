import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {ALARM, listing, parsePage, readLinks, exportJob} from './core.mjs';
import {createController} from './controller.mjs';

const base = 'https://www.wenku8.net/modules/article/articlelist.php';
const url = page => `${base}?page=${page}`;
const page = (n, ids = [100 + n], next = n + 1) => ({url: url(n), is_listing: true,
  links: [...ids.map(id => `https://www.wenku8.net/book/${id}.htm`), ...(next ? [url(next)] : [])]});

function harness() {
  let clock = 100000, stored = {}, tab = {id: 7, url: url(1), status: 'complete'}, raw = page(1);
  const events = {alarms: [], navigation: [], reads: 0};
  const api = {
    storage: {local: {async get() {return structuredClone(stored);}, async set(value) {stored = structuredClone(value);}}},
    alarms: {async clear() {}, async create(name, value) {events.alarms.push({name, ...value});}},
    tabs: {async get(id) {if (id !== 7) throw new Error('Missing tab'); return {...tab};},
      async update(id, value) {events.navigation.push(value.url); tab.url = value.url; tab.status = 'loading';}},
    scripting: {async executeScript() {events.reads++; return [{result: structuredClone(raw)}];}}
  };
  return {controller: createController(api, () => clock), events, api,
    advance(ms = 30000) {clock += ms;},
    loaded(n, ids, next) {raw = page(n, ids, next); tab.url = url(n); tab.status = 'complete';},
    redirect(value) {tab.url = value;},
    restartWorker() {return createController(api, () => clock);}};
}

test('manifest grants only temporary active tab access, no credential or host permissions', async () => {
  const m = JSON.parse(await readFile(new URL('./manifest.json', import.meta.url)));
  assert.deepEqual(m.permissions, ['activeTab', 'scripting', 'storage', 'alarms']);
  assert.equal(m.host_permissions, undefined);
  assert.equal(m.externally_connectable, undefined);
});
test('rejects unsafe origins, credentials, paths, filters and duplicate page queries', () => {
  for (const bad of ['http://www.wenku8.net/modules/article/articlelist.php',
    'https://www.wenku8.net.evil.test/modules/article/articlelist.php',
    'https://user@www.wenku8.net/modules/article/articlelist.php',
    `${base}?page=1&page=2`, `${base}?page=0`, `${base}?sort=lastupdate`, `${base}#x`,
    'https://www.wenku8.net/login.php']) assert.throws(() => listing(bad));
  assert.equal(listing(base).url, url(1));
});
test('reads only bounded hrefs and public title marker, not account content or cookie', () => {
  globalThis.location = {href: url(1)};
  globalThis.document = {title: '轻小说列表 - 轻小说文库',
    get cookie() {throw new Error('Forbidden');},
    querySelectorAll(selector) {
      assert.equal(selector, 'a[href]');
      return [{getAttribute(name) {assert.equal(name, 'href'); return '/book/123.htm';}}];
    }};
  assert.deepEqual(readLinks(), {url: url(1), is_listing: true, links: ['/book/123.htm']});
  delete globalThis.document; delete globalThis.location;
});
test('deduplicates real IDs and only follows observed adjacent same-filter same-origin page', () => {
  const raw = page(1, [123, 123]);
  raw.links.push('/book/124.htm', 'https://evil.test/book/125.htm', '/book/126.htm?token=x',
    'https://user@www.wenku8.net/book/127.htm', 'https://wenku8.net/modules/article/articlelist.php?page=2',
    `${base}?page=2&fullflag=1`, url(220));
  assert.deepEqual(parsePage(raw, base), {url: url(1), book_ids: [123, 124], next_url: url(2)});
});
test('login, empty or redirected pages do not contribute records; terminal page stays bounded', () => {
  assert.throws(() => parsePage({...page(1), is_listing: false}, url(1)));
  assert.throws(() => parsePage({url: url(1), is_listing: true, links: []}, url(1)));
  assert.throws(() => parsePage(page(2), url(1)));
  assert.equal(parsePage(page(220, [999], null), url(220)).next_url, null);
});
test('validates user bounds before reading any page', async () => {
  const h = harness();
  for (const args of [[0,30],[21,30],[2,29],[2,301],[1.5,30]]) {
    await assert.rejects(h.controller.start(7, ...args));
  }
  assert.equal(h.events.reads, 0);
});
test('respects delay, survives service-worker restart, stops exactly at cap with unique IDs', async () => {
  const h = harness();
  await h.controller.start(7, 2, 30);
  await h.controller.tick();
  assert.equal(h.events.navigation.length, 0);
  h.advance();
  const restarted = h.restartWorker();
  await restarted.tick();
  assert.deepEqual(h.events.navigation, [url(2)]);
  assert.equal((await restarted.get()).phase, 'loading');
  h.loaded(2, [101, 102]);
  const result = await restarted.loaded(7);
  assert.equal(result.status, 'completed');
  assert.deepEqual(result.ids, [101, 102]);
  assert.equal(result.pages.length, 2);
  assert.equal(h.events.alarms[0].name, ALARM);
  assert.equal(exportJob(result).complete_catalog, false);
  h.advance(); await restarted.tick();
  assert.equal(h.events.navigation.length, 1);
});
test('pause, resume and tab ownership do not navigate unrelated tabs', async () => {
  const h = harness();
  await h.controller.start(7, 3, 30);
  await h.controller.pause(); h.advance(); await h.controller.tick();
  assert.equal(h.events.navigation.length, 0);
  await assert.rejects(h.controller.resume(8));
  await h.controller.resume(7); await h.controller.tick();
  assert.equal(h.events.navigation.length, 0);
  h.advance(); await h.controller.tick();
  assert.equal(h.events.navigation.length, 1);
});
test('redirects stop before page reads and preserve earlier results', async () => {
  const h = harness();
  await h.controller.start(7, 3, 30); h.advance(); await h.controller.tick();
  h.redirect('https://www.wenku8.net/login.php');
  const result = await h.controller.loaded(7);
  assert.equal(result.status, 'paused');
  assert.equal(result.ids.length, 1);
  assert.equal(h.events.reads, 1);
});
test('manual refresh and browser restart pause, existing results require explicit replacement', async () => {
  const h = harness();
  await h.controller.start(7, 3, 30);
  assert.equal((await h.controller.loaded(7)).status, 'paused');
  await assert.rejects(h.controller.start(7, 3, 30));
  await h.controller.resume(7);
  await h.controller.startup();
  assert.equal((await h.controller.get()).status, 'paused');
  await h.controller.removed(99);
  assert.equal((await h.controller.get()).pages.length, 1);
});
test('loading watchdog pauses, slow successful load can explicitly resume without refetching earlier pages', async () => {
  const h = harness();
  await h.controller.start(7, 3, 30); h.advance(); await h.controller.tick();
  h.advance(); assert.equal((await h.controller.tick()).status, 'paused');
  h.loaded(2); assert.equal((await h.controller.resume(7)).pages.length, 2);
  assert.equal(h.events.reads, 2);
});
test('last page completes immediately and a closed tab preserves accumulated results', async () => {
  const h = harness(); h.loaded(1, [200], null);
  assert.equal((await h.controller.start(7, 3, 30)).status, 'completed');
  assert.equal(h.events.navigation.length, 0);
  h.loaded(1); await h.controller.start(7, 3, 30, true); await h.controller.removed(7);
  assert.equal((await h.controller.get()).status, 'paused');
  assert.deepEqual(exportJob(await h.controller.get()).book_ids, [101]);
});
