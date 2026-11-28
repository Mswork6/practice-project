export class FormError extends Error {
  constructor(message, errors) {
    super(message);
    this.errors = errors;
  }
}

export async function api(path, { params, method = 'GET', data, signal } = {}) {
  const query = new URLSearchParams();
  Object.entries(params ?? {}).forEach(([key, value]) => {
    if (value !== '' && value != null) query.set(key, value);
  });
  let response;
  try {
    response = await fetch(`/api/${path}${query.size ? `?${query}` : ''}`, {
      method, signal,
      headers: data ? { 'Content-Type': 'application/json' } : undefined,
      body: data ? JSON.stringify(data) : undefined,
    });
  } catch (error) {
    if (error.name === 'AbortError') throw error;
    throw new Error('Сервер недоступен. Проверьте подключение и повторите попытку.');
  }
  if (response.status === 204) return null;
  const payload = await response.json().catch(() => null);
  if (!response.ok || payload == null) {
    const detail = payload?.detail;
    const errors = payload?.errors ?? (Array.isArray(detail) ? detail : []);
    const message = typeof detail === 'string' ? detail :
      errors.map(({ msg }) => msg).join(' ') || 'Операция не выполнена.';
    throw new FormError(message, errors);
  }
  return payload;
}
