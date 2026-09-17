import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { chatFetch, MODELS_PATH, CHAT_PATH } from './chatTransport';

const HOSTS = {
  apiHost: '', apiPort: '',
  ollamaHost: 'localhost', ollamaPort: '11434',
};

describe('chatTransport', () => {
  beforeEach(() => { globalThis.fetch = vi.fn(); });
  afterEach(() => { vi.restoreAllMocks(); });

  it('server mode hits the gateway with credentials (cookie)', async () => {
    globalThis.fetch.mockResolvedValue(new Response('{}', { status: 200 }));
    await chatFetch('server', HOSTS, CHAT_PATH.server, { method: 'POST', body: 'x' });
    expect(globalThis.fetch).toHaveBeenCalledWith(
      '/v1/inference/chat',
      expect.objectContaining({ credentials: 'include', method: 'POST', body: 'x' }),
    );
  });

  it('local mode hits Ollama directly with NO credentials', async () => {
    globalThis.fetch.mockResolvedValue(new Response('{}', { status: 200 }));
    await chatFetch('local', HOSTS, CHAT_PATH.local, { method: 'POST', body: 'x' });
    const [url, opts] = globalThis.fetch.mock.calls[0];
    expect(url).toBe('http://localhost:11434/api/chat');
    expect(opts).not.toHaveProperty('credentials');
  });

  it('local mode with blank host is same-origin relative', async () => {
    globalThis.fetch.mockResolvedValue(new Response('{}', { status: 200 }));
    await chatFetch('local', { ...HOSTS, ollamaHost: '', ollamaPort: '' }, MODELS_PATH.local);
    expect(globalThis.fetch.mock.calls[0][0]).toBe('/api/tags');
  });

  it('exposes the gateway and Ollama paths per source', () => {
    expect(MODELS_PATH.server).toBe('/v1/inference/models');
    expect(MODELS_PATH.local).toBe('/api/tags');
    expect(CHAT_PATH.server).toBe('/v1/inference/chat');
    expect(CHAT_PATH.local).toBe('/api/chat');
  });
});
