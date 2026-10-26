import assert from 'node:assert/strict';
import {createShelfRefresh} from '../static/live-shelf.mjs';

// Fast metadata/control smoke test. Actual anchor disconnect/focus is tested in
// scripts/fixtures/live-shelf-focus.html using a native browser DOM.
for (const scope of ['public', 'activity']) {
  const field=html=>({innerHTML:html,get childNodes(){return [{html:this.innerHTML}];},replaceChildren(...nodes){this.innerHTML=nodes.map(n=>n.html).join('');}});
  const tags=field('<a>原标签</a>'), title=field('作品'), author=field('作者');
  const button={isConnected:true,closest:()=>null};
  const fields={'.book-info .book-title':title,'.book-info .work-author':author,'.book-info .tags':tags};
  const current={dataset:{shelfWork:'1'},querySelector:s=>fields[s]};
  const grid={querySelectorAll:()=>[current],querySelector:()=>current};
  const shelf={querySelector:s=>s==='[data-shelf-grid]'?grid:null,querySelectorAll:()=>[]};
  const doc={activeElement:button,querySelector:()=>shelf,importNode:n=>({...n})};
  const win={scrollY:0,addEventListener(){},matchMedia:()=>({matches:false,addEventListener(){}})};
  const refresh=createShelfRefresh({doc,win});
  for(const html of ['<a>原标签</a><a>新标签</a>','<a>原标签</a>','']){
    const incoming={dataset:{shelfWork:'1'},querySelector:s=>s==='.book-info .tags'?field(html):fields[s]};
    const next={querySelector:s=>s==='[data-shelf-grid]'?{querySelectorAll:()=>[incoming]}:null,
      querySelectorAll:s=>s==='[data-shelf-grid] > [data-shelf-work]'?[incoming]:[]};
    refresh.apply(next);
    assert.equal(tags.innerHTML,html,scope+': retained card tags follow the latest server response');
    assert.equal(doc.activeElement,button,scope+': focused interaction remains attached');
    assert.equal(grid.querySelector(),current,scope+': retained card is not replaced');
  }
}
console.log('Retained card metadata and external control smoke test passed; run the native DOM fixture for tag-anchor focus.');
