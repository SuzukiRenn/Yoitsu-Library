import assert from 'node:assert/strict';
import {finishAccountLogin} from '../static/account-onboarding.mjs';
function result(required,next){const assigned=[];const win={location:{origin:'https://local.test',assign:value=>assigned.push(value)}};finishAccountLogin({nickname_required:required},next,win);return assigned[0];}
const source='/activities/3/?q=book&tag=2&page=4#reasons';
let target=new URL(result(true,source),'https://local.test');
assert.equal(target.pathname,'/account/nickname/');assert.equal(target.searchParams.get('next'),source);
for(const bad of ['https://evil.test/','//evil.test/','/api/v1/account/logout/','/account/nickname/']){
 target=new URL(result(true,bad),'https://local.test');assert.equal(target.pathname,'/account/nickname/');assert.equal(target.searchParams.get('next'),'/');
}
for(const required of [false,undefined,'true'])assert.equal(result(required,source),source);
console.log('Nickname setup: server flag routes once, safe return preserves filters and fragment, unsafe destinations rejected.');
