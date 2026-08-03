export async function api(path, options = {}) {
  const multipart = options.body instanceof FormData;
  const response = await fetch(`/api${path}`, {
    credentials: 'include',
    headers: multipart
      ? { ...(options.headers || {}) }
      : { 'Content-Type': 'application/json', ...(options.headers || {}) },
    ...options,
  });
  if (response.status === 401) {
    window.dispatchEvent(new Event('contracts:unauthorized'));
  }
  if (!response.ok) {
    let detail = `Erro ${response.status}`;
    try { detail = (await response.json()).detail || detail; } catch { /* empty */ }
    throw new Error(detail);
  }
  const contentType = response.headers.get('content-type') || '';
  return contentType.includes('application/json') ? response.json() : response;
}

export const get = (path) => api(path);
export const post = (path, body = {}) => api(path, { method: 'POST', body: JSON.stringify(body) });
export const patch = (path, body = {}) => api(path, { method: 'PATCH', body: JSON.stringify(body) });
export const remove = (path) => api(path, { method: 'DELETE' });
export const upload = (path, body) => api(path, { method: 'POST', body });
