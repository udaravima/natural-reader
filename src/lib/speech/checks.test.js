import { describe, it, expect } from 'vitest';
import { heardSpeech, isEcho } from './checks';

const LIMITS = { max_no_speech: 0.4, min_logprob: -1.0 };

describe('heardSpeech', () => {
  it('takes words Whisper is sure of', () => {
    expect(heardSpeech({ text: 'Stop.', no_speech: 0.05, avg_logprob: -0.5 }, LIMITS)).toBe(true);
  });

  it('refuses what Whisper thinks is not speech, however real the words look', () => {
    expect(heardSpeech({ text: 'Thank you.', no_speech: 0.5, avg_logprob: -0.3 }, LIMITS)).toBe(false);
  });

  it('refuses words Whisper is unsure of', () => {
    expect(heardSpeech({ text: 'Woof, woof!', no_speech: 0.1, avg_logprob: -1.3 }, LIMITS)).toBe(false);
  });

  it('refuses a clip with no words in it', () => {
    expect(heardSpeech({ text: ' ... ', no_speech: 0.1, avg_logprob: -0.2 }, LIMITS)).toBe(false);
    expect(heardSpeech({ text: '', no_speech: null, avg_logprob: null }, LIMITS)).toBe(false);
  });

  it('takes any words from an engine that gives no scores', () => {
    expect(heardSpeech({ text: 'Hello', no_speech: null, avg_logprob: null }, LIMITS)).toBe(true);
  });
});

describe('isEcho', () => {
  const said = ['The capital of France is Paris.'];

  it('knows its own words coming back through the microphone', () => {
    expect(isEcho('the capital of France', said)).toBe(true);
  });

  it('is not fooled by a few shared words', () => {
    expect(isEcho('what about the capital of Germany then', said)).toBe(false);
  });

  it('never drops a short word: "stop" may be in the reply and still be meant', () => {
    expect(isEcho('France', said)).toBe(false);
    expect(isEcho('Paris, stop', said)).toBe(false);
  });

  it('with nothing said there is nothing to echo', () => {
    expect(isEcho('the capital of France', [])).toBe(false);
  });
});
