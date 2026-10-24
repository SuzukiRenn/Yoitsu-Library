export const APPEARANCE_KEY='yoitsu:appearance:v1';
export const DEFAULT_APPEARANCE=Object.freeze({mode:'system',color:'original'});
export const MODES=['system','light','dark'];
export const COLORS=['original','moss','blue','rose','brown'];
export function normalizeAppearance(value){
 return {mode:MODES.includes(value?.mode)?value.mode:'system',color:COLORS.includes(value?.color)?value.color:'original'};
}
export function decodeAppearance(raw){try{return normalizeAppearance(JSON.parse(raw));}catch{return {...DEFAULT_APPEARANCE};}}
export function applyAppearance(root,value,systemDark=false){
 const prefs=normalizeAppearance(value);
 root.dataset.appearanceMode=prefs.mode;root.dataset.colorTheme=prefs.color;
 root.dataset.theme=prefs.mode==='system'?(systemDark?'dark':'light'):prefs.mode;
 return prefs;
}
