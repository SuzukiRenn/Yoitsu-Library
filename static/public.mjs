import {installShelfSort} from './shelf-sort.mjs?v=20261023-clear-search-1';
import {startReasonLikes} from './reason-likes.mjs?v=20261022-reason-gate';
import {startLiveInteractions} from './live-interactions.mjs?v=20261022-reason-gate';
import {createShelfRefresh} from './live-shelf.mjs?v=20261023-tag-links-1';
import './navigation-motion.mjs?v=20261020-1';
import './work-motion.mjs?v=20261021-5';
import './back-to-top.mjs?v=20261023-header-tools-1';
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
