import assert from 'node:assert/strict';
import {installTouchTooltip} from '../static/touch-tooltip.mjs';

function fixture(){
 const attrs=new Map(),events=new Map(),windowEvents=new Map(),timers=new Map();
 let now=0,next=0;
 const button={setAttribute:(k,v)=>attrs.set(k,v),removeAttribute:k=>attrs.delete(k),addEventListener:(k,fn)=>events.set(k,fn)};
 const win={setTimeout(fn,delay){const id=++next;timers.set(id,{at:now+delay,fn});return id;},clearTimeout:id=>timers.delete(id),addEventListener:(k,fn)=>windowEvents.set(k,fn)};
 installTouchTooltip({querySelector:()=>button},win);
 return {attrs,timers,fire:(type,event={})=>events.get(type)?.(event),leave:()=>windowEvents.get('pagehide')(),advance(ms){now+=ms;for(const [id,t] of timers)if(t.at<=now){timers.delete(id);t.fn();}}};
}

const touch=fixture();
touch.fire('pointerup',{pointerType:'touch'});
touch.fire('click',{detail:1});
assert.equal(touch.attrs.get('data-touch-tooltip'),'visible');
touch.advance(1499);
assert.equal(touch.attrs.get('data-touch-tooltip'),'visible');
touch.advance(1);
assert.equal(touch.attrs.get('data-touch-tooltip'),'hidden','persistent focus/hover must not keep the touch tooltip visible');

touch.fire('pointerup',{pointerType:'touch'});
touch.advance(1000);
touch.fire('pointerup',{pointerType:'touch'});
touch.advance(500);
assert.equal(touch.attrs.get('data-touch-tooltip'),'visible','a second tap gets a fresh timeout');
touch.advance(1000);
assert.equal(touch.attrs.get('data-touch-tooltip'),'hidden');

for(const [type,event] of [['pointerenter',{pointerType:'mouse'}],['pointerdown',{pointerType:'mouse'}],['keydown',{key:'Tab'}],['click',{detail:0}],['focusout',{}],['pointercancel',{}]]){
 const f=fixture();f.fire('pointerup',{pointerType:'touch'});f.fire(type,event);
 assert.equal(f.attrs.has('data-touch-tooltip'),false,`${type} restores ordinary tooltip behavior`);
 f.advance(2000);assert.equal(f.attrs.has('data-touch-tooltip'),false);
 assert.equal(f.timers.size,0);
}
const mouse=fixture();mouse.fire('pointerup',{pointerType:'mouse'});
assert.equal(mouse.attrs.size,0);assert.equal(mouse.timers.size,0);
const page=fixture();page.fire('pointerup',{pointerType:'pen'});page.leave();page.advance(2000);
assert.equal(page.attrs.size,0);assert.equal(page.timers.size,0);
assert.doesNotThrow(()=>installTouchTooltip({querySelector:()=>null},{}));
console.log('Touch tooltip timeout, repeated taps, mouse/keyboard handoff and cleanup passed.');
