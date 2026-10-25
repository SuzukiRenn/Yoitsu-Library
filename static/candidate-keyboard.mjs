// Move actual focus in visual result order; Enter uses native button/link activation.
export function installCandidateKeyboard({input,list,selector,onEscape=()=>{},isComposing=()=>false,onEnter=()=>{}}){
  const items=()=>list.getAttribute?.('aria-busy')==='true' ? [] : [...list.querySelectorAll(selector)].filter(item=>!item.disabled && !item.hidden && item.getClientRects().length);
  const composing=event=>isComposing() || event.isComposing || event.keyCode===229;
  input.addEventListener('keydown',event=>{
    if(composing(event) || event.altKey || event.ctrlKey || event.metaKey || event.shiftKey)return;
    if(['ArrowDown','ArrowUp'].includes(event.key)){
      const choices=items();if(!choices.length)return;
      event.preventDefault();choices[event.key==='ArrowDown'?0:choices.length-1].focus();
    }else if(event.key==='Escape'){onEscape();}
    else if(event.key==='Enter'){event.preventDefault();onEnter();}
  });
  list.addEventListener('keydown',event=>{
    if(composing(event) || event.altKey || event.ctrlKey || event.metaKey || event.shiftKey)return;
    const choices=items(),index=choices.indexOf(event.target);if(index<0)return;
    if(['ArrowDown','ArrowUp'].includes(event.key)){
      event.preventDefault();choices[(index+(event.key==='ArrowDown'?1:-1)+choices.length)%choices.length].focus();
    }else if(event.key==='Escape'){
      event.preventDefault();event.stopPropagation();onEscape();input.focus({preventScroll:true});
    }
  });
}
