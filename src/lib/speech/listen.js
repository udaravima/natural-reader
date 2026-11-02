/**
 * The microphone and the VAD. Silero runs in the browser, so nothing leaves it until speech starts. The library, its
 * model and its 15 MB runtime load only here, for a person who pressed Start. The files are served from this app
 * (vite.config.js), never a CDN.
 */
const ASSETS = `${import.meta.env.BASE_URL}vad/`;

export async function listen({ settings, onSpeechStarted, onSpeechFailed, onSpeechEnded }) {
  const { MicVAD } = await import('@ricky0123/vad-web');
  return MicVAD.new({
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
    onSpeechRealStart: onSpeechStarted,
    onVADMisfire: onSpeechFailed,
    onSpeechEnd: onSpeechEnded,
  });
}
