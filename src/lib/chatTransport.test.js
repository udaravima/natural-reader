import { describe, it, expect } from 'vitest';
import { budgetDetail, formatResetAt } from './chatTransport';

describe('budgetDetail', () => {
  it('returns the detail on a 429', async () => {
    const res = new Response(JSON.stringify({
      detail: { remaining_tokens: 0, reset_at: '2026-09-18T00:00:00Z' },
    }), { status: 429 });
    expect(await budgetDetail(res)).toEqual({ remaining_tokens: 0, reset_at: '2026-09-18T00:00:00Z' });
  });

  it('returns null on non-429', async () => {
    const res = new Response('{}', { status: 200 });
    expect(await budgetDetail(res)).toBeNull();
  });

  it('returns null on an unparsable 429 body', async () => {
    const res = new Response('nope', { status: 429 });
    expect(await budgetDetail(res)).toBeNull();
  });

  it('returns null on a 429 with a plain-string detail', async () => {
    const res = new Response(JSON.stringify({ detail: 'rate limited' }), { status: 429 });
    expect(await budgetDetail(res)).toBeNull();
  });
});

describe('formatResetAt', () => {
  it('renders a local time string', () => {
    expect(formatResetAt('2026-09-18T00:00:00Z')).toMatch(/\d{1,2}:\d{2}/);
  });

  it('returns empty string for garbage input', () => {
    expect(formatResetAt('not a date')).toBe('');
  });
});

import { MODELS_PATH } from './chatTransport';
describe('MODELS_PATH', () => {
  it('is the server model list', () => expect(MODELS_PATH).toBe('/v1/inference/models'));
});
