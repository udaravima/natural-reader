import { describe, it, expect, vi, beforeEach } from 'vitest';
import { voiceSample, resetVoiceSamplesForTests } from './voiceSample';

// Each sample is a Google call, billed: hearing it again in the same session must not call Google again.
describe('voiceSample', () => {
  beforeEach(() => resetVoiceSamplesForTests());

  it('two quick taps make one request, and both get the sample', async () => {
    const fetchSample = vi.fn(async () => 'blob:puck');
    const [a, b] = await Promise.all([voiceSample('Puck', fetchSample), voiceSample('Puck', fetchSample)]);
    expect([a, b]).toEqual(['blob:puck', 'blob:puck']);
    expect(fetchSample).toHaveBeenCalledTimes(1);
    expect(await voiceSample('Puck', fetchSample)).toBe('blob:puck');
    expect(fetchSample).toHaveBeenCalledTimes(1);
  });

  it('another voice, another request', async () => {
    const fetchSample = vi.fn(async (voice) => `blob:${voice}`);
    expect(await voiceSample('Puck', fetchSample)).toBe('blob:Puck');
    expect(await voiceSample('Kore', fetchSample)).toBe('blob:Kore');
    expect(fetchSample.mock.calls.map((c) => c[0])).toEqual(['Puck', 'Kore']);
  });

  it('a sample that failed is asked for again next time', async () => {
    const fetchSample = vi.fn().mockRejectedValueOnce(new Error('quota')).mockResolvedValueOnce('blob:puck');
    await expect(voiceSample('Puck', fetchSample)).rejects.toThrow('quota');
    expect(await voiceSample('Puck', fetchSample)).toBe('blob:puck');
  });
});
