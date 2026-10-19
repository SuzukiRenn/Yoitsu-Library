import {exportJob} from './core.mjs';
const $ = id => document.getElementById(id);
let job = null;
async function call(type, extra = {}) {
  const result = await chrome.runtime.sendMessage({type, ...extra});
  if (!result?.ok) throw new Error(result?.error || '扩展暂时无法响应');
  job = result.job;
  render();
}
function render() {
  $('status').textContent = {running: '正在采集', paused: '已暂停', completed: '本轮完成'}[job?.status] || '等待开始';
  $('counts').textContent = `${job?.pages.length || 0} 页 · ${job?.ids.length || 0} 部作品`;
  $('reason').textContent = job?.reason || (job?.status === 'running' ? '请保留任务标签页，避免手动翻页。' : '');
  $('start').disabled = job?.status === 'running';
  $('pause').disabled = job?.status !== 'running';
  $('resume').disabled = job?.status !== 'paused';
  $('export').disabled = !job?.ids.length;
}
for (const action of ['start', 'pause', 'resume', 'export']) {
  $(action).addEventListener('click', async () => {
    $('error').textContent = '';
    try {
      if (action === 'export') {
        await call('status');
        const url = URL.createObjectURL(new Blob([JSON.stringify(exportJob(job), null, 2) + '\n'], {type: 'application/json'}));
        const anchor = document.createElement('a');
        anchor.href = url; anchor.download = `wenku8-directory-${Date.now()}.json`;
        anchor.click(); setTimeout(() => URL.revokeObjectURL(url), 10000);
        return;
      }
      const [tab] = await chrome.tabs.query({active: true, currentWindow: true});
      if (!tab?.id) throw new Error('请先打开文库列表标签页');
      if (action === 'start' && job?.pages.length && !confirm('开始新一轮会替换现有目录记录。请先导出 JSON。确定继续？')) return;
      await call(action, {tabId: tab.id, maxPages: Number($('pages').value), interval: Number($('interval').value), replace: action === 'start'});
    } catch (error) { $('error').textContent = error.message; }
  });
}
chrome.storage.onChanged.addListener((changes, area) => {
  if (area === 'local' && changes.job) { job = changes.job.newValue; render(); }
});
call('status').catch(() => { $('error').textContent = '读取状态失败，请重新打开扩展'; });
