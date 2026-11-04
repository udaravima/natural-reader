/**
 * The microphone and the VAD. Silero runs in the browser, so nothing leaves it until speech starts. The library, its
 * model and its 15 MB runtime load only here, for a person who pressed Start. The files are served from this app
 * (vite.config.js), never a CDN.
 */
import { createGate } from './gate';

const ASSETS = `${import.meta.env.BASE_URL}vad/`;
const FRAME_MS = 32;   // Silero v5 takes 512 samples at 16 kHz
const QUIET_DB = -50;   // dBFS that shows as still: a quiet room is about -57
const LOUD_DB = -10;    // dBFS that shows as full: speech close to the microphone
// No automatic gain: it turns a far voice up whenever the person goes quiet, the moment a turn should end, and it
// would move the level the gate compares with (gate.js). Echo cancellation stays: the reply mustn't hear itself.
const MICROPHONE = { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: false };

/** How loud a frame is, 0 to 1 on a decibel scale, so a soft voice still shows. */
export function levelOf(frame) {
  let sum = 0;
  for (const sample of frame) sum += sample * sample;
  const db = 10 * Math.log10(sum / frame.length || 1e-12);
  return Math.min(1, Math.max(0, (db - QUIET_DB) / (LOUD_DB - QUIET_DB)));
}

export async function listen({ settings, onSpeechStarted, onSpeechFailed, onSpeechEnded, onLevel }) {
  const { MicVAD } = await import('@ricky0123/vad-web');
  // Silero's memory runs on from whatever it last heard, and that dulls it to the next short word. In a continuous
  // stream a real "stop" fired 8 times in 25; with the memory cleared after a silence, 24 (docs/DEPLOYMENT.md).
  const quietFrames = Math.floor(settings.silence_ms / FRAME_MS);
  const gate = createGate({ marginDb: settings.gate_db, startScore: settings.vad_start });
  let quiet = 0;
  let vad = null;
  let applied = null;    // what the browser did with MICROPHONE: a phone may keep its own gain
  let started = false;   // the turn has really started: the gate lets the person's softer words count, and learns
  let before = null;     // the gate's level as this turn started, to give back if it wasn't speech
  let sending = false;   // Send was pressed: silence until the turn has ended
  const open = async () => {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: MICROPHONE });
    applied = stream.getAudioTracks?.()[0]?.getSettings?.() ?? null;
    return stream;
  };
  const settle = () => {
    started = false;
    if (!sending) return;
    sending = false;
    vad?.setOptions({ redemptionMs: settings.silence_ms });
  };
  vad = await MicVAD.new({
    model: 'v5',
    baseAssetPath: ASSETS,
    onnxWASMBasePath: ASSETS,
    positiveSpeechThreshold: settings.vad_start,
    negativeSpeechThreshold: settings.vad_stay,
    minSpeechMs: settings.min_speech_ms,
    preSpeechPadMs: settings.pre_roll_ms,
    redemptionMs: settings.silence_ms,
    getStream: open,
    resumeStream: open,
    ortConfig: (ort) => {
      ort.env.logLevel = 'error';
      ort.env.wasm.numThreads = 1;   // it takes a tiny share of one core, and more needs cross-origin isolation
    },
    onFrameProcessed: ({ isSpeech }, frame) => {
      if (frame) onLevel?.(levelOf(frame));
      quiet = isSpeech < settings.vad_stay ? quiet + 1 : 0;
      if (quiet === quietFrames) vad?.model.reset_state();
    },
    onSpeechRealStart: () => {
      started = true;
      before = gate.voiceDb();
      onSpeechStarted?.();
    },
    onVADMisfire: () => {
      settle();
      onSpeechFailed?.();
    },
    onSpeechEnd: (audio) => {
      const level = before;
      settle();
      onSpeechEnded?.(audio, () => gate.lowerTo(level));
    },
  });
  // The score every frame is judged by. vad-web has no public way in: its FrameProcessor asks this function for
  // each frame (frame-processor.js), so the gate sits in front of Silero there. listen.contract.test.js fails if an
  // upgrade moves it; until then a missing hook means Talk listens as it did before the gate.
  const processor = vad.frameProcessor;
  if (typeof processor?.modelProcessFunc === 'function') {
    const silero = processor.modelProcessFunc;
    processor.modelProcessFunc = async (frame) => {
      const probs = await silero(frame);
      const isSpeech = sending ? 0 : gate.judge(frame, probs.isSpeech, started);
      return { isSpeech, notSpeech: 1 - isSpeech };
    };
  } else {
    console.warn("This version of the VAD library has no hook for the voice gate: every voice counts as the person's");
  }
  /** Send: the turn ends on the next frame, as if a full silence had passed, and is sent as usual. */
  const endTurn = () => {
    if (!started || sending) return;   // with no turn under way, nothing would end it and the microphone would stay deaf
    sending = true;
    vad.setOptions({ redemptionMs: FRAME_MS });
  };
  return Object.assign(vad, { endTurn, applied: () => applied });
}
