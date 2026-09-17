import { apiFetch } from '../utils/apiFetch';
import { buildApiUrl } from '../utils/url';

// The two inference sources share one transport. Server mode rides the
// authenticated same-origin /v1 gateway (session cookie via apiFetch; PAT
// Bearer for the extension); local mode is byte-identical to the historical
// browser→Ollama calls — no credentials, /api/* paths, host/port honored.
export const MODELS_PATH = { server: '/v1/inference/models', local: '/api/tags' };
export const CHAT_PATH = { server: '/v1/inference/chat', local: '/api/chat' };

export function chatFetch(source, hosts, path, opts = {}) {
  if (source === 'server') {
    return apiFetch(hosts.apiHost, hosts.apiPort, path, opts);
  }
  return fetch(buildApiUrl(hosts.ollamaHost, hosts.ollamaPort, path), opts);
}

// 429 from the gateway means the daily token budget is gone. Callers must
// handle it BEFORE any 4xx retry chain — retries re-spend tokens.
export async function budgetDetail(res) {
  if (res.status !== 429) return null;
  try {
    const body = await res.json();
    const detail = body?.detail;
    return detail && typeof detail === 'object' ? detail : null;
  } catch {
    return null;
  }
}

export function formatResetAt(iso) {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '';
  return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}
