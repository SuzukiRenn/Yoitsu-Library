import {layout,render,FONT,zipParts} from './activity-summary-layout.mjs?v=20261022-charcoal-1';
const root=document.querySelector('[data-summary-export]');
if (root) {
  const find=name=>root.querySelector(`[data-${name}]`),generate=find('generate'),save=find('save'),all=find('save-all'),status=find('summary-status'),image=find('summary-image');
  const canvas=document.createElement('canvas'),ctx=canvas.getContext('2d');
  let snapshot=null,plan=null,index=0,previewURL=null,busy=false,images=new Map();
  const png=()=>new Promise((resolve,reject)=>canvas.toBlob(blob=>blob ? resolve(blob) : reject(new Error('无法生成图片，请重试')),'image/png'));
  const filename=n=>`${snapshot.activity.title.replace(/[\\/:*?"<>|\x00-\x1f]/g,'_')}-推荐总结-${String(n+1).padStart(2,'0')}.png`;
  const download=(blob,name)=>{const url=URL.createObjectURL(blob),link=document.createElement('a');link.href=url;link.download=name;document.body.append(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),60000);};
  async function show() {
    render(canvas,plan,index,images);const blob=await png();
    if (previewURL) URL.revokeObjectURL(previewURL);
    previewURL=URL.createObjectURL(blob);image.src=previewURL;image.hidden=false;
    find('page-label').textContent=`第 ${index+1} / ${plan.pages.length} 张`;
    find('previous').disabled=index===0;find('next').disabled=index===plan.pages.length-1;
  }
  const lock=value=>{busy=value;generate.disabled=value;save.disabled=value;all.disabled=value;find('previous').disabled=value||index===0;find('next').disabled=value||!plan||index===plan.pages.length-1;};
  generate.addEventListener('click',async()=>{
    if (busy) return;lock(true);status.textContent='正在整理活动快照…';
    try {
      const response=await fetch(root.dataset.source,{credentials:'same-origin',cache:'no-store'});
      const result=await response.json();if (!response.ok) throw new Error(result.error?.message||'无法读取活动快照');
      await document.fonts.ready;
      const nextPlan=layout(result,(text,size,bold)=>{ctx.font=`${bold ? 650 : 400} ${size}px ${FONT}`;return ctx.measureText(text).width;});
      status.textContent='正在加载作品封面…';
      const loaded=new Map(),urls=[...new Set(result.works.map(work=>work.cover_url).filter(Boolean))];
      let cursor=0;
      await Promise.all(Array.from({length:Math.min(4,urls.length)},async()=>{
        while(cursor<urls.length) {
          const url=urls[cursor++];
          // Only trusted same-origin cache paths can enter the export canvas.
          if(!/^\/media\/covers\/[0-9a-f]{64}\.(jpg|png)$/.test(url)) continue;
          const img=new Image();
          await new Promise(resolve=>{const timer=setTimeout(()=>{img.src='';resolve();},10000);img.onload=()=>{clearTimeout(timer);loaded.set(url,img);resolve();};img.onerror=()=>{clearTimeout(timer);resolve();};img.src=url;});
        }
      }));
      snapshot=result;plan=nextPlan;images=loaded;index=0;await show();
      save.hidden=false;all.hidden=plan.pages.length===1;root.querySelector('.summary-paging').hidden=false;find('summary-note').hidden=false;
      generate.textContent='重新生成快照';status.textContent=`已生成 ${plan.pages.length} 张图片，包含 ${snapshot.total} 部作品。`;
    } catch(error) {status.textContent=error.message||'生成失败，请重试';} finally {lock(false);}
  });
  for (const [name,step] of [['previous',-1],['next',1]]) find(name).addEventListener('click',async()=>{
    if (busy||!plan||index+step<0||index+step>=plan.pages.length) return;
    lock(true);try {index+=step;await show();} catch(error) {status.textContent=error.message;} finally {lock(false);}
  });
  save.addEventListener('click',async()=>{if (busy||!plan) return;lock(true);try {download(await png(),filename(index));} catch(error) {status.textContent=error.message;} finally {lock(false);}});
  all.addEventListener('click',async()=>{
    if (busy||!plan) return;lock(true);
    try {
      const files=[];
      for (let n=0;n<plan.pages.length;n++) {
        status.textContent=`正在打包 ${n+1} / ${plan.pages.length}…`;render(canvas,plan,n,images);
        files.push({name:filename(n),bytes:new Uint8Array(await (await png()).arrayBuffer())});
        await new Promise(resolve=>requestAnimationFrame(resolve));
      }
      download(new Blob(zipParts(files),{type:'application/zip'}),filename(0).replace(/-01\.png$/,'.zip'));
      status.textContent='全部图片已打包下载。';
    } catch(error) {status.textContent=error.message;} finally {render(canvas,plan,index,images);lock(false);}
  });
  addEventListener('pagehide',()=>{if (previewURL) URL.revokeObjectURL(previewURL);});
}
