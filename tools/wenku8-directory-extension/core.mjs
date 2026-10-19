export const ALARM = 'wenku8-directory-step';
export const MAX_PAGES = 20;

export function listing(raw) {
  const u = new URL(raw);
  if (u.protocol !== 'https:' || !['wenku8.net', 'www.wenku8.net'].includes(u.hostname) ||
      u.port || u.username || u.password || u.hash || u.pathname !== '/modules/article/articlelist.php') {
    throw new Error('请打开 HTTPS 轻小说列表页');
  }
  // The site's own “全部” links include initial= or class= with an empty value.
  // Validate duplicates before normalizing these equivalent unfiltered URLs.
  const patterns = {page: /^[1-9][0-9]{0,3}$/, fullflag: /^[01]$/,
    initial: /^[A-Z1]?$/, class: /^(?:[1-9]|1[0-4])?$/};
  for (const [key, value] of u.searchParams) {
    if (!Object.hasOwn(patterns, key) || u.searchParams.getAll(key).length !== 1 || !patterns[key].test(value)) {
      throw new Error('暂不支持这个目录筛选条件');
    }
  }
  for (const key of ['initial', 'class']) {
    if (u.searchParams.get(key) === '') u.searchParams.delete(key);
  }
  const page = Number(u.searchParams.get('page') || 1);
  u.searchParams.set('page', String(page));
  u.searchParams.sort();
  const filters = new URLSearchParams(u.searchParams);
  filters.delete('page');
  return {url: u.href, origin: u.origin, page, filter: filters.toString()};
}

// Chrome serializes this function into the selected tab. Keep it self-contained.
// Read hrefs and a public title marker; never read cookies, forms or account text.
export function readLinks() {
  return {url: location.href, is_listing: document.title.includes('轻小说列表'), links: Array.from(document.querySelectorAll('a[href]'),
    a => a.getAttribute('href')).filter(href => typeof href === 'string' && href.length <= 2048).slice(0, 3000)};
}

// Runs in Chrome's isolated content-script world; returns without holding the
// service worker open. Only wakes the controller, which rechecks task and time.
export function scheduleTick(delay, token) {
  clearTimeout(globalThis.__sharedBooklistDirectoryTimer);
  globalThis.__sharedBooklistDirectoryTimer = setTimeout(() => {
    chrome.runtime.sendMessage({type: 'directory-timer', token}).catch(() => {});
  }, Math.max(0, delay));
}

export function parsePage(raw, expected) {
  if (!raw?.is_listing || !Array.isArray(raw.links) || raw.links.length > 3000) throw new Error('目录页面标识异常，已停止');
  const current = listing(raw.url);
  if (current.url !== listing(expected).url) throw new Error('页面已跳转，已停止；请检查登录或验证提示');
  const ids = new Set();
  const next = new Set();
  for (const href of raw.links) {
    let u;
    try { u = new URL(href, current.url); } catch { continue; }
    if (u.protocol !== 'https:' || u.username || u.password || u.port || u.hash) continue;
    if (['wenku8.net', 'www.wenku8.net'].includes(u.hostname) && !u.search) {
      const match = /^\/book\/([1-9][0-9]{0,7})\.htm$/.exec(u.pathname);
      if (match) ids.add(Number(match[1]));
    }
    try {
      const candidate = listing(u.href);
      if (candidate.origin === current.origin && candidate.filter === current.filter &&
          candidate.page === current.page + 1) next.add(candidate.url);
    } catch { /* unrelated page link */ }
  }
  if (!ids.size || ids.size > 100) throw new Error('目录内容异常或没有作品链接，已停止');
  if (next.size > 1) throw new Error('下一页链接不明确，已停止');
  return {url: current.url, book_ids: [...ids], next_url: [...next][0] || null};
}

export function exportJob(job) {
  if (!job) throw new Error('还没有目录记录');
  return {schema_version: 1, source: 'wenku8-browser-directory', exported_at: new Date().toISOString(),
    status: job.status, reason: job.reason, pages: job.pages, book_ids: job.ids,
    next_url: job.nextUrl || null, complete_catalog: false,
    scope: 'User-opened directory links only; metadata not yet fetched or imported'};
}
