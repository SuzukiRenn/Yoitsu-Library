import {localReturn,returnTo} from './navigation-context.mjs?v=20261104-rankings-1';
export function finishAccountLogin(result,next,win=window){
 const destination=localReturn(next,win.location.origin);
 if(result?.nickname_required===true){
  win.location.assign('/account/nickname/?'+new URLSearchParams({next:destination}));
 }else returnTo(destination,win);
}
