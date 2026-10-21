import assert from 'node:assert/strict';
import {safeNext} from '../static/account.mjs';

for(const [input, expected] of [
  ['/recommend/?work=1&edit=1#reason','/recommend/?work=1&edit=1#reason'],
  ['https://evil.example/','/'], ['//evil.example/','/'],
  ['javascript:alert(1)','/'], ['\\\\evil.example/','/'],
  ['http://127.0.0.1:8765/','/'], ['http://127.0.0.1:8877/activities/1/','/activities/1/'],
  [null,'/'],
])assert.equal(safeNext(input,'http://127.0.0.1:8877'),expected,input);
console.log('Account return URLs stay on the identity origin, preserving local paths and queries');
