const WORD = /[\p{L}\p{N}']+/gu;
const words = (text) => text.toLowerCase().match(WORD) ?? [];

const MIN_ECHO_WORDS = 3;   // a short word proves nothing: "stop" can be in the reply and still be meant
const ECHO_SHARE = 0.6;     // of a clip's words found in what was just said

/**
 * Is this clip a person talking? Whisper's text can't say: it wrote words for 20 of 27 noises that got past the VAD.
 * Its own scores can (docs/DEPLOYMENT.md, "The speech window"). An engine with no scores is taken at its word.
 */
export function heardSpeech({ text, no_speech: noSpeech, avg_logprob: logprob }, limits) {
  if (!text || !/[\p{L}\p{N}]/u.test(text)) return false;
  if (noSpeech != null && noSpeech >= limits.max_no_speech) return false;
  return logprob == null || logprob >= limits.min_logprob;
}

/** Is this clip the reply coming back through the microphone? `said`: the sentences spoken just now. */
export function isEcho(text, said) {
  const heard = words(text);
  if (heard.length < MIN_ECHO_WORDS) return false;
  const known = new Set(said.flatMap(words));
  return heard.filter((word) => known.has(word)).length / heard.length >= ECHO_SHARE;
}
