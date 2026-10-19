import {normalize} from './text.mjs';

// This controller owns request order, not work identity or contribution writes.
export function createSearchController({fetcher, onResult, onStatus}) {
  let generation = 0, active = null;
  function cancel() { generation++; active?.abort(); active = null; }
  async function run(raw) {
    cancel();
    const current = generation;
    let query;
    try { query = normalize(raw, 'title', true); }
    catch { onStatus('书名限 1—300 字', false); return; }
    active = new AbortController();
    onStatus('搜索中…', true);
    try {
      const response = await fetcher(`/api/v1/catalog/search/?${new URLSearchParams({q: query})}`, {signal: active.signal});
      const payload = await response.json();
      if (current !== generation) return;
      if (!response.ok) { onStatus(payload.error?.message || '搜索失败', false); return; }
      onResult(payload);
      onStatus(`${payload.results.length} 条`, false);
    } catch (error) {
      if (current === generation && error.name !== 'AbortError') onStatus('网络错误', false);
    }
  }
  return {run, cancel};
}
