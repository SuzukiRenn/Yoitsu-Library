import assert from 'node:assert/strict';
import {installEmailLogin} from '../static/email-login.mjs';

function fixture(post,purpose='login'){
 const events={},windowEvents={},intervals=new Map();let id=0;
 const email={value:'123456@qq.com',addEventListener:(n,f)=>events['email:'+n]=f,reportValidity:()=>true};
 const code={value:'123456',focus(){this.focused=true;}};
 const send={disabled:false,addEventListener:(n,f)=>events['send:'+n]=f};
 const submit={disabled:false},status={textContent:''},redirects=[];
 const fields={'[name=email]':email,'[name=code]':code,'[data-send-code]':send,'[type=submit]':submit,'[role=status]':status};
 const form={dataset:{emailLogin:purpose},querySelector:s=>fields[s],addEventListener:(n,f)=>events[n]=f};
 const win={setInterval:f=>{intervals.set(++id,f);return id;},clearInterval:n=>intervals.delete(n),location:{assign:u=>redirects.push(u)},addEventListener:(n,f)=>windowEvents[n]=f};
 installEmailLogin(post,'/?tag=2',{querySelector:()=>form},win);
 return {email,code,send,submit,status,redirects,intervals,events,windowEvents};
}

let now=100000;const oldNow=Date.now;Date.now=()=>now;
try{
 const calls=[];
 const f=fixture(async(url,data)=>{calls.push({url,data});return {challenge:'token',retry_after:60};});
 await f.events.submit({preventDefault(){}});
 assert.equal(calls.length,0);assert.match(f.status.textContent,/先获取/);
 await f.events['send:click']();
 assert.equal(f.send.disabled,true);assert.equal(f.send.textContent,'60 秒后重发');assert.equal(f.code.focused,true);
 now+=61000;for(const tick of f.intervals.values())tick();assert.equal(f.send.disabled,false);
 await f.events.submit({preventDefault(){}});
 assert.equal(calls[1].url,'/api/v1/account/email/login/');assert.equal(calls[1].data.email,'123456@qq.com');
 assert.deepEqual(f.redirects,['/?tag=2']);
 f.email.value='654321@qq.com';f.events['email:input']();
 await f.events.submit({preventDefault(){}});assert.equal(calls.length,2,'changing email invalidates its outstanding challenge');
 f.windowEvents.pagehide();assert.equal(f.intervals.size,0);

 let release;
 const pending=fixture(()=>new Promise(resolve=>{release=resolve;}));
 const sending=pending.events['send:click']();
 assert.equal(pending.send.disabled,true);
 pending.email.value='654321@qq.com';pending.events['email:input']();release({challenge:'old-token',retry_after:60});await sending;
 await pending.events.submit({preventDefault(){}});assert.equal(pending.redirects.length,0);
 assert.match(pending.status.textContent,/先获取/);
 pending.windowEvents.pagehide();

 const binding=fixture(async()=>({challenge:'token',retry_after:60}),'bind');
 await binding.events['send:click']();await binding.events.submit({preventDefault(){}});
 assert.deepEqual(binding.redirects,['/account/settings/']);binding.windowEvents.pagehide();
 assert.doesNotThrow(()=>installEmailLogin(()=>{},'/',{querySelector:()=>null},{}));
 console.log('Email login: countdown, missing challenge, edit/send race, migration redirect and cleanup passed.');
}finally{Date.now=oldNow;}
