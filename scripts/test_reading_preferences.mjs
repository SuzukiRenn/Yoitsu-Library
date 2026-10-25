import assert from 'node:assert/strict';
import {motionPreference} from '../static/motion-preference.mjs';
import {syncHeatLabels} from '../static/heat-display.mjs';
const events={},media={matches:false,addEventListener:(k,f)=>events.system=f,removeEventListener(){}};
const doc={documentElement:{dataset:{motionPreference:'system',hideHeat:'false'}},addEventListener:(k,f)=>events[k]=f,removeEventListener(){}};
const motion=motionPreference({matchMedia:()=>media},doc);let changes=[];
motion.addEventListener('change',event=>changes.push(event.matches));
assert.equal(motion.matches,false);doc.documentElement.dataset.motionPreference='reduce';events['booklist:preferences']();assert.equal(motion.matches,true);
doc.documentElement.dataset.motionPreference='system';media.matches=true;events.system();assert.equal(motion.matches,true);
media.matches=false;events.system();assert.equal(motion.matches,false);assert.deepEqual(changes,[true,true,false]);
function button(label){return {dataset:{},label,getAttribute(){return this.label;},setAttribute(k,v){this.label=v;}};}
const recommend=button('取消推荐，推荐总数 4'),like=button('点赞，点赞总数 8');
const scope={ownerDocument:doc,querySelectorAll:()=>[recommend,like]};
syncHeatLabels(scope);doc.documentElement.dataset.hideHeat='true';syncHeatLabels(scope);
assert.equal(recommend.label,'取消推荐');assert.equal(like.label,'点赞');
like.label='取消点赞，点赞总数 9';syncHeatLabels(scope);assert.equal(like.label,'取消点赞');
doc.documentElement.dataset.hideHeat='false';syncHeatLabels(scope);assert.equal(like.label,'取消点赞，点赞总数 9');
assert.equal(recommend.label,'取消推荐，推荐总数 4');
console.log('Effective user/system reduction and hidden/restored accessible work counts passed');
