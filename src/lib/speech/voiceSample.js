/**
 * Hearing a Google voice before choosing it (spec 2026-11-04-speech-choices-per-person, Part 3). Each sample is a
 * call to Google, billed by use, so one is kept per voice for the session: a second tap plays it again, and two
 * quick taps share one request. A sample that failed is asked for again the next time.
 */

const SAMPLES = new Map();   // key (a voice) -> Promise of an object URL

/** The sample for `key`, fetched once with `fetchSample(key)` (which resolves to an object URL). */
export function voiceSample(key, fetchSample) {
  if (!SAMPLES.has(key)) {
    const sample = Promise.resolve().then(() => fetchSample(key));
    SAMPLES.set(key, sample);
    sample.catch(() => { if (SAMPLES.get(key) === sample) SAMPLES.delete(key); });
  }
  return SAMPLES.get(key);
}

export function resetVoiceSamplesForTests() {
  SAMPLES.clear();
}

/** One sentence each in English, Sinhala and Tamil: the route reads each in its own language. */
export const SAMPLE_TEXT = "Hello, this is how I'll read your replies. ආයුබෝවන්, මම ඔබේ පිළිතුරු කියවන්නේ මෙහෙමයි. "
  + 'வணக்கம், உங்கள் பதில்களை நான் இப்படித்தான் படிப்பேன்.';
