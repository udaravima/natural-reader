import { describe, it, expect, vi, beforeEach } from 'vitest';
import { listen, levelOf } from './listen';

const made = vi.hoisted(() => ({ options: null, vad: null, hook: true, silero: 0.9 }));
vi.mock('@ricky0123/vad-web', () => ({
  MicVAD: {
    new: vi.fn(async (options) => {
      made.options = options;
      made.vad = { model: { reset_state: vi.fn() } };
      if (made.hook) {
        // vad-web 0.0.31: MicVAD keeps its FrameProcessor here, which asks this function for every frame's score
        made.vad.frameProcessor = { modelProcessFunc: vi.fn(async () => ({ isSpeech: made.silero, notSpeech: 0.1 })) };
      }
      made.vad.setOptions = vi.fn();
      return made.vad;
    }),
  },
}));

const SETTINGS = {
  vad_start: 0.5, vad_stay: 0.35, min_speech_ms: 250, pre_roll_ms: 300, silence_ms: 700, gate: false, gate_db: 9,
};
const GATED = { ...SETTINGS, gate: true };
const frames = (score, count) => {
  for (let i = 0; i < count; i += 1) made.options.onFrameProcessed({ isSpeech: score });
};
/** A 32 ms frame `db` dBFS loud, through the score function the VAD now calls. */
const at = (db) => new Float32Array(512).fill(10 ** (db / 20));
const scored = async (db, count = 3) => {
  let probs;
  for (let i = 0; i < count; i += 1) probs = await made.vad.frameProcessor.modelProcessFunc(at(db));
  return probs.isSpeech;
};

const onLevel = vi.fn();
const onSpeechEnded = vi.fn();
let mic;
beforeEach(async () => {
  made.hook = true;
  made.silero = 0.9;
  onLevel.mockClear();
  onSpeechEnded.mockClear();
  mic = await listen({ settings: SETTINGS, onLevel, onSpeechEnded });
});

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

  it('reports how loud each frame is, for the circle to move with', () => {
    made.options.onFrameProcessed({ isSpeech: 0.9 }, new Float32Array(512).fill(0.5));
    made.options.onFrameProcessed({ isSpeech: 0.1 }, new Float32Array(512));
    expect(onLevel.mock.calls).toEqual([[1], [0]]);
  });
});

describe('listen, in a room with other voices', () => {
  const getUserMedia = vi.fn(async () => ({
    getAudioTracks: () => [{ getSettings: () => ({ autoGainControl: false, echoCancellation: true }) }],
  }));
  beforeEach(() => {
    getUserMedia.mockClear();
    Object.defineProperty(navigator, 'mediaDevices', { value: { getUserMedia }, configurable: true });
  });

  it('asks for the microphone with the browser\'s own processing, as Talk always has, unless the person changed it', async () => {
    await made.options.getStream();
    await made.options.resumeStream();
    for (const [constraints] of getUserMedia.mock.calls) {
      expect(constraints.audio).toEqual({
        channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true,
      });
    }
    expect(getUserMedia).toHaveBeenCalledTimes(2);
  });

  // Automatic gain turns a far voice up whenever the person goes quiet: the moment a turn should end.
  it('asks for what the person chose in Settings, Talk', async () => {
    await listen({ settings: { ...SETTINGS, auto_gain: false, noise_suppression: false, echo_cancellation: false } });
    await made.options.getStream();
    expect(getUserMedia.mock.calls.at(-1)[0].audio).toEqual({
      channelCount: 1, echoCancellation: false, noiseSuppression: false, autoGainControl: false,
    });
  });

  it('says what the browser actually applied, which a phone may not', async () => {
    expect(mic.applied()).toBe(null);
    await made.options.getStream();
    expect(mic.applied()).toEqual({ autoGainControl: false, echoCancellation: true });
  });

  /** A turn of the person's at `db`, ended; Whisper accepts it as speech unless `accepted` is false. */
  const aTurn = async (db = -22, { accepted = true } = {}) => {
    made.options.onSpeechRealStart();
    await scored(db, 30);
    made.options.onSpeechEnd(new Float32Array(1));
    const [, learn] = onSpeechEnded.mock.calls.at(-1);
    if (accepted) learn();
  };

  it('the gate is off unless it is switched on: every voice counts', async () => {
    await aTurn();
    expect(await scored(-45)).toBe(0.9);
  });

  it('switched on, a voice well below the person\'s last turn scores as silence', async () => {
    await listen({ settings: GATED, onSpeechEnded });
    await aTurn();
    expect(await scored(-35)).toBe(0);
  });

  it('a turn teaches the gate only once Whisper accepts it as speech', async () => {
    await listen({ settings: GATED, onSpeechEnded });
    await aTurn(-22, { accepted: false });
    expect(await scored(-45)).toBe(0.9);
    await aTurn();
    expect(await scored(-45)).toBe(0);
  });

  it('in a turn, the bar is that turn\'s own level: sitting back, the softer words still count', async () => {
    await listen({ settings: GATED, onSpeechEnded });
    await aTurn(-22);
    await scored(-60, 30);   // the room between turns
    made.options.onSpeechRealStart();
    await scored(-28, 30);
    expect(await scored(-36)).toBe(0.9);
    made.options.onSpeechEnd(new Float32Array(1));
    expect(await scored(-36)).toBe(0);   // between turns, the last accepted turn's level: -22 still
  });

  it('a server without the settings listens as before, every voice counting', async () => {
    const { gate: _, gate_db: __, ...older } = SETTINGS;
    await listen({ settings: older, onSpeechEnded });
    await aTurn();
    expect(await scored(-45)).toBe(0.9);
  });

  it('a library without the hook still listens, ungated, and says why once', async () => {
    made.hook = false;
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
    const ungated = await listen({ settings: SETTINGS });
    expect(warn).toHaveBeenCalledTimes(1);
    expect(ungated.endTurn).toBeTypeOf('function');
    warn.mockRestore();
  });
});

describe('endTurn: Send, while the person is talking', () => {
  it('ends the turn on the next frame: silence, and a silence one frame long', async () => {
    made.options.onSpeechRealStart();
    mic.endTurn();
    expect(made.vad.setOptions).toHaveBeenCalledWith({ redemptionMs: 32 });
    expect(await scored(-22)).toBe(0);
  });

  it('then listens as before, and the turn is sent as usual', async () => {
    made.options.onSpeechRealStart();
    mic.endTurn();
    const audio = new Float32Array(16);
    made.options.onSpeechEnd(audio);
    expect(made.vad.setOptions).toHaveBeenLastCalledWith({ redemptionMs: 700 });
    expect(onSpeechEnded).toHaveBeenCalledWith(audio, expect.any(Function));
    expect(await scored(-22)).toBe(0.9);
  });

  it('a turn too short to count still restores the silence', async () => {
    made.options.onSpeechRealStart();
    mic.endTurn();
    made.options.onVADMisfire();
    expect(made.vad.setOptions).toHaveBeenLastCalledWith({ redemptionMs: 700 });
  });

  it('does nothing when no turn has started: nothing would end it, and the microphone would stay deaf', async () => {
    mic.endTurn();
    expect(made.vad.setOptions).not.toHaveBeenCalled();
    expect(await scored(-22)).toBe(0.9);
  });
});

describe('levelOf', () => {
  const tone = (amplitude) => Float32Array.from({ length: 512 }, (_, i) => amplitude * Math.sin(i / 3));

  it('is 0 in silence and 1 for loud speech, near the microphone', () => {
    expect(levelOf(new Float32Array(512))).toBe(0);
    expect(levelOf(tone(0.003))).toBe(0);   // about -54 dBFS, a quiet room
    expect(levelOf(tone(0.5))).toBe(1);     // about -9 dBFS
  });

  it('grows with the loudness in between', () => {
    const soft = levelOf(tone(0.02));
    const louder = levelOf(tone(0.1));
    expect(soft).toBeGreaterThan(0);
    expect(louder).toBeGreaterThan(soft);
    expect(louder).toBeLessThan(1);
  });
});
