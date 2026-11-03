import assert from 'node:assert/strict';
import {installRecentViews} from '../static/recent-views.mjs';
function setup({owner=1,hidden=false,activity='',failure=false}={}){
 const events={},windowEvents={},calls=[],redirects=[];
 const detail={dataset:{recentWork:'7',recentActivity:activity}},toggle={checked:true,disabled:false,addEventListener:(n,f)=>events['toggle:'+n]=f},status={textContent:''};
 const doc={hidden,body:{dataset:{readerId:String(owner)}},querySelector:s=>s==='[data-recent-work]'?detail:s==='[data-recent-tracking]'?toggle:status,addEventListener:(n,f)=>events[n]=f};
 const win={location:{origin:'http://local',pathname:'/account/',search:'?tab=recent',assign:u=>redirects.push(u)},addEventListener:(n,f)=>windowEvents[n]=f};
 installRecentViews(doc,win,async(url,payload)=>{calls.push({url,payload});if(failure)throw Error('synthetic network');return {};});
 return {doc,events,windowEvents,calls,redirects,toggle,status};
}
let f=setup();assert.deepEqual(f.calls[0].payload,{account_id:1,work_id:7,activity_id:null});
f.events.visibilitychange();assert.equal(f.calls.length,1,'same visible document records once');
f.windowEvents.pageshow({persisted:true});assert.equal(f.calls.length,2,'returning through BFCache counts as a new view');
f=setup({hidden:true,activity:'3'});assert.equal(f.calls.length,0);f.doc.hidden=false;f.events.visibilitychange();assert.equal(f.calls[0].payload.activity_id,3);
assert.equal(setup({owner:0}).calls.length,0,'visitors do not collect account history');
f=setup();f.toggle.checked=false;await f.events['toggle:change']();assert.deepEqual(f.calls.at(-1).payload,{account_id:1,action:'tracking',enabled:false});
f=setup({failure:true});await Promise.resolve();f.toggle.checked=false;await f.events['toggle:change']();assert.equal(f.toggle.checked,true);assert.equal(f.toggle.disabled,false);assert.match(f.status.textContent,/network/);
f=setup();const button={disabled:false,dataset:{recentRemove:'7'},hasAttribute:()=>false};await f.events.click({target:{closest:()=>button}});assert.equal(f.calls.at(-1).payload.work_id,7);assert.equal(f.redirects[0],'/account/?tab=recent');
console.log('Account history UI: visibility, one-time recording, BFCache, owner isolation, preference failure and removal passed.');
