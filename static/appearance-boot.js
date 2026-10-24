// Resolve before paint. This small blocking script has no network or identity access.
(()=>{
 let prefs;try{prefs=JSON.parse(localStorage.getItem('yoitsu:appearance:v1'));}catch{}
 const mode=['system','light','dark'].includes(prefs?.mode)?prefs.mode:'system';
 const color=['original','moss','blue','rose','brown'].includes(prefs?.color)?prefs.color:'original';
 const root=document.documentElement;root.dataset.appearanceMode=mode;root.dataset.colorTheme=color;
 root.dataset.theme=mode==='system'?(window.matchMedia('(prefers-color-scheme: dark)').matches?'dark':'light'):mode;
})();
