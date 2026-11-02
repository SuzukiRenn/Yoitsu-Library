import assert from 'node:assert/strict';
import {safeNext, notificationText} from '../static/account.mjs';

for(const [input, expected] of [
  ['/recommend/?work=1&edit=1#reason','/recommend/?work=1&edit=1#reason'],
  ['https://evil.example/','/'], ['//evil.example/','/'],
  ['javascript:alert(1)','/'], ['\\\\evil.example/','/'],
  ['http://127.0.0.1:8765/','/'], ['http://127.0.0.1:8877/activities/1/','/activities/1/'],
  [null,'/'],
])assert.equal(safeNext(input,'http://127.0.0.1:8877'),expected,input);
console.log('Account return URLs stay on the identity origin, preserving local paths and queries');

for (const [kind, text] of [['work_like','读者 赞了你推荐的《作品》'],['reason_like','读者 赞同了你为《作品》写的推荐理由'],['co_recommendation','读者 也推荐了你推荐的《作品》']]) {
  assert.equal(notificationText({kind,actor:{display_name:'读者'},work_title:'作品'}),text);
}
