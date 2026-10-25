export const APPEARANCE_KEY='yoitsu:appearance:v1';
export const DEFAULT_APPEARANCE=Object.freeze({mode:'system',color:'original',motion:'system',hideHeat:false});
export const MODES=['system','light','dark'];
export const COLORS=['original','moss','blue','rose','brown'];
export const MOTIONS=['system','reduce'];
export function normalizeAppearance(value){
 return {mode:MODES.includes(value?.mode)?value.mode:'system',color:COLORS.includes(value?.color)?value.color:'original',motion:MOTIONS.includes(value?.motion)?value.motion:'system',hideHeat:value?.hideHeat===true};
}
export function decodeAppearance(raw){try{return normalizeAppearance(JSON.parse(raw));}catch{return {...DEFAULT_APPEARANCE};}}
export function applyAppearance(root,value,systemDark=false,systemReduced=false){
 const prefs=normalizeAppearance(value);
 root.dataset.appearanceMode=prefs.mode;root.dataset.colorTheme=prefs.color;
 root.dataset.theme=prefs.mode==='system'?(systemDark?'dark':'light'):prefs.mode;
 root.dataset.motionPreference=prefs.motion;root.dataset.motion=prefs.motion==='reduce'||systemReduced?'reduce':'full';
 root.dataset.hideHeat=String(prefs.hideHeat);
 return prefs;
}
