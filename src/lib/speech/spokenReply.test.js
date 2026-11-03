import { describe, it, expect, vi } from 'vitest';
import { createSpokenReply } from './spokenReply';
import { FakeAudio } from './fakeAudio.testutil';

const tick = () => new Promise((resolve) => { setTimeout(resolve, 0); });
const make = (over = {}) => {
  const audio = new FakeAudio();
  const synthesize = vi.fn(async (text) => `blob:${text}`);
  const revoke = vi.fn();
  const reply = createSpokenReply({ synthesize, audio, revoke, ...over });
  return { audio, synthesize, revoke, reply };
};
const A = { text: 'One.', end: 4 };
const B = { text: 'Two.', end: 9 };
const C = { text: 'Three.', end: 16 };
const D = { text: 'Four.', end: 22 };

describe('createSpokenReply', () => {
  it('plays sentences in order, one at a time', async () => {
    const { audio, reply } = make();
    reply.add(A); reply.add(B);
    await tick();
    expect(audio.src).toBe('blob:One.');
    audio.finish();
    await tick();
    expect(audio.src).toBe('blob:Two.');
  });

  it('synthesizes the next two ahead, and no further', async () => {
    const { synthesize, reply } = make();
    [A, B, C, D].forEach((s) => reply.add(s));
    await tick();
    expect(synthesize.mock.calls.map((c) => c[0])).toEqual(['One.', 'Two.', 'Three.']);
  });

  it('pause keeps the place, and resume carries on from it', async () => {
    const { audio, reply } = make();
    reply.add(A);
    await tick();
    audio.currentTime = 1.2;
    reply.pause();
    expect(audio.paused).toBe(true);
    reply.resume();
    expect(audio.paused).toBe(false);
    expect(audio.currentTime).toBe(1.2);
    expect(audio.src).toBe('blob:One.');
  });

  it('paused between sentences, the next one waits', async () => {
    const { audio, reply } = make();
    reply.add(A); reply.add(B);
    await tick();
    reply.pause();
    audio.finish();
    await tick();
    expect(audio.src).toBe('blob:One.');
    reply.resume();
    await tick();
    expect(audio.src).toBe('blob:Two.');
  });

  it('paused before anything played, the first sentence waits too', async () => {
    const { audio, reply } = make();
    reply.pause();
    reply.add(A);
    await tick();
    expect(audio.src).toBe('');
    reply.resume();
    await tick();
    expect(audio.src).toBe('blob:One.');
  });

  it('counts a sentence as heard once more than half of it has played', async () => {
    const { audio, reply } = make();
    reply.add(A); reply.add(B);
    await tick();
    expect(reply.heardChars()).toBe(0);
    audio.currentTime = 0.9;
    expect(reply.heardChars()).toBe(0);
    audio.currentTime = 1.1;
    expect(reply.heardChars()).toBe(4);
    audio.finish();
    await tick();
    audio.currentTime = 0;
    expect(reply.heardChars()).toBe(4);
  });

  it('stop silences it, plays nothing more and frees what was fetched', async () => {
    const { audio, revoke, reply } = make();
    reply.add(A); reply.add(B);
    await tick();
    reply.stop();
    await tick();
    expect(audio.paused).toBe(true);
    expect(audio.src).toBe('blob:One.');
    expect(revoke).toHaveBeenCalledWith('blob:One.');
    expect(revoke).toHaveBeenCalledWith('blob:Two.');
    await reply.done;
  });

  it('stop aborts synthesis still running', async () => {
    let seen;
    const { reply } = make({ synthesize: (_text, signal) => { seen = signal; return new Promise(() => {}); } });
    reply.add(A);
    await tick();
    reply.stop();
    expect(seen.aborted).toBe(true);
    await reply.done;
  });

  it('a sentence that could not be synthesized is skipped', async () => {
    const synthesize = vi.fn(async (text) => (text === 'One.' ? null : `blob:${text}`));
    const { audio, reply } = make({ synthesize });
    reply.add(A); reply.add(B);
    await tick();
    expect(audio.src).toBe('blob:Two.');
  });

  it('is done when everything has played and no more is coming', async () => {
    const { audio, reply } = make();
    let done = false;
    reply.done.then(() => { done = true; });
    reply.add(A);
    await tick();
    audio.finish();
    await tick();
    expect(done).toBe(false);
    reply.finish();
    await tick();
    expect(done).toBe(true);
    expect(reply.playedAll()).toBe(true);
  });

  it('finishing with nothing to say is done at once', async () => {
    const { reply } = make();
    reply.finish();
    await reply.done;
  });

  it('tells when the first sound starts, once, and each sentence as it starts', async () => {
    const onPlaying = vi.fn();
    const onSentence = vi.fn();
    const { audio, reply } = make({ onPlaying, onSentence });
    reply.add(A); reply.add(B);
    await tick();
    audio.finish();
    await tick();
    expect(onPlaying).toHaveBeenCalledTimes(1);
    expect(onSentence.mock.calls.map((c) => c[0])).toEqual(['One.', 'Two.']);
  });

  it('adding after stop does nothing', async () => {
    const { audio, synthesize, reply } = make();
    reply.stop();
    reply.add(A);
    await tick();
    expect(synthesize).not.toHaveBeenCalled();
    expect(audio.src).toBe('');
  });
});
