import { describe, it, expect } from 'vitest';
import { createGate } from './gate';

/** A 32 ms frame whose loudness is `db` dBFS. */
const at = (db) => new Float32Array(512).fill(10 ** (db / 20));
const SPEECH = 0.9;
const YOU = -22;

function heard(gate, db, { frames = 1, score = SPEECH, started = true } = {}) {
  let out;
  for (let i = 0; i < frames; i += 1) out = gate.judge(at(db), score, started);
  return out;
}

/** A gate that has heard you talk for a second. */
function knowingYou(marginDb = 9) {
  const gate = createGate({ marginDb, startScore: 0.5 });
  heard(gate, YOU, { frames: 30 });
  return gate;
}

describe('createGate', () => {
  it('counts every voice until it has heard one', () => {
    const gate = createGate({ marginDb: 9, startScore: 0.5 });
    expect(gate.judge(at(-45), SPEECH, false)).toBe(SPEECH);
  });

  it('once it knows your voice, one more than 9 dB quieter is silence', () => {
    const gate = knowingYou();
    expect(heard(gate, YOU - 13, { frames: 3 })).toBe(0);
  });

  it('your own softer words, within 9 dB, still count', () => {
    const gate = knowingYou();
    expect(heard(gate, YOU - 6, { frames: 3 })).toBe(SPEECH);
  });

  // The simulation (docs/DEPLOYMENT.md, "Talk in a busy room"): a strict start kept a colleague 8-11 dB quieter
  // from opening turns of her own, and delayed your cut-in by about 0.03 s.
  it('a turn starts only near your level: within 4 dB until it really starts', () => {
    const gate = knowingYou();
    expect(heard(gate, YOU - 6, { frames: 3, started: false })).toBe(0);
    expect(heard(gate, YOU - 3, { frames: 3, started: false })).toBe(SPEECH);
  });

  it('a long stretch of the quieter voice never lowers the bar', () => {
    const gate = knowingYou();
    expect(heard(gate, YOU - 13, { frames: 1000 })).toBe(0);
    expect(heard(gate, YOU - 7, { frames: 3 })).toBe(SPEECH);
  });

  it('if the quieter voice came first, yours takes over within a second', () => {
    const gate = createGate({ marginDb: 9, startScore: 0.5 });
    heard(gate, -40, { frames: 30 });
    heard(gate, YOU, { frames: 31 });
    expect(heard(gate, -40, { frames: 3 })).toBe(0);
  });

  it('a loud noise that isn\'t speech, a door or a cough, doesn\'t raise the bar', () => {
    const gate = knowingYou();
    heard(gate, -5, { frames: 30, score: 0.1 });
    heard(gate, -60, { frames: 3, score: 0.05 });   // the bang dies away
    expect(heard(gate, YOU - 7, { frames: 3 })).toBe(SPEECH);
  });

  // Walked in Chromium (2026-11-04): between turns her loudest syllables, within 9 dB, pulled the level down 5 dB
  // in 6 s, until they cleared the start bar and paused a reply. Only a turn under way teaches it now.
  it('a voice between turns never moves the bar, however near it comes', () => {
    const gate = knowingYou();
    heard(gate, YOU - 6, { frames: 1000, started: false });
    expect(heard(gate, YOU - 6, { frames: 3, started: false })).toBe(0);
    expect(gate.voiceDb()).toBeCloseTo(YOU, 1);
  });

  it('a turn that turns out not to be speech gives back what it taught', () => {
    const gate = knowingYou();
    const before = gate.voiceDb();
    heard(gate, -5, { frames: 30 });   // a loud cough the VAD took for speech
    gate.lowerTo(before);
    expect(heard(gate, YOU - 7, { frames: 3 })).toBe(SPEECH);
  });

  it('giving back never raises the bar', () => {
    const gate = knowingYou();
    gate.lowerTo(-10);
    expect(gate.voiceDb()).toBeCloseTo(YOU, 1);
  });

  it('a first sound that was noise is forgotten, and every voice counts again', () => {
    const gate = createGate({ marginDb: 9, startScore: 0.5 });
    heard(gate, -5, { frames: 30 });
    gate.lowerTo(null);
    expect(gate.voiceDb()).toBe(null);
    expect(heard(gate, -45, { frames: 3, started: false })).toBe(SPEECH);
  });

  it('a margin of 0 lets every voice through', () => {
    const gate = createGate({ marginDb: 0, startScore: 0.5 });
    heard(gate, YOU, { frames: 30 });
    expect(heard(gate, YOU - 30, { frames: 3, started: false })).toBe(SPEECH);
  });

  it('a margin below 4 dB is used for starting too', () => {
    const gate = knowingYou(2);
    expect(heard(gate, YOU - 3, { frames: 3, started: false })).toBe(0);
    expect(heard(gate, YOU - 1, { frames: 3, started: false })).toBe(SPEECH);
  });
});
