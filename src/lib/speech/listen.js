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
/**
 * The browser's own processing, all on as Talk always had it, unless the person changed it (Settings, Talk). Automatic
 * gain turns a far voice up whenever the person goes quiet, the moment a turn should end, so with the voice gate it is
 * best off; but it also evens out a person who sits back, which is why it stays on by default. Echo cancellation is
 * what keeps a reply from hearing itself through the speakers.
 */
const microphoneFor = (settings) => ({
  channelCount: 1,
  echoCancellation: settings.echo_cancellation ?? true,
  noiseSuppression: settings.noise_suppression ?? true,
  autoGainControl: settings.auto_gain ?? true,
});

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
  const gate = createGate({ marginDb: settings.gate ? settings.gate_db : 0, startScore: settings.vad_start });
  let quiet = 0;
  let vad = null;
  let applied = null;    // what the browser did with the microphone asked for: a phone may keep its own gain
  let started = false;   // the turn has really started: the gate learns this turn's level
  let sending = false;   // Send was pressed: silence until the turn has ended
  const open = async () => {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: microphoneFor(settings) });
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
      onSpeechStarted?.();
    },
    onVADMisfire: () => {
      gate.endTurn();   // too short to be a turn: it teaches nothing
      settle();
      onSpeechFailed?.();
    },
    // `learn`, for the conversation to call once Whisper accepts the clip as speech: this turn's level joins the
    // ones the gate's bar between turns is set by. A clip that wasn't speech (a cough) teaches nothing.
    onSpeechEnd: (audio) => {
      const learn = gate.endTurn();
      settle();
      onSpeechEnded?.(audio, learn);
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
