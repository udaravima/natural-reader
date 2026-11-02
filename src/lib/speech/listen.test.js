import { describe, it, expect, vi, beforeEach } from 'vitest';
import { listen } from './listen';

const made = vi.hoisted(() => ({ options: null, vad: null }));
vi.mock('@ricky0123/vad-web', () => ({
  MicVAD: {
    new: vi.fn(async (options) => {
      made.options = options;
      made.vad = { model: { reset_state: vi.fn() } };
      return made.vad;
    }),
  },
}));

const SETTINGS = { vad_start: 0.5, vad_stay: 0.35, min_speech_ms: 250, pre_roll_ms: 300, silence_ms: 700 };
const frames = (score, count) => {
  for (let i = 0; i < count; i += 1) made.options.onFrameProcessed({ isSpeech: score });
};

beforeEach(async () => { await listen({ settings: SETTINGS }); });

describe('listen', () => {
  it('listens with the server\'s settings, from files served by this app', () => {
    expect(made.options).toMatchObject({
      model: 'v5', positiveSpeechThreshold: 0.5, negativeSpeechThreshold: 0.35, minSpeechMs: 250,
      preSpeechPadMs: 300, redemptionMs: 700, baseAssetPath: '/vad/', onnxWASMBasePath: '/vad/',
    });
  });

  // Silero's memory dulls it to the next short word: a real "stop" fired 8 times in 25 without this, and 24 with it.
  it('clears the model\'s memory once a silence has lasted as long as the one that ends a turn', () => {
    frames(0.9, 10);
    frames(0.1, 20);
    expect(made.vad.model.reset_state).not.toHaveBeenCalled();
    frames(0.1, 1);
    expect(made.vad.model.reset_state).toHaveBeenCalledTimes(1);
  });

  it('clears it once per silence, however long', () => {
    frames(0.1, 200);
    expect(made.vad.model.reset_state).toHaveBeenCalledTimes(1);
  });

  it('speech starts the count again', () => {
    frames(0.1, 20);
    frames(0.9, 1);
    frames(0.1, 20);
    expect(made.vad.model.reset_state).not.toHaveBeenCalled();
    frames(0.1, 1);
    frames(0.9, 1);
    frames(0.1, 21);
    expect(made.vad.model.reset_state).toHaveBeenCalledTimes(2);
  });

  it('a score between the two thresholds isn\'t silence', () => {
    frames(0.4, 50);
    expect(made.vad.model.reset_state).not.toHaveBeenCalled();
  });
});
