import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import webSearch from './webSearch';

const ctx = { apiHost: 'localhost', apiPort: 8000 };

beforeEach(() => { global.fetch = vi.fn(); });
afterEach(() => { vi.restoreAllMocks(); });

describe('webSearch.execute validation', () => {
  it('errors and does NOT fetch when query is empty/whitespace', async () => {
    const r = await webSearch.execute({ query: '   ' }, ctx);
    expect(r.error).toMatch(/query is required/i);
    expect(global.fetch).not.toHaveBeenCalled();
  });

  it('errors and does NOT fetch when count is out of range (>10)', async () => {
    const r = await webSearch.execute({ query: 'hi', count: 11 }, ctx);
    expect(r.error).toMatch(/count must be/i);
    expect(global.fetch).not.toHaveBeenCalled();
  });

  it('fetches once and returns agent_response on success', async () => {
    global.fetch.mockResolvedValue({
      ok: true,
      json: async () => ({ query: 'hi', results: [{}, {}] }),
    });
    const r = await webSearch.execute({ query: 'hi', count: 3 }, ctx);
    expect(global.fetch).toHaveBeenCalledTimes(1);
    expect(r.agent_response.results).toHaveLength(2);
    expect(r.summary_text).toMatch(/2 result/);
  });
});

describe('webSearch.definition', () => {
  it('caps count at 10 in the JSON schema', () => {
    expect(webSearch.definition.function.parameters.properties.count.maximum).toBe(10);
  });
});
