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
