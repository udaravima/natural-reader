/**
 * The microphone and the VAD. Silero runs in the browser, so nothing leaves it until speech starts. The library, its
 * model and its 15 MB runtime load only here, for a person who pressed Start. The files are served from this app
 * (vite.config.js), never a CDN.
 */
const ASSETS = `${import.meta.env.BASE_URL}vad/`;
const FRAME_MS = 32;   // Silero v5 takes 512 samples at 16 kHz

export async function listen({ settings, onSpeechStarted, onSpeechFailed, onSpeechEnded }) {
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
    onFrameProcessed: ({ isSpeech }) => {
      quiet = isSpeech < settings.vad_stay ? quiet + 1 : 0;
      if (quiet === quietFrames) vad?.model.reset_state();
    },
    onSpeechRealStart: onSpeechStarted,
    onVADMisfire: onSpeechFailed,
    onSpeechEnd: onSpeechEnded,
  });
  return vad;
}
