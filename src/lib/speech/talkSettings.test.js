import { describe, it, expect } from 'vitest';
import { TALK_CONTROLS, talkDefaults, talkSettings, shownValue } from './talkSettings';

// What /v1/speech/status says, as server/speech/window.py builds it.
const WINDOW = {
  vad_start: 0.5, vad_stay: 0.35, min_speech_ms: 250, pre_roll_ms: 300, silence_ms: 700, first_clause_chars: 30,
  max_no_speech: 0.25, min_logprob: -1.0, gate: false, gate_db: 9, language: 'en',
};

describe('talkDefaults', () => {
  it('are the server\'s, and the browser\'s own processing for the microphone', () => {
    expect(talkDefaults(WINDOW)).toEqual({
      auto_gain: true, noise_suppression: true, echo_cancellation: true, gate: false, gate_db: 9,
      silence_ms: 700, min_speech_ms: 250, vad_start: 0.5,
    });
  });

  it('follow what an admin set', () => {
    expect(talkDefaults({ ...WINDOW, gate: true, silence_ms: 900 })).toMatchObject({ gate: true, silence_ms: 900 });
  });

  it('an older server without a setting gets the measured value', () => {
    const { gate: _, gate_db: __, ...older } = WINDOW;
    expect(talkDefaults(older)).toMatchObject({ gate: false, gate_db: 9 });
    expect(talkDefaults(null).silence_ms).toBe(700);
  });
});

describe('talkSettings: what Talk listens with', () => {
  it('with no choices, exactly the server\'s window plus the microphone defaults', () => {
    expect(talkSettings(WINDOW, {})).toEqual({
      ...WINDOW, auto_gain: true, noise_suppression: true, echo_cancellation: true,
    });
  });

  it('a person\'s choice wins over the default', () => {
    const got = talkSettings(WINDOW, { gate: true, gate_db: 6, auto_gain: false, silence_ms: 1200 });
    expect(got).toMatchObject({ gate: true, gate_db: 6, auto_gain: false, silence_ms: 1200 });
  });

  it('a choice that isn\'t usable is left out, and the default applies', () => {
    const got = talkSettings(WINDOW, { gate_db: 2, silence_ms: 'long', gate: 'yes', vad_start: 7, pre_roll_ms: 900 });
    expect(got).toMatchObject({ gate_db: 9, silence_ms: 700, gate: false, vad_start: 0.5, pre_roll_ms: 300 });
  });

  it('nothing stored at all is the same as no choices', () => {
    expect(talkSettings(WINDOW, undefined)).toEqual(talkSettings(WINDOW, {}));
    expect(talkSettings(WINDOW, 'garbage')).toEqual(talkSettings(WINDOW, {}));
  });

  // The VAD refuses a "stay" score above the "start" one (server/speech/window.py does the same).
  it('a sensitivity below the server\'s stay score brings the stay score down with it', () => {
    expect(talkSettings(WINDOW, { vad_start: 0.3 })).toMatchObject({ vad_start: 0.3, vad_stay: 0.3 });
    expect(talkSettings(WINDOW, { vad_start: 0.7 })).toMatchObject({ vad_start: 0.7, vad_stay: 0.35 });
  });
});

describe('the controls', () => {
  it('each has a label, and its range holds the server\'s own default', () => {
    for (const control of TALK_CONTROLS) {
      expect(control.label).toBeTruthy();
      if (control.kind === 'range') {
        expect(WINDOW[control.key]).toBeGreaterThanOrEqual(control.min);
        expect(WINDOW[control.key]).toBeLessThanOrEqual(control.max);
      }
    }
  });

  it('show their values the way a person reads them', () => {
    const control = (key) => TALK_CONTROLS.find((c) => c.key === key);
    expect(shownValue(control('gate'), true)).toBe('on');
    expect(shownValue(control('auto_gain'), false)).toBe('off');
    expect(shownValue(control('silence_ms'), 700)).toBe('700 ms');
    expect(shownValue(control('gate_db'), 9)).toBe('9 dB');
    expect(shownValue(control('vad_start'), 0.5)).toBe('0.50');
  });
});
