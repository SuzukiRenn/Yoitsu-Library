import assert from 'node:assert/strict';
import {guidePosition, installFirstVisit} from '../static/first-visit.mjs';

for (const width of [320, 390, 700, 1280]) {
  const viewport = {width, height: 720};
  const panel = {width: Math.min(320, width - 24), height: 190};
  for (const rect of [
    {left: 12, width: 90, top: 60, bottom: 104},
    {left: width - 102, width: 90, top: 600, bottom: 644}
  ]) {
    const pos = guidePosition(rect, panel, viewport);
    assert.ok(pos.left >= 12 && pos.left + panel.width <= width - 12);
    assert.ok(pos.top >= 12 && pos.top + panel.height <= viewport.height - 12);
    assert.ok(pos.top >= rect.bottom || pos.top + panel.height <= rect.top);
  }
}
// Completed guides, forms, and blocked browser storage must never open a dialog.
const doc = {querySelector: () => ({}), createElement: () => assert.fail('Unexpected dialog')};
installFirstVisit(doc, {HTMLDialogElement: class {}, localStorage: {getItem: () => '1'}});
installFirstVisit({querySelector: () => null}, {});
installFirstVisit(doc, {HTMLDialogElement: class {}, get localStorage() {throw Error('blocked');}, get sessionStorage() {throw Error('blocked');}});
console.log('Guide checks passed: mobile/desktop bounds, target visibility, completion, forms and blocked storage');
