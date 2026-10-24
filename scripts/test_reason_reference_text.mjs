import assert from 'node:assert/strict';
import {renderReasonText} from '../static/reason-reference-text.mjs';
import {normalizedReferenceValue,referenceQuery} from '../static/reason-reference-editor.mjs';
const doc={createElement:tag=>({tag}),createTextNode:text=>({textContent:text})};
const node={ownerDocument:doc,dataset:{},replaceChildren(...children){this.children=children;this.textContent=children.map(c=>c.textContent).join('');}};
renderReasonText(node,{reason:'前书后',parts:[{text:'前'},{text:'书',url:'/works/2/'},{text:'后'}]});
assert.equal(node.children[1].href,'/works/2/');assert.equal(node.textContent,'前书后');
renderReasonText(node,{reason:'恶意',parts:[{text:'恶意',url:'https://evil.test/'}]});assert.equal(node.children[0].href,undefined);
assert.deepEqual(normalizedReferenceValue(' 😀书名 ',[{work_id:2,start:2,end:4}]),{text:'😀书名',references:[{work_id:2,start:1,end:3}]});
assert.throws(()=>normalizedReferenceValue('x'.repeat(201),[]));
console.log('Reference rendering accepts only in-site URLs; Unicode offsets and visible 200-character limit passed.');

for(const text of ['黑猫，，喜欢','龙王','/','/   ','https://example.com/book','/龙王\n普通文字'])assert.equal(referenceQuery(text),null,text);
assert.deepEqual(referenceQuery('黑猫，，喜欢/龙王'),{query:'龙王',start:6,end:9});
assert.deepEqual(referenceQuery('😀/龙'),{query:'龙',start:1,end:3});
assert.deepEqual(referenceQuery('/ 狼与香辛料 '),{query:'狼与香辛料',start:0,end:8});
assert.deepEqual(referenceQuery('/龙王 与 /温泉'),{query:'温泉',start:6,end:9});
console.log('Only explicit slash commands trigger suggestions; slash replacement, Unicode offsets and normal prose/URLs passed.');
