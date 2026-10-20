import assert from 'node:assert/strict';
import {installNavigationMotion} from '../static/navigation-motion.mjs';

function harness() {
  const events = {}, windowEvents = {}, timers = new Map(), navigations = [], animations = [];
  const reduced = {matches: false, addEventListener: (_, callback) => {reduced.change = callback;}};
  const main = {animate: () => {
    let finish;
    const animation = {finished: new Promise(resolve => {finish = resolve;}),
      finish: () => finish(), cancel() {this.cancelled = true;}};
    animations.push(animation); return animation;
  }};
  const win = {location: {href: 'http://local/', origin: 'http://local', pathname: '/', search: '',
    assign: url => navigations.push(url)}, matchMedia: () => reduced,
    addEventListener: (name, callback) => {windowEvents[name] = callback;},
    setTimeout: callback => {const id = Symbol(); timers.set(id, callback); return id;},
    clearTimeout: id => timers.delete(id)};
  installNavigationMotion({querySelector: () => main, addEventListener: (name, callback) => {events[name] = callback;}}, win);
  const click = (href = 'http://local/activities/', overrides = {}, linkOverrides = {}) => {
    const event = {button: 0, defaultPrevented: false, preventDefault() {this.defaultPrevented = true;},
      target: {closest: () => ({href, target: '', hasAttribute: () => false, ...linkOverrides})}, ...overrides};
    events.click(event); return event;
  };
  return {click, animations, navigations, timers, reduced, windowEvents};
}

let h = harness();
assert.equal(h.click().defaultPrevented, true);
assert.equal(h.navigations.length, 0);
h.animations[0].finish(); await Promise.resolve();
assert.deepEqual(h.navigations, ['http://local/activities/']);
assert.equal(h.timers.size, 0);
h.windowEvents.pageshow();
assert.equal(h.animations[0].cancelled, true);

for (const overrides of [{ctrlKey: true}, {metaKey: true}, {shiftKey: true}, {altKey: true}, {button: 1}, {defaultPrevented: true}]) {
  h = harness(); h.click(undefined, overrides); assert.equal(h.animations.length, 0);
}
for (const href of ['https://elsewhere/activities/', 'http://local/#main']) {
  h = harness(); assert.equal(h.click(href).defaultPrevented, false);
}
h = harness(); assert.equal(h.click(undefined, {}, {target: '_blank'}).defaultPrevented, false);
h = harness(); h.reduced.matches = true; assert.equal(h.click().defaultPrevented, false);
h = harness(); h.click(); h.click('http://local/recommend/');
h.animations[0].finish(); await Promise.resolve(); assert.equal(h.navigations.length, 0);
h.animations[1].finish(); await Promise.resolve(); assert.deepEqual(h.navigations, ['http://local/recommend/']);
h = harness(); h.click(); [...h.timers.values()][0](); h.animations[0].finish();
await Promise.resolve(); assert.equal(h.navigations.length, 1);
h = harness(); h.click(); h.reduced.matches = true; h.reduced.change();
assert.equal(h.navigations.length, 1); assert.equal(h.animations[0].cancelled, true);
h = harness(); h.click(); h.windowEvents.pagehide(); h.animations[0].finish();
await Promise.resolve(); assert.equal(h.navigations.length, 0);
console.log('Navigation motion preserves modified clicks, history restoration, reduced motion, latest navigation and timeout fallback.');
