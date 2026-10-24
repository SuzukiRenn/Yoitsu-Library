import assert from 'node:assert/strict';
import {installSpoilerReveal} from '../static/spoiler-reveal.mjs';
const handlers={};
installSpoilerReveal({addEventListener:(name,fn)=>{handlers[name]=fn;}});
const text={hidden:true,focus(options){this.focused=options;}};
const control={hidden:false};
const wrapper={querySelector:selector=>selector==='[data-spoiler-text]'?text:null};
const button={closest:selector=>selector==='.spoiler-control'?control:wrapper};
let prevented=false;
handlers.click({target:{closest:()=>button},preventDefault(){prevented=true;}});
assert.equal(control.hidden,true);
assert.equal(text.hidden,false);
assert.deepEqual(text.focused,{preventScroll:true});
assert.equal(prevented,true);
// Other actions, including reason likes, must never reveal a spoiler.
text.hidden=true;control.hidden=false;
handlers.click({target:{closest:()=>null},preventDefault(){throw Error('unrelated click intercepted');}});
assert.equal(text.hidden,true);assert.equal(control.hidden,false);
console.log('Spoiler clicks replace the placeholder with focused text; unrelated actions keep spoilers hidden.');
