/**
 * The microphone and the VAD. Silero runs in the browser, so nothing leaves it until speech starts. The library, its
 * model and its 15 MB runtime load only here, for a person who pressed Start. The files are served from this app
 * (vite.config.js), never a CDN.
 */
const ASSETS = `${import.meta.env.BASE_URL}vad/`;
const FRAME_MS = 32;   // Silero v5 takes 512 samples at 16 kHz
const QUIET_DB = -50;   // dBFS that shows as still: a quiet room is about -57
const LOUD_DB = -10;    // dBFS that shows as full: speech close to the microphone

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
  let quiet = 0;
  let vad = null;
  vad = await MicVAD.new({
    model: 'v5',
    baseAssetPath: ASSETS,
    onnxWASMBasePath: ASSETS,
    positiveSpeechThreshold: settings.vad_start,
    negativeSpeechThreshold: settings.vad_stay,
    minSpeechMs: settings.min_speech_ms,
    preSpeechPadMs: settings.pre_roll_ms,
    redemptionMs: settings.silence_ms,
    ortConfig: (ort) => {
      ort.env.logLevel = 'error';
      ort.env.wasm.numThreads = 1;   // it takes a tiny share of one core, and more needs cross-origin isolation
    },
    onFrameProcessed: ({ isSpeech }, frame) => {
      if (frame) onLevel?.(levelOf(frame));
      quiet = isSpeech < settings.vad_stay ? quiet + 1 : 0;
      if (quiet === quietFrames) vad?.model.reset_state();
    },
    onSpeechRealStart: onSpeechStarted,
    onVADMisfire: onSpeechFailed,
    onSpeechEnd: onSpeechEnded,
  });
  return vad;
}
