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

/** The room between turns: nearly silent, and not speech. */
const quiet = (gate) => heard(gate, -60, { frames: 30, score: 0.02, started: false });

/** The room, then one turn of `frames` at `db`, ended, and accepted as speech unless `accepted` is false. */
function aTurn(gate, db, { frames = 60, score = SPEECH, accepted = true } = {}) {
  quiet(gate);
  heard(gate, db, { frames, score });
  const learn = gate.endTurn();
  if (accepted) learn();
}

/** A gate that has heard one turn of you, two seconds long, accepted. */
function knowingYou(marginDb = 9) {
  const gate = createGate({ marginDb, startScore: 0.5 });
  aTurn(gate, YOU);
  return gate;
}

describe('createGate', () => {
  it('counts every voice until it has heard one', () => {
    const gate = createGate({ marginDb: 9, startScore: 0.5 });
    expect(gate.judge(at(-45), SPEECH, false)).toBe(SPEECH);
  });

  it('once it knows your voice, one more than 9 dB quieter is silence, in a turn and between turns', () => {
    const gate = knowingYou();
    expect(heard(gate, YOU - 13, { frames: 3, started: false })).toBe(0);
    heard(gate, YOU, { frames: 30 });
    expect(heard(gate, YOU - 13, { frames: 3 })).toBe(0);
  });

  it('your own softer words, within 9 dB, still count', () => {
    const gate = knowingYou();
    heard(gate, YOU, { frames: 30 });
    expect(heard(gate, YOU - 6, { frames: 3 })).toBe(SPEECH);
  });

  // A live test, 2026-11-04: the person sat back from the microphone, and the gate held them to the level of an
  // earlier, louder turn: their turns were cut in half (docs/DEPLOYMENT.md, "Talk in a busy room").
  it('sitting back: a turn 6 dB quieter than your last one starts, and its own softer words count', () => {
    const gate = knowingYou();
    expect(heard(gate, YOU - 6, { frames: 3, started: false })).toBe(SPEECH);
    heard(gate, YOU - 6, { frames: 30 });
    expect(heard(gate, YOU - 6 - 8, { frames: 3 })).toBe(SPEECH);
  });

  it('after three quieter turns, the bar has followed you down; after one, it hasn\'t yet', () => {
    const gate = knowingYou();
    aTurn(gate, YOU - 6);
    quiet(gate);
    expect(heard(gate, YOU - 6 - 8, { frames: 3, started: false })).toBe(0);
    aTurn(gate, YOU - 6);
    aTurn(gate, YOU - 6);
    quiet(gate);
    expect(heard(gate, YOU - 6 - 8, { frames: 3, started: false })).toBe(SPEECH);
    expect(heard(gate, YOU - 6 - 13, { frames: 3, started: false })).toBe(0);
  });

  // Walked in Chromium (2026-11-04): with the bar set by the last turn alone, one quieter turn of the person's let the
  // colleague's voice in, the colleague's turns were accepted as speech, and the bar became theirs.
  it('a turn or two of the colleague\'s can\'t pull the bar down: it stays at your loudest recent turn', () => {
    const gate = knowingYou();
    aTurn(gate, YOU - 8);   // near enough to clear the margin, and Whisper calls the colleague's words speech
    aTurn(gate, YOU - 8);
    quiet(gate);
    expect(heard(gate, YOU - 13, { frames: 3, started: false })).toBe(0);
  });

  it('one loud turn sets the bar only for the next three: at your usual level they start, and then it comes back', () => {
    const gate = knowingYou();
    aTurn(gate, YOU + 8);   // you laughed, or leaned in, or coughed mid-sentence
    quiet(gate);
    expect(heard(gate, YOU, { frames: 3, started: false })).toBe(SPEECH);
    aTurn(gate, YOU);
    aTurn(gate, YOU);
    aTurn(gate, YOU);
    quiet(gate);
    expect(heard(gate, YOU - 8, { frames: 3, started: false })).toBe(SPEECH);
    expect(heard(gate, YOU - 13, { frames: 3, started: false })).toBe(0);
  });

  it('a long stretch of the quieter voice in a turn never lowers the bar', () => {
    const gate = knowingYou();
    heard(gate, YOU, { frames: 30 });
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
    heard(gate, YOU, { frames: 30 });
    heard(gate, -5, { frames: 30, score: 0.1 });
    heard(gate, -60, { frames: 3, score: 0.05 });   // the bang dies away
    expect(heard(gate, YOU - 7, { frames: 3 })).toBe(SPEECH);
  });

  // Walked in Chromium (2026-11-04): between turns the colleague's loudest syllables pulled a running level down
  // 5 dB in 6 s, until they cleared the start bar and paused a reply. Between turns nothing is learned.
  it('a voice between turns never moves the bar, however near it comes', () => {
    const gate = knowingYou();
    const before = gate.voiceDb();
    heard(gate, YOU - 6, { frames: 1000, started: false });
    expect(gate.voiceDb()).toBe(before);
  });

  it('nothing heard between turns carries into the next, not even the colleague\'s nearest syllables', () => {
    const gate = knowingYou();
    for (let i = 0; i < 20; i += 1) {   // now and then within the margin, too seldom to start a turn
      heard(gate, YOU - 7, { frames: 2, started: false });
      heard(gate, YOU - 20, { frames: 25, started: false });
    }
    expect(heard(gate, YOU - 13, { frames: 3 })).toBe(0);
  });

  it('a turn that wasn\'t speech teaches nothing: a cough the VAD took for speech leaves the bar where it was', () => {
    const gate = knowingYou();
    const before = gate.voiceDb();
    aTurn(gate, -5, { accepted: false });
    expect(gate.voiceDb()).toBe(before);
    expect(heard(gate, YOU - 7, { frames: 3, started: false })).toBe(SPEECH);
  });

  it('a first sound that wasn\'t speech is forgotten, and every voice still counts', () => {
    const gate = createGate({ marginDb: 9, startScore: 0.5 });
    aTurn(gate, -5, { accepted: false });
    expect(gate.voiceDb()).toBe(null);
    expect(heard(gate, -45, { frames: 3, started: false })).toBe(SPEECH);
  });

  it('a new turn learns afresh: the last one\'s level is only its starting point', () => {
    const gate = knowingYou();
    quiet(gate);
    heard(gate, YOU - 6, { frames: 60 });
    expect(heard(gate, YOU - 6 - 8, { frames: 3 })).toBe(SPEECH);
    expect(heard(gate, YOU - 6 - 13, { frames: 3 })).toBe(0);
  });

  it('a margin of 0 lets every voice through', () => {
    const gate = createGate({ marginDb: 0, startScore: 0.5 });
    aTurn(gate, YOU);
    expect(heard(gate, YOU - 30, { frames: 3, started: false })).toBe(SPEECH);
  });

  it('a wider margin lets a quieter voice through, to start and to stay', () => {
    const gate = knowingYou(15);
    expect(heard(gate, YOU - 13, { frames: 3, started: false })).toBe(SPEECH);
    heard(gate, YOU, { frames: 30 });
    expect(heard(gate, YOU - 13, { frames: 3 })).toBe(SPEECH);
  });
});
