import {ALARM} from './core.mjs';
import {createController} from './controller.mjs';

const controller = createController(chrome);
let queue = Promise.resolve();
const serial = action => {
  const result = queue.then(action);
  queue = result.catch(() => {});
  return result;
};
chrome.runtime.onMessage.addListener((message, sender, respond) => {
  if (sender.id !== chrome.runtime.id || sender.tab) return false;
  serial(async () => {
    switch (message.type) {
      case 'status': return controller.get();
      case 'start': return controller.start(message.tabId, message.maxPages, message.interval, message.replace === true);
      case 'pause': return controller.pause();
      case 'resume': return controller.resume(message.tabId);
      default: throw new Error('未知操作');
    }
  }).then(job => respond({ok: true, job}), error => respond({ok: false, error: error.message}));
  return true;
});
chrome.alarms.onAlarm.addListener(alarm => {
  if (alarm.name === ALARM) void serial(() => controller.tick()).catch(() => {});
});
chrome.tabs.onUpdated.addListener((id, info) => {
  if (info.status === 'complete') void serial(() => controller.loaded(id)).catch(() => {});
});
chrome.tabs.onRemoved.addListener(id => { void serial(() => controller.removed(id)).catch(() => {}); });
chrome.runtime.onStartup.addListener(() => { void serial(() => controller.startup()).catch(() => {}); });
chrome.runtime.onInstalled.addListener(() => { void serial(() => controller.startup()).catch(() => {}); });
