import {normalize} from './text.mjs';
import {appendEditionBadge} from './edition-badge.mjs?v=20261024-editions-1';
const length=text=>Array.from(text).length;
export function normalizedReferenceValue(text,references){
 text=text.replace(/\r\n?/g,'\n');
 const normalized=normalize(text,'reason'),start=length(text.slice(0,text.indexOf(normalized)));
 return {text:normalized,references:references.filter(r=>r.start>=start && r.end<=start+length(normalized)).map(r=>({...r,start:r.start-start,end:r.end-start}))};
}
// Only an explicit / command opens book suggestions; ordinary prose stays quiet.
export function referenceQuery(prefix){
 const slash=prefix.lastIndexOf('/');
 if(slash<0 || /[A-Za-z0-9:/]$/.test(prefix.slice(0,slash)))return null;
 const typed=prefix.slice(slash+1),query=typed.trim();
 if(!query || typed.includes('\n') || length(query)>100)return null;
 return {query,start:length(prefix.slice(0,slash)),end:length(prefix)};
}
export function createReasonReferenceEditor(textarea,{exclude=()=>null,fetcher=fetch}={}){
 const doc=textarea.ownerDocument,win=doc.defaultView,wrap=textarea.closest('.reason-editor');
 const editor=doc.createElement('div');editor.className='reason-reference-editor';editor.id='reason-reference-input';
 doc.querySelector('label[for="recommend-reason"]')?.setAttribute('for',editor.id);editor.contentEditable='true';editor.setAttribute('role','textbox');editor.setAttribute('aria-label','推荐理由');editor.setAttribute('aria-multiline','true');editor.setAttribute('aria-describedby','reason-writing-hint reason-length');editor.setAttribute('aria-autocomplete','list');editor.dataset.placeholder=textarea.placeholder;
 const popup=doc.createElement('div');popup.className='reason-reference-options';popup.id='reason-reference-options';popup.setAttribute('role','listbox');popup.setAttribute('aria-label','引用作品候选');popup.hidden=true;
 textarea.before(editor);wrap.append(popup);textarea.hidden=true;
 editor.setAttribute('aria-controls',popup.id);
 let composing=false,readonly=false,timer,request,generation=0,replacement=null,options=[],active=0;
 function close(){win.clearTimeout(timer);request?.abort();generation++;popup.hidden=true;editor.removeAttribute('aria-activedescendant');editor.setAttribute('aria-expanded','false');}
 function serialize(){
  let text='';const references=[];
  function walk(node){
   if(node.nodeType===3){text+=node.nodeValue;return;}
   if(node.nodeType!==1)return;
   if(node.dataset.referenceWork){const start=length(text);text+=node.textContent;references.push({work_id:Number(node.dataset.referenceWork),start,end:length(text)});return;}
   if(node.tagName==='BR'){text+='\n';return;}
   if(node!==editor && ['DIV','P'].includes(node.tagName) && text && !text.endsWith('\n'))text+='\n';
   for(const child of node.childNodes)walk(child);
  }
  walk(editor);return {text,references};
 }
 function sync(){textarea.value=serialize().text;textarea.dispatchEvent(new win.Event('input',{bubbles:true}));}
 function pill(text,id){const span=doc.createElement('span');span.className='reason-citation';span.contentEditable='false';span.dataset.referenceWork=String(id);span.textContent=text;return span;}
 function setValue(text,references=[]){
  close();const nodes=[];let position=0;
  for(const ref of Array.isArray(references)?references:[]){
   const chars=Array.from(text);if(ref.start<position || ref.end>chars.length || ref.start>=ref.end)continue;
   if(ref.start>position)nodes.push(doc.createTextNode(chars.slice(position,ref.start).join('')));
   nodes.push(pill(chars.slice(ref.start,ref.end).join(''),ref.work_id));position=ref.end;
  }
  nodes.push(doc.createTextNode(Array.from(text).slice(position).join('')));editor.replaceChildren(...nodes);textarea.value=text;
 }
 function pointAt(offset){
  const walker=doc.createTreeWalker(editor,win.NodeFilter.SHOW_TEXT);let node;
  while((node=walker.nextNode())){const size=length(node.nodeValue);if(offset<=size)return [node,Array.from(node.nodeValue).slice(0,offset).join('').length];offset-=size;}
  return [editor,editor.childNodes.length];
 }
 function rangeAt(start,end){const range=doc.createRange();range.setStart(...pointAt(start));range.setEnd(...pointAt(end));return range;}
 function caret(range){const selection=win.getSelection();selection.removeAllRanges();selection.addRange(range);}
 function insertText(text){
  if(readonly)return;
  text=text.replace(/\r\n?/g,'\n');
  const selection=win.getSelection();if(!selection.rangeCount)return;
  const range=selection.getRangeAt(0);if(!editor.contains(range.commonAncestorContainer))return;
  range.deleteContents();const node=doc.createTextNode(text);range.insertNode(node);range.setStartAfter(node);range.collapse(true);caret(range);sync();
 }
 function position(){
  if(!replacement)return;const rect=replacement.range.getBoundingClientRect(),base=wrap.getBoundingClientRect();
  popup.style.left=`${Math.max(0,Math.min(rect.left-base.left,base.width-popup.offsetWidth))}px`;
  const below=win.innerHeight-rect.bottom,above=rect.top;
  const top=below<180 && above>below?rect.top-base.top-popup.offsetHeight-8:rect.bottom-base.top+8;
  popup.style.top=`${Math.max(0,top)}px`;
 }
 function choose(index){
  if(readonly || !replacement || !options[index])return;
  const work=options[index],state=serialize();
  if(state.references.length>=8){close();return;}
  if(length(state.text)-replacement.end+replacement.start+length(work.title)>200){close();return;}
  replacement.range.deleteContents();const span=pill(work.title,work.id);replacement.range.insertNode(span);
  const after=doc.createTextNode('');span.after(after);const range=doc.createRange();range.setStart(after,0);range.collapse(true);caret(range);
  close();sync();editor.focus({preventScroll:true});
 }
 function highlight(){
  [...popup.children].forEach((button,index)=>button.setAttribute('aria-selected',String(index===active)));
  editor.setAttribute('aria-activedescendant',`reason-reference-option-${active}`);
 }
 async function search(){
  if(composing || readonly)return;const selection=win.getSelection();
  if(!selection?.rangeCount || !editor.contains(selection.anchorNode))return close();
  const selectionRange=selection.getRangeAt(0),prefix=doc.createRange();prefix.selectNodeContents(editor);prefix.setEnd(selectionRange.startContainer,selectionRange.startOffset);
  if(!selectionRange.collapsed)return close();
  const command=referenceQuery(prefix.toString());if(!command)return close();
  const {query:match,start,end}=command;
  const state=serialize();if(state.references.some(r=>start<r.end && end>r.start))return close();
  close();const epoch=generation,controller=new AbortController();request=controller;
  try{
   const response=await fetcher('/api/v1/catalog/search/?q='+encodeURIComponent(match)+'&limit=5',{credentials:'same-origin',cache:'no-store',signal:controller.signal});
   if(!response.ok)return;
   const payload=await response.json();if(epoch!==generation || doc.activeElement!==editor)return;
   options=payload.results.filter(work=>work.id!==exclude());if(!options.length)return;
   replacement={start,end,range:rangeAt(start,end)};popup.replaceChildren();active=0;
   options.forEach((work,index)=>{
    const button=doc.createElement('button');button.type='button';button.id=`reason-reference-option-${index}`;button.setAttribute('role','option');
    const title=doc.createElement('strong');title.textContent=work.title;appendEditionBadge(title,work);
    const author=doc.createElement('span');author.textContent=work.author || '作者未填写';button.append(title,author);
    button.addEventListener('pointerdown',event=>event.preventDefault());button.addEventListener('click',()=>choose(index));popup.append(button);
   });
   popup.hidden=false;editor.setAttribute('aria-expanded','true');highlight();position();
  }catch(error){if(error.name!=='AbortError')close();}
 }
 function schedule(){close();if(!composing && !readonly)timer=win.setTimeout(search,350);}
 editor.addEventListener('input',()=>{sync();schedule();});
 editor.addEventListener('compositionstart',()=>{composing=true;close();});
 editor.addEventListener('compositionend',()=>{composing=false;sync();schedule();});
 editor.addEventListener('paste',event=>{event.preventDefault();insertText(event.clipboardData.getData('text/plain'));schedule();});
 editor.addEventListener('drop',event=>event.preventDefault());
 editor.addEventListener('beforeinput',event=>{if(event.inputType==='insertParagraph' || event.inputType==='insertLineBreak'){event.preventDefault();insertText('\n');schedule();}});
 editor.addEventListener('keydown',event=>{
  if(composing || event.isComposing)return;
  if(event.key==='Escape'){close();return;}
  if(!popup.hidden && ['ArrowDown','ArrowUp','Enter'].includes(event.key)){
   event.preventDefault();if(event.key==='Enter')choose(active);else{active=(active+(event.key==='ArrowDown'?1:-1)+options.length)%options.length;highlight();}return;
  }
  if(['Tab','ArrowLeft','ArrowRight','Home','End'].includes(event.key))close();
 });
 editor.addEventListener('click',event=>{
  const target=event.target.closest('[data-reference-work]');if(target){close();const range=doc.createRange();range.selectNode(target);caret(range);}else close();
 });
 doc.addEventListener('pointerdown',event=>{if(!editor.contains(event.target) && !popup.contains(event.target))close();});
 win.addEventListener('resize',close);win.addEventListener('pagehide',close);
 let initial=[];try{initial=JSON.parse(doc.querySelector('#reason-reference-data')?.textContent||'[]');}catch{}
 setValue(textarea.value,initial);
 return {setValue,focus:()=>editor.focus({preventScroll:true}),value:()=>{const value=serialize();return normalizedReferenceValue(value.text,value.references);},setReadonly(value){readonly=value;editor.contentEditable=String(!value);editor.setAttribute('aria-readonly',String(value));if(value)close();},get composing(){return composing;}};
}
