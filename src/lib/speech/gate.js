/**
 * Only the voice at the microphone counts. Silero tells speech from noise, not near from far: a voice 35 dB quieter
 * still scores 0.97, so in an office a colleague's talk keeps a turn open for ever and pauses replies. The gate learns
 * how loud the person's own voice is and treats a voice more than `marginDb` below it as silence. To start a turn the
 * bar is higher (START_MARGIN_DB), so a nearby voice can't open one; once the turn has really started, the person's
 * softer words count. It decides only when a turn starts and stops: the audio that is transcribed is untouched.
 * The numbers come from a simulation through Silero v5 and the VAD's own rules: docs/DEPLOYMENT.md, "Talk in a busy
 * room".
 */
export const START_MARGIN_DB = 4;
const SMOOTH_FRAMES = 3;   // about 100 ms: one quiet syllable doesn't count as a gap
const LEARN_UP = 0.05;     // per 32 ms frame: a louder voice takes over within about a second
const LEARN_DOWN = 0.01;   // slower down than up, so the level sits near the top of the person's speech

const powerOf = (frame) => {
  let sum = 0;
  for (const sample of frame) sum += sample * sample;
  return sum / frame.length;
};

/**
 * `judge(frame, score, started)` returns the score the VAD should act on: Silero's, or 0 for a voice that isn't the
 * person's. `started`: the turn has really started (the VAD's speech-real-start), which loosens the bar. Only such a
 * turn teaches the level: between turns a colleague's loudest syllables come within the margin, and they pulled it
 * down 5 dB in 6 s until they could open turns (walked in Chromium, 2026-11-04).
 * `lowerTo(before)` gives back what a turn taught, when it turns out not to be speech (a cough the VAD took for
 * speech would otherwise raise the bar above the person's voice, and nothing would ever bring it down): `before` is
 * `voiceDb()` as the turn started. A `marginDb` of 0 turns the gate off.
 */
export function createGate({ marginDb, startScore }) {
  if (!marginDb) return { judge: (_frame, score) => score, voiceDb: () => null, lowerTo: () => {} };
  const startMargin = Math.min(START_MARGIN_DB, marginDb);
  const recent = [];
  let voice = null;   // dBFS: the person's voice, learned only from frames that already count as theirs
  return {
    judge(frame, score, started) {
      recent.push(powerOf(frame));
      if (recent.length > SMOOTH_FRAMES) recent.shift();
      const level = 10 * Math.log10(recent.reduce((a, b) => a + b, 0) / recent.length || 1e-12);
      const passes = voice === null || level >= voice - (started ? marginDb : startMargin);
      if (started && score >= startScore && (voice === null || level >= voice - marginDb)) {
        voice = voice === null ? level : voice + (level > voice ? LEARN_UP : LEARN_DOWN) * (level - voice);
      }
      return passes ? score : 0;
    },
    voiceDb: () => voice,
    lowerTo(before) {
      if (before === null) voice = null;
      else if (voice !== null && voice > before) voice = before;
    },
  };
}
