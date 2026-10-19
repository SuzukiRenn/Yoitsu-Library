import {ALARM, MAX_PAGES, listing, parsePage, readLinks, scheduleTick} from './core.mjs';

export function createController(api, now = () => Date.now()) {
  const get = async () => (await api.storage.local.get('job')).job || null;
  const save = job => api.storage.local.set({job});
  async function halt(job, reason, status = 'paused') {
    await api.alarms.clear(ALARM);
    if (job) { job.status = status; job.reason = reason; await save(job); }
    return job;
  }
  async function arm(job) {
    job.timerToken = crypto.randomUUID();
    await save(job);
    await api.alarms.create(ALARM, {when: Math.max(now() + 30000, job.dueAt || 0)});
    if (job.phase === 'waiting') {
      await api.scripting.executeScript({target: {tabId: job.tabId}, func: scheduleTick,
        args: [Math.max(0, job.dueAt - now()), job.timerToken]});
    }
  }
  async function collect(job) {
    const tab = await api.tabs.get(job.tabId);
    if (tab.status !== 'complete') throw new Error('目录尚未加载完成，请加载完成后再继续');
    if (listing(tab.url).url !== job.expectedUrl) throw new Error('所选页面已改变，已暂停');
    const results = await api.scripting.executeScript({target: {tabId: job.tabId}, func: readLinks});
    const page = parsePage(results[0]?.result, job.expectedUrl);
    if (job.pages.some(row => row.url === page.url)) throw new Error('遇到重复分页，已暂停');
    job.ids = [...new Set([...job.ids, ...page.book_ids])];
    job.pages.push({url: page.url, book_ids: page.book_ids, captured_at: new Date(now()).toISOString()});
    job.currentUrl = page.url;
    job.nextUrl = page.next_url;
    job.phase = 'waiting';
    job.dueAt = now() + job.interval * 1000;
    if (!job.nextUrl) return halt(job, '本目录已到末页；不代表全站目录完整', 'completed');
    if (job.pages.length >= job.maxPages) return halt(job, '已达到本轮页数上限', 'completed');
    await arm(job);
    return job;
  }
  async function protect(job, action) {
    try { return await action(); }
    catch (error) {
      // Do not store browser exception strings (which can contain unrelated URLs).
      const reason = error instanceof Error && /^(请打开|暂不支持|页面已跳转|目录|下一页|所选页面|遇到重复)/.test(error.message)
        ? error.message : '无法读取目录或页面权限已失效，请回到原目录后继续';
      return halt(job, reason);
    }
  }
  return {
    get,
    async start(tabId, maxPages, interval, replace = false) {
      if (!Number.isInteger(maxPages) || maxPages < 1 || maxPages > MAX_PAGES ||
          !Number.isInteger(interval) || interval < 5 || interval > 300) throw new Error('页数应为 1–20，间隔应为 5–300 秒');
      const old = await get();
      if (old?.status === 'running') throw new Error('请先暂停正在运行的任务');
      if (old?.pages.length && !replace) throw new Error('请先导出记录，再确认开始新一轮');
      const tab = await api.tabs.get(tabId);
      const current = listing(tab.url);
      const job = {tabId, maxPages, interval, status: 'running', phase: 'loading', reason: '',
        expectedUrl: current.url, currentUrl: current.url, nextUrl: null, pages: [], ids: [], dueAt: 0};
      await api.alarms.clear(ALARM);
      await save(job);
      return protect(job, () => collect(job));
    },
    async pause() { return halt(await get(), '已手动暂停'); },
    async resume(tabId) {
      const job = await get();
      if (!job || job.status !== 'paused' || job.tabId !== tabId) throw new Error('请在原任务标签页继续');
      const tab = await api.tabs.get(tabId);
      if (listing(tab.url).url !== job.expectedUrl) throw new Error('请回到任务当前目录页再继续');
      job.status = 'running'; job.reason = '';
      return protect(job, async () => {
        if (job.phase === 'loading') return collect(job);
        job.dueAt = now() + job.interval * 1000;
        await arm(job); return job;
      });
    },
    async tick(tabId = null, token = null) {
      const job = await get();
      if (!job || job.status !== 'running') return job;
      // Ignore timers from prior rounds, paused/resumed tasks, subframes or other tabs.
      if (tabId !== null && (tabId !== job.tabId || token !== job.timerToken || job.phase !== 'waiting')) return job;
      return protect(job, async () => {
        if (job.phase === 'loading') {
          if (job.loadDeadline && now() < job.loadDeadline) {await arm(job); return job;}
          return halt(job, '页面加载未完成，已暂停；可加载完成后继续');
        }
        if (now() < job.dueAt) { await arm(job); return job; }
        const tab = await api.tabs.get(job.tabId);
        if (listing(tab.url).url !== job.currentUrl) throw new Error('所选页面已改变，已暂停');
        const next = listing(job.nextUrl);
        if (next.origin !== listing(job.currentUrl).origin || next.page !== listing(job.currentUrl).page + 1 ||
            next.filter !== listing(job.currentUrl).filter) throw new Error('下一页链接异常');
        job.phase = 'loading'; job.expectedUrl = next.url; job.loadDeadline = now() + 30000;
        await arm(job); // persist before navigation; a watchdog stops stalled navigation
        await api.tabs.update(job.tabId, {url: next.url});
        return job;
      });
    },
    async loaded(tabId) {
      const job = await get();
      if (!job || job.status !== 'running' || job.tabId !== tabId) return job;
      if (job.phase !== 'loading') return halt(job, '任务页面被刷新或更改，已暂停');
      return protect(job, () => collect(job));
    },
    async removed(tabId) {
      const job = await get();
      if (job?.tabId === tabId && job.status === 'running') return halt(job, '任务标签页已关闭');
    },
    async startup() {
      const job = await get();
      if (job?.status === 'running') return halt(job, '浏览器重启或扩展更新后已暂停，请检查页面');
    }
  };
}
