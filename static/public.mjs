import {installAppearance} from './appearance.mjs?v=20261024-appearance-2';
installAppearance();
import {installShelfSort} from './shelf-sort.mjs?v=20261024-tag-mode-1';
import {startReasonLikes} from './reason-likes.mjs?v=20261024-identity-sync';
import {startLiveInteractions} from './live-interactions.mjs?v=20261024-identity-sync';
import {createShelfRefresh} from './live-shelf.mjs?v=20261024-motion-perf-1';
import './navigation-motion.mjs?v=20261020-1';
import './work-motion.mjs?v=20261021-5';
import {installShelfReturn} from './shelf-return.mjs?v=20261024-references-1';
installShelfReturn();
import {installShelfView} from './shelf-view.mjs?v=20261024-motion-perf-1';
installShelfView();
import {installWorkSequence} from './work-sequence.mjs?v=20261024-sequence-1';
installWorkSequence();
import './back-to-top.mjs?v=20261024-appearance-1';
import {installSpoilerReveal} from './spoiler-reveal.mjs?v=20261024-inline-spoiler-4';
installSpoilerReveal();
const status = document.querySelector('#action-status');
const shelfRefresh=createShelfRefresh();
startLiveInteractions({refresh:shelfRefresh});
installShelfSort(shelfRefresh);

document.addEventListener('keydown', event => {
  if (event.key !== 'Escape') return;
  for (const control of document.querySelectorAll('.recommend-control')) {
    control.setAttribute('data-tooltip-dismissed', '');
  }
});
for (const name of ['pointerout', 'focusout']) document.addEventListener(name, event => {
  const control = event.target.closest('.recommend-control');
  if (control && !control.contains(event.relatedTarget)) control.removeAttribute('data-tooltip-dismissed');
});

startReasonLikes();
