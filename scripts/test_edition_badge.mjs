import assert from 'node:assert/strict';
import {appendEditionBadge} from '../static/edition-badge.mjs';
const children=[],title={append:node=>children.push(node)};
const doc={createElement:tag=>({tag,setAttribute(name,value){this[name]=value;}})};
for(const edition of ['book','other','unknown',undefined])appendEditionBadge(title,{edition},doc);
assert.equal(children.length,0);
appendEditionBadge(title,{edition:'web'},doc);
assert.equal(children.length,1);assert.equal(children[0].textContent,'Web');assert.equal(children[0]['aria-label'],'Web版');
console.log('Only explicit Web editions get the shared accessible title badge.');
