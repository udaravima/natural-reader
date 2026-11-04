/**
 * Only the voice at the microphone counts, when the person turns this on (Settings, Talk: "Ignore quieter voices").
 * Silero tells speech from noise, not near from far: a voice 35 dB quieter still scores 0.97, so in an office a
 * colleague's talk keeps a turn open for ever and pauses replies. The gate treats a voice more than `marginDb` below
 * the person's as silence. It decides only when a turn starts and stops: the audio that is transcribed is untouched.
 *
 * Which level is "the person's":
 * - in a turn, the level of THIS turn, learned as it goes. Someone who sat back from the microphone is held to how
 *   loud they are now. Holding them to a louder earlier turn cut their turns in half (a live test,
 *   2026-11-04).
 * - between turns, the loudest of their last three turns that Whisper accepted as speech. A cough alone teaches
 *   nothing, one loud turn sets the bar only for the next three, and someone who sat back is followed after three.
 *   It is never the last turn alone: one quieter turn would bring a colleague's voice within the margin, the
 *   colleague's turns are speech too, and the bar would become theirs (walked in Chromium, 2026-11-04).
 * One margin decides both starting and staying, so the setting means what it says. The numbers come from
 * simulations through Silero v5 and the VAD's own rules: docs/DEPLOYMENT.md, "Talk in a busy room".
 */
const SMOOTH_FRAMES = 3;   // about 100 ms: one quiet syllable doesn't count as a gap
const LEARN_UP = 0.05;     // per 32 ms frame: a louder voice takes over the turn within about a second
const LEARN_DOWN = 0.01;   // slower down than up, so the turn's level sits near the top of its speech
const TURNS_KEPT = 3;      // the accepted turns the bar between turns is the loudest of

const powerOf = (frame) => {
  let sum = 0;
  for (const sample of frame) sum += sample * sample;
  return sum / frame.length;
};

const OFF = { judge: (_frame, score) => score, voiceDb: () => null, endTurn: () => () => {} };

/**
 * `judge(frame, score, started)` returns the score the VAD should act on: Silero's, or 0 for a voice that isn't the
 * person's. `started`: the turn has really started (the VAD's speech-real-start); only such a turn learns. Between
 * turns nothing is learned: a colleague's loudest syllables came within the margin there, and a running level
 * they could pull down let them open turns (walked in Chromium, 2026-11-04).
 * `endTurn()` closes the turn and returns `learn`: call it if Whisper accepts the turn as speech, and the turn's level
 * joins the ones the bar between turns is set by. A `marginDb` of 0 turns the gate off.
 */
export function createGate({ marginDb, startScore }) {
  if (!marginDb) return OFF;
  const recent = [];
  const accepted = [];   // dBFS: the levels of the person's last accepted turns, oldest first
  let voice = null;      // dBFS: the loudest of them
  let turn = null;    // dBFS: this turn, learned only from frames that already count as the person's
  return {
    judge(frame, score, started) {
      recent.push(powerOf(frame));
      if (recent.length > SMOOTH_FRAMES) recent.shift();
      const level = 10 * Math.log10(recent.reduce((a, b) => a + b, 0) / recent.length || 1e-12);
      const bar = started && turn !== null ? turn : voice;
      const passes = bar === null || level >= bar - marginDb;
      if (started && passes && score >= startScore) {
        turn = turn === null ? level : turn + (level > turn ? LEARN_UP : LEARN_DOWN) * (level - turn);
      }
      return passes ? score : 0;
    },
    voiceDb: () => voice,
    endTurn() {
      const learned = turn;
      turn = null;
      return () => {
        if (learned === null) return;
        accepted.push(learned);
        if (accepted.length > TURNS_KEPT) accepted.shift();
        voice = Math.max(...accepted);
      };
    },
  };
}
