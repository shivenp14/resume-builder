export const API_BASE = import.meta.env.VITE_API_BASE || 'http://127.0.0.1:8000';

function errorMessage(data, status) {
  const detail = data.detail || data.message;
  if (Array.isArray(detail)) return detail.map(issue => {
    const field = (issue.loc || []).filter(part => part !== 'body').join(' › ');
    return `${field ? `${field}: ` : ''}${issue.msg || 'Invalid value'}`;
  }).join('. ');
  return typeof detail === 'string' ? detail : `Request failed (${status})`;
}

export async function request(path, options = {}) {
  const response = await fetch(API_BASE + path, {
    ...options,
    headers: { 'Content-Type': 'application/json', ...options.headers },
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(errorMessage(data, response.status));
    error.status = response.status;
    throw error;
  }
  return data;
}
