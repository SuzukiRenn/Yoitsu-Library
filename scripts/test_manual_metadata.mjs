import assert from 'node:assert/strict';
import {readMetadata} from '../static/manual-metadata.mjs';
const root=(author,description='',tags='')=>({querySelector:selector=>({value:{'#manual-author':author,'#manual-description':description,'#manual-tags':tags}[selector]})});
assert.deepEqual(readMetadata(root(' 作家 ',' 简介 ','奇幻，校园')), {author:'作家',description:'简介',tags:['奇幻','校园']});
assert.equal(Array.from(readMetadata(root('😀'.repeat(20),'😀'.repeat(2000))).description).length,2000);
assert.throws(()=>readMetadata(root('作'.repeat(21))),/20/);
assert.throws(()=>readMetadata(root('作家','字'.repeat(2001))),/2000/);
console.log('Manual author and description normalization and Unicode limits passed');
