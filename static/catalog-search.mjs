import {normalize} from './text.mjs';

// This controller owns request order, not work identity or contribution writes.
export function createSearchController({fetcher, onResult, onStatus}) {
  let generation = 0, active = null, query = '', nextOffset = null, loading = false;
  let seen = new Set();
  function cancel() { generation++; active?.abort(); active = null; loading = false; }
  async function request(append) {
    const current = generation;
    active = new AbortController();
    loading = true;
    onStatus(append ? '正在加载更多…' : '搜索中…', true, {append});
    try {
      const params = new URLSearchParams({q: query});
      if (append) params.set('offset', String(nextOffset));
      const response = await fetcher(`/api/v1/catalog/search/?${params}`, {signal: active.signal});
      const payload = await response.json();
      if (current !== generation) return;
      if (!response.ok) { onStatus(payload.error?.message || '搜索失败', false, {append}); return; }
      const results = payload.results.filter(work => {
        if (seen.has(work.id)) return false;
        seen.add(work.id); return true;
      });
      nextOffset = payload.next_offset ?? null;
      onResult({...payload, results, loaded: seen.size}, {append});
      onStatus(`已展示 ${seen.size} 部／共匹配 ${payload.total ?? seen.size} 部`, false, {append});
    } catch (error) {
      if (current === generation && error.name !== 'AbortError') onStatus('网络错误，请重试', false, {append});
    } finally {
      if (current === generation) loading = false;
    }
  }
  async function run(raw) {
    cancel(); seen = new Set(); nextOffset = null;
    try { query = normalize(raw, 'title', true); }
    catch { onStatus('书名限 1—300 字', false); return; }
    return request(false);
  }
  async function loadMore() {
    if (loading || nextOffset === null) return;
    return request(true);
  }
  return {run, cancel, loadMore};
}
