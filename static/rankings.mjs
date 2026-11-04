const $=id=>document.getElementById(id);
const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const params=new URLSearchParams(location.search);
let data,year=Number(params.get('year'))||2026,category=params.get('category')||'bunko',limit=10;
limit=Math.max(10,Math.min(100,Math.trunc(Number(params.get('shown')))||10));
function setUrl(){const p=new URLSearchParams({year:String(year),category});if(limit>10)p.set('shown',String(limit));history.replaceState(null,'',location.pathname+'?'+p);}
function currentYear(){return data.years.find(y=>y.year===year);}
function currentSection(){const y=currentYear();return y.sections.find(s=>s.id===category)||y.sections[0];}
function cover(entry,featured=false){return entry.cover?`<img class="cover" src="${esc(entry.cover)}" alt="${esc(entry.title)}封面" loading="${featured?'eager':'lazy'}" width="66" height="96">`:'<span class="cover cover-fallback" aria-label="暂无封面">暂无<br>封面</span>';}
function featured(entry,index){return `<button type="button" class="featured" data-entry="${index}" aria-label="查看${esc(entry.title)}的上榜记录">${cover(entry,true)}<div class="featured-body"><div class="featured-rank">${String(entry.rank).padStart(2,'0')}<small>${entry.rankLabel.startsWith('并列')?'并列':''}</small></div><h3 title="${esc(entry.title)}">${esc(entry.title)}</h3><p class="jp-title" lang="ja" title="${esc(entry.japanese)}">${esc(entry.japanese)}</p></div></button>`;}
function row(entry,index){return `<li class="rank-row"><span class="rank-number">${String(entry.rank).padStart(2,'0')}${entry.rankLabel.startsWith('并列')?'<span class="tied">并列</span>':''}</span><button type="button" class="rank-work" data-entry="${index}" aria-label="查看${esc(entry.title)}的上榜记录">${cover(entry)}<span class="work-text"><span class="row-title" title="${esc(entry.title)}">${esc(entry.title)}</span><span class="jp-title" lang="ja" title="${esc(entry.japanese)}">${esc(entry.japanese)}</span></span></button><span class="rank-author" title="${esc(entry.author||'' )}">${esc(entry.author||'—')}</span></li>`;}
function renderYears(){const menu=$('year-grid');menu.innerHTML=[...data.years].reverse().map(y=>`<button type="button" data-year="${y.year}"${y.year===year?' aria-current="true"':''}>${y.year}</button>`).join('');$('selected-year').textContent=String(year);$('year-menu-toggle').setAttribute('aria-label','选择年份：'+year+' 年');$('previous-year').disabled=year===data.years[0].year;$('next-year').disabled=year===data.years.at(-1).year;}
function render(){
 const y=currentYear();if(!y)return;const section=currentSection();category=section.id;
 $('category-tabs').innerHTML=y.sections.map(s=>`<button type="button" role="tab" id="tab-${s.id}" data-category="${s.id}" aria-controls="rankings-panel" aria-selected="${s.id===category}">${esc(s.label==='单行本／小说部门'?'单行本／小说':s.label)}</button>`).join('');
 $('rankings-panel').setAttribute('aria-labelledby','tab-'+category);
 const entries=section.entries,visible=entries.slice(0,limit);
 $('result-count').textContent=`收录 ${entries.length} 部`;
 if(!entries.length){$('rankings-panel').innerHTML='<div class="empty-state">本年榜单暂无收录作品。</div>';}
 else {
  const top=visible.filter(e=>e.rank<=3),rest=visible.filter(e=>!top.includes(e));
  $('rankings-panel').innerHTML=(top.length?`<div class="podium">${top.map(e=>featured(e,section.entries.indexOf(e))).join('')}</div>`:'')+(rest.length?`<div class="rank-table-heading" aria-hidden="true"><span>排名</span><span>作品</span><span>作者</span></div><ol class="rank-list">${rest.map(e=>row(e,section.entries.indexOf(e))).join('')}</ol>`:'');
 }
 $('load-more').hidden=visible.length>=entries.length;$('load-more').textContent='加载更多';
 $('list-end').textContent=entries.length?`已展示 ${visible.length} / ${entries.length} 部`:'';
 renderYears();setUrl();
}
function setYearMenu(open,returnFocus=false){$('year-menu').hidden=!open;$('year-menu-toggle').setAttribute('aria-expanded',String(open));if(open)$('year-grid').querySelector('[aria-current]')?.focus();else if(returnFocus)$('year-menu-toggle').focus();}
function chooseYear(value){year=value;limit=10;const sections=currentYear().sections;if(!sections.some(s=>s.id===category))category=sections[0].id;setYearMenu(false);render();}
$('year-grid').addEventListener('click',event=>{const b=event.target.closest('[data-year]');if(b){chooseYear(Number(b.dataset.year));$('year-menu-toggle').focus();}});
$('previous-year').addEventListener('click',()=>chooseYear(year-1));$('next-year').addEventListener('click',()=>chooseYear(year+1));
$('year-menu-toggle').addEventListener('click',()=>setYearMenu($('year-menu').hidden));
$('year-grid').addEventListener('keydown',event=>{if(!['ArrowLeft','ArrowRight','ArrowUp','ArrowDown','Home','End'].includes(event.key))return;event.preventDefault();const buttons=[...$('year-grid').querySelectorAll('button')];const current=buttons.indexOf(document.activeElement);const steps={ArrowLeft:-1,ArrowRight:1,ArrowUp:-5,ArrowDown:5};const next=event.key==='Home'?0:event.key==='End'?buttons.length-1:Math.max(0,Math.min(buttons.length-1,current+steps[event.key]));buttons[next].focus();});
document.addEventListener('pointerdown',event=>{if(!event.target.closest('.year-picker'))setYearMenu(false);});
document.addEventListener('focusin',event=>{if(!event.target.closest('.year-picker'))setYearMenu(false);});
$('category-tabs').addEventListener('click',event=>{const b=event.target.closest('[data-category]');if(!b)return;category=b.dataset.category;limit=10;render();document.getElementById('tab-'+category)?.focus();});
$('category-tabs').addEventListener('keydown',event=>{if(!['ArrowLeft','ArrowRight','Home','End'].includes(event.key))return;event.preventDefault();const tabs=currentYear().sections;let index=tabs.findIndex(s=>s.id===category);index=event.key==='Home'?0:event.key==='End'?tabs.length-1:(index+(event.key==='ArrowRight'?1:tabs.length-1))%tabs.length;category=tabs[index].id;limit=10;render();$('tab-'+category).focus();});
$('load-more').addEventListener('click',()=>{const first=limit;limit+=10;render();$('rankings-panel').querySelector(`[data-entry="${first}"]`)?.focus({preventScroll:true});});
$('rankings-panel').addEventListener('click',event=>{
 const b=event.target.closest('[data-entry]');if(!b)return;const entry=currentSection().entries[Number(b.dataset.entry)];
 const historyRows=[];for(const y of [...data.years].reverse())for(const s of y.sections)for(const e of s.entries)if(e.japanese===entry.japanese)historyRows.push({year:y.year,section:s.label,rank:e.rankLabel});
 $('work-dialog-content').innerHTML=`<div class="dialog-book">${cover(entry,true)}<div><h2 id="work-title">${entry.workUrl?`<a class="ranked-detail-link" href="${esc(entry.workUrl+'?'+new URLSearchParams({from:location.pathname+location.search}))}">${esc(entry.title)}</a>`:esc(entry.title)}</h2><p class="jp-title" lang="ja">${esc(entry.japanese)}</p>${entry.author?`<p class="dialog-author">${esc(entry.author)}</p>`:''}</div></div><h3 class="history-heading">历年上榜记录</h3><ul class="rank-history">${historyRows.map(r=>`<li><span>${r.year} 年</span><span>${esc(r.section)}</span><strong>${esc(r.rank)} 名</strong></li>`).join('')}</ul>`;
 $('work-dialog').showModal();
});
$('close-dialog').addEventListener('click',()=>$('work-dialog').close());
$('work-dialog').addEventListener('click',event=>{if(event.target!==$('work-dialog'))return;const box=$('work-dialog').getBoundingClientRect();if(event.clientX<box.left||event.clientX>box.right||event.clientY<box.top||event.clientY>box.bottom)$('work-dialog').close();});
document.addEventListener('keydown',event=>{if(event.key==='Escape'&&!$('year-menu').hidden){event.preventDefault();setYearMenu(false,true);}});
try{const response=await fetch('/api/v1/rankings/');if(!response.ok)throw new Error('data');data=await response.json();if(!data.years.some(y=>y.year===year))year=data.years.at(-1).year;render();document.querySelector('.rankings-page').removeAttribute('data-navigation-loading');document.dispatchEvent(new CustomEvent('booklist:navigation-ready'));}catch{$('rankings-panel').innerHTML='<p class="empty-state">榜单暂时无法读取，请刷新重试。</p>';}
