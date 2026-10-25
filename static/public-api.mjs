// Keep an identical idempotency key when retrying an uncertain request.
export function createPublicApi({fetcher = fetch, newKey = () => crypto.randomUUID()} = {}) {
  let ready = null, csrf = '', pending = new Map();
  async function decode(response) {
    const body = await response.json();
    if (!response.ok) {
      if (body.error?.code === 'login_required' && typeof window !== 'undefined') {
        window.location.assign('/account/login/?next=' + encodeURIComponent(window.location.pathname + window.location.search + window.location.hash));
      }
      const error = Error(body.error?.message || '请求失败');
      error.status = response.status; error.code = body.error?.code; error.confirmation = body.error?.confirmation;
      throw error;
    }
    return body;
  }
  async function prepare() {
    csrf = (await decode(await fetcher('/api/v1/csrf/', {credentials: 'same-origin'}))).csrf_token;
    await decode(await fetcher('/api/v1/identity/', {method: 'POST', credentials: 'same-origin',
      headers: {'Content-Type': 'application/json', 'X-CSRFToken': csrf}, body: '{}'}));
  }
  return async function write(url, payload) {
    if (!ready) ready = prepare().catch(error => { ready = null; throw error; });
    await ready;
    const body = JSON.stringify(payload), signature = url + '\n' + body;
    if (!pending.has(signature)) pending.set(signature, newKey());
    try {
      const result = await decode(await fetcher(url, {method: 'POST', credentials: 'same-origin',
        headers: {'Content-Type': 'application/json', 'X-CSRFToken': csrf, 'Idempotency-Key': pending.get(signature)}, body}));
      pending.delete(signature);
      return result;
    } catch (error) {
      if (error.status && error.status < 500) pending.delete(signature);
      throw error;
    }
  };
}
