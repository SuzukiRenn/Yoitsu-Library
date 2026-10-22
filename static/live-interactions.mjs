import {renderRecommenders} from './account.mjs?v=20261022-identity-sync';
import {renderReasonPreview} from './reason-ui.mjs?v=20261022-identity-sync';
import {createPublicApi} from './public-api.mjs?v=20261022-identity-sync';

export function updateInteraction(container, work) {
  renderRecommenders(container, work);
  const available = work.available !== false;
  const writable = available && work.writable !== false;
  container.dataset.readonly = String(!writable);
  const stats = container.querySelectorAll('.work-stats strong, [data-action-count]');
  if (available) {
    [work.recommendation_count, work.like_count].forEach((value, index) => {
      const displayed=stats[index]?.hasAttribute('data-action-count') && value>99 ? '99+' : String(value);
      if (stats[index] && stats[index].textContent !== displayed) {
        stats[index].textContent = displayed;
        stats[index].classList.remove('count-updated');
        // Restart the short feedback only when the server count actually changes.
        void stats[index].offsetWidth;
        stats[index].classList.add('count-updated');
      }
    });
  }
  const activity = container.dataset.activity;
  const emptyActivity = activity && work.can_join === false;
  const emptyPublic = !activity && work.in_shelf === false;
  for (const button of container.querySelectorAll('[data-action]')) {
    const recommended = button.dataset.action !== 'like';
    const enabled = Boolean(recommended ? work.recommended : work.liked);
    if (available) {
      button.dataset.enabled = String(!enabled);
      button.setAttribute('aria-pressed', String(enabled));
      const label = button.querySelector('[data-action-label]');
      const text = recommended ? label ? enabled ? '已推荐' : '推荐' : enabled ? '取消推荐' : '+1' : enabled ? '已赞' : '点赞';
      if(label)button.setAttribute('aria-label',`${recommended ? enabled ? '取消推荐' : '推荐这本书' : enabled ? '取消点赞' : '点赞'}，${recommended ? '推荐' : '点赞'}总数 ${recommended ? work.recommendation_count : work.like_count}`);
      if (label) label.textContent = text;
      else button.textContent = text;
    }
    button.disabled = !writable || Boolean(emptyActivity) || emptyPublic;
    if (recommended) {
      const control = button.closest('.recommend-control');
      let tooltip = control?.querySelector('.control-tooltip');
      if (!tooltip && control) {
        tooltip = document.createElement('span'); tooltip.className = 'control-tooltip';
        tooltip.setAttribute('role', 'tooltip'); tooltip.textContent = '我也推荐这本书';
        tooltip.id = `recommend-tip-${activity || 'public'}-${container.dataset.work}`;
        control.append(tooltip);
      }
      if (tooltip) {
        tooltip.hidden = enabled || !writable || Boolean(emptyActivity) || emptyPublic;
        if (tooltip.hidden) button.removeAttribute('aria-describedby');
        else button.setAttribute('aria-describedby', tooltip.id);
      }
    }
  }
  let note = container.querySelector('[data-live-note]');
  const message = emptyPublic ? (work.resubmission_required ? '已退出书单，重新推荐需填写作品资料' : '已退出公共书单') : !available ? '作品暂不可用' : !writable ? '活动已暂停或结束' : emptyActivity ? '已退出本活动书单' : '';
  if (message && !note) {
    note = document.createElement('p'); note.dataset.liveNote = ''; note.className = 'live-note';
    container.append(note);
  }
  if (note) {note.textContent = message; note.hidden = !message;}
  const actions = container.querySelector('.action-buttons');
  let edit = actions?.querySelector('a.reason-link');
  const showEdit = writable && !emptyPublic && (work.recommended || Boolean(emptyActivity));
  if (!edit && showEdit && actions) {
    edit = document.createElement('a'); edit.className = 'reason-link'; actions.append(edit);
  }
  if (edit) {
    edit.hidden = !showEdit;
    edit.textContent = emptyActivity ? '重新推荐' : work.own_reason ? '编辑理由' : '写理由';
    edit.href = `${activity ? `/activities/${activity}/recommend/` : '/recommend/'}?work=${container.dataset.work}&edit=1`;
  }
  const preview = container.closest('.public-card')?.querySelector('.reason-preview');
  if (preview && available) {
    renderReasonPreview(preview, work.top_reason, container.dataset.work, activity);
    let link = preview.querySelector('a');
    if (!work.reason_count) link?.remove();
    else {
      if (!link) {
        link = document.createElement('a'); link.className = 'reason-link';
        link.href = `/works/${container.dataset.work}/${activity ? `?activity=${activity}` : ''}#reasons`;
        preview.append(link);
      }
      link.textContent = `查看理由（${work.reason_count}）`;
    }
  }
}

export function startLiveInteractions({doc = document, write = createPublicApi(), fetcher = fetch, refresh,
  render = updateInteraction, schedule = setTimeout, cancel = clearTimeout} = {}) {
  const containers = () => [...doc.querySelectorAll('.public-actions[data-work]')];
  if (!containers().length && !refresh) return;
  const status = doc.querySelector('#action-status');
  const busy = new Set();
  let generation = 0, timer, request, statusTimer, polling = false, stopped = false, navigating = false, delay = 5000;
  function plan() {
    cancel(timer);
    if (!stopped && !doc.hidden && !navigating) timer = schedule(poll, delay);
  }
  async function poll() {
    if (stopped || doc.hidden || polling || navigating) return;
    if (busy.size) {plan(); return;}
    polling = true;
    const epoch = generation;
    request = new AbortController();
    try {
      const groups = new Map();
      for (const container of containers()) {
        const prefix = container.dataset.activity ? `/api/v1/activities/${container.dataset.activity}` : '/api/v1/public';
        if (!groups.has(prefix)) groups.set(prefix, []);
        groups.get(prefix).push(container);
      }
      for (const [prefix, cards] of groups) {
        const ids = [...new Set(cards.map(card => card.dataset.work))].join(',');
        const response = await fetcher(`${prefix}/stats/?ids=${ids}`, {credentials: 'same-origin', cache: 'no-store', signal: request.signal});
        if (!response.ok) {
          delay = response.status === 429 ? 60000 : Math.min(delay * 2, 60000);
          return;
        }
        const payload = await response.json();
        if (epoch !== generation || doc.hidden || stopped) return;
        for (const work of payload.results) for (const card of cards) {
          if (Number(card.dataset.work) === work.id && !busy.has(card)) render(card, work);
        }
      }
      if (refresh && epoch === generation && !doc.hidden && !stopped) {
        const response = await refresh({signal: request.signal,
          isCurrent: () => epoch === generation && !doc.hidden && !stopped && !busy.size});
        if (response?.ok === false) {delay = response.status === 429 ? 60000 : Math.min(delay * 2, 60000); return;}
      }
      delay = 5000;
    } catch (error) {
      if (error.name !== 'AbortError') delay = Math.min(delay * 2, 60000);
    } finally {polling = false; plan();}
  }
  doc.addEventListener('click', async event => {
    const button = event.target.closest('[data-action]');
    const container = button?.closest('.public-actions[data-work]');
    if (!container || button.disabled || busy.has(container)) return;
    busy.add(container); generation++; request?.abort();
    const buttons = [...container.querySelectorAll('[data-action]')];
    buttons.forEach(item => {item.disabled = true;});
    container.setAttribute('aria-busy', 'true'); status.textContent = '';
    doc.defaultView?.clearTimeout(statusTimer);
    const prefix = container.dataset.activity ? `/api/v1/activities/${container.dataset.activity}` : '/api/v1/public';
    const payload = button.dataset.action === 'withdraw' ? {} : {enabled: button.dataset.enabled === 'true'};
    try {
      const result = await write(`${prefix}/works/${container.dataset.work}/${button.dataset.action}/`, payload);
      render(container, result.work);
      status.textContent = '已更新';
      statusTimer = doc.defaultView?.setTimeout(() => {status.textContent = '';}, 1800);
    } catch (error) {
      status.textContent = error.message;
      buttons.forEach(item => {item.disabled = container.dataset.readonly === 'true';});
    } finally {
      busy.delete(container); generation++; container.removeAttribute('aria-busy'); plan();
    }
  });
  doc.addEventListener('booklist:shelf-navigation', event => {
    navigating=event.detail.pending; generation++; request?.abort(); cancel(timer);
    if(!navigating)plan();
  });
  doc.addEventListener('booklist:reason-updated', () => {generation++; request?.abort(); plan();});
  doc.addEventListener('visibilitychange', () => {
    cancel(timer); request?.abort(); generation++;
    if (!doc.hidden) { delay = 5000; poll(); }
  });
  doc.defaultView?.addEventListener('pagehide', () => {stopped = true; cancel(timer); request?.abort();});
  doc.defaultView?.addEventListener('pageshow', () => {if (stopped) {stopped = false; plan();}});
  plan();
  return {poll};
}
