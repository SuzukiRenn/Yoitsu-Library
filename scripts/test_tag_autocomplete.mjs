import assert from 'node:assert/strict';
import {tagInputContext,completeTag,tagKey} from '../static/tag-autocomplete.mjs';

assert.deepEqual(tagInputContext('奇幻， 冒'), {start:3,end:5,query:'冒',others:['奇幻']});
assert.deepEqual(tagInputContext('奇幻,科,恋爱',4), {start:3,end:4,query:'科',others:['奇幻','恋爱']});
assert.deepEqual(completeTag('奇幻， 冒',5,'冒险'), {value:'奇幻，冒险，',caret:6});
assert.deepEqual(completeTag('奇幻,科,恋爱',4,'科幻'), {value:'奇幻,科幻,恋爱',caret:6});
assert.deepEqual(completeTag('校园',2,'校园'), {value:'校园，',caret:3});
assert.equal(tagKey(' ＳＦ '),tagKey('sf'));
assert.deepEqual(tagInputContext('奇幻\n校园，',6).others,['奇幻','校园']);
console.log('Tag completion handles the caret token, mixed separators, canonical names, and existing tags');
