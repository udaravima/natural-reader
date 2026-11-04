import { createSentenceSplitter } from './sentences';
import { createSpokenReply } from './spokenReply';
import { heardSpeech, isEcho } from './checks';

const SAMPLES_PER_MS = 16;   // the VAD's clips are 16 kHz

/**
 * The speech window's conversation, with nothing of React or the microphone in it (spec 2026-11-02-speech-window).
 *
 * The VAD reports speech; a reply pauses the moment it does, and keeps writing and fetching audio behind the pause, so
 * a false alarm loses nothing. Whisper then decides, on the whole clip. Real speech stops the reply and goes to the
 * model with how much of the reply was heard. Anything else, and the reply carries on from the same word.
 *
 * `send(text, { heard, onStart, onText })` resolves when the turn has settled, and `abort()` ends it early.
 */
export function createConversation({
  settings, transcribe, send, abort, synthesize, audio, revoke, onChange, onClip, now = () => performance.now(),
}) {
  let reply = null;         // { spoken, messageId, playing, sending }
  let hearing = false;
  let checking = false;
  let switching = false;    // between a reply stopping and the next one starting
  let draining = false;
  let ended = false;
  let lastPhase = null;
  let nextClip = 1;
  const waiting = [];       // clips still to be transcribed
  const accepted = [];      // { text, clip } waiting for the person to finish
  let lastSpoken = null;    // the latest reply to make a sound: { said, finishedAt }
  let cut = null;           // a reply the person stopped with a tap: { heard, sending }, for their next message

  const phase = () => {
    if (ended) return 'off';
    if (hearing) return 'hearing';
    if (checking) return 'checking';
    if (switching) return 'thinking';
    if (!reply) return 'listening';
    return reply.playing ? 'speaking' : 'thinking';
  };

  const emit = () => {
    const next = phase();
    if (next !== lastPhase) { lastPhase = next; onChange?.(next); }
  };

  // Only a clip that began while a reply was playing can be that reply. Afterwards, the same words are the person's.
  const echoable = (clip) => {
    if (!lastSpoken) return [];
    const began = clip.endedAt - clip.speechMs;
    return lastSpoken.finishedAt == null || began <= lastSpoken.finishedAt ? lastSpoken.said : [];
  };

  const verdictOf = (result, clip) => {
    if (!heardSpeech(result, settings)) return 'noise';
    return isEcho(result.text, echoable(clip)) ? 'echo' : 'speech';
  };

  // How much of a reply was heard, if it was stopped before its end: the model is to remember only that.
  const heardOf = (turn) => (turn.messageId && !turn.spoken.playedAll()
    ? { messageId: turn.messageId, chars: turn.spoken.heardChars() } : undefined);

  const startReply = (text, clip, heard) => {
    const turn = { messageId: null, playing: false, spoken: null, sending: null, said: [], finishedAt: null };
    const noteClip = () => onClip?.(clip);
    turn.spoken = createSpokenReply({
      synthesize,
      audio,
      revoke,
      onSentence: (sentence) => {
        turn.said.push(sentence);
        lastSpoken = turn;
      },
      onPlaying: () => {
        clip.firstAudioAt = now();
        turn.playing = true;
        noteClip();
        emit();
      },
    });
    const splitter = createSentenceSplitter((sentence) => {
      clip.firstSentenceAt ??= now();
      turn.spoken.add(sentence);
    }, { firstClauseChars: settings.first_clause_chars });
    reply = turn;
    emit();
    turn.sending = Promise.resolve(send(text, {
      ...(heard ? { heard } : {}),
      onStart: (id) => { turn.messageId = id; },
      onText: (delta) => {
        clip.firstTextAt ??= now();
        splitter.feed(delta);
      },
    })).then((outcome) => {
      if (outcome?.sent === false) clip.verdict = 'refused';
    }, () => {
      clip.verdict = 'failed';
    }).finally(() => {
      splitter.flush();
      turn.spoken.finish();
      noteClip();
    });
    turn.spoken.done.then(() => {
      turn.finishedAt = now();
      if (reply === turn) { reply = null; emit(); }
    });
  };

  const interruptWith = async (text, clip) => {
    const before = reply;
    const tapped = cut;
    cut = null;
    let heard = tapped?.heard;
    if (before || tapped) {
      reply = null;
      switching = true;
      emit();
      if (before) {
        heard = heardOf(before);
        before.spoken.stop();
        abort();
      }
      await (before ?? tapped).sending;   // the chat takes one message at a time
      switching = false;
    }
    if (ended) return;
    const more = accepted.splice(0).map((a) => a.text);   // said while the last reply was stopping
    startReply([text, ...more].join(' '), clip, heard);
  };

  // Everything that was waiting on the person has arrived: send what they said, or carry on.
  const settle = () => {
    if (hearing || draining || switching || waiting.length > 0 || ended) return;
    if (accepted.length > 0) {
      const text = accepted.map((a) => a.text).join(' ');
      const { clip } = accepted[0];
      accepted.length = 0;
      interruptWith(text, clip);
      return;
    }
    reply?.spoken.resume();
    emit();
  };

  const drain = async () => {
    if (draining) return;
    draining = true;
    while (waiting.length > 0 && !ended) {
      const { clip, samples, learn } = waiting.shift();
      checking = true;
      emit();
      let result = null;
      try {
        result = await transcribe(samples);
      } catch (e) {
        clip.error = e.message;
      }
      if (ended) break;
      checking = false;
      clip.transcribedAt = now();
      if (result) {
        clip.text = result.text;
        clip.scores = { noSpeech: result.no_speech, avgLogprob: result.avg_logprob };
        clip.heardIn = result.language ?? null;   // the language and model that heard it (What it heard)
        clip.heardBy = result.model ?? null;
        clip.verdict = verdictOf(result, clip);
      } else {
        clip.verdict = 'failed';
      }
      onClip?.(clip);
      if (clip.verdict === 'speech') {
        learn?.();   // the person speaking: the voice gate learns their level from this turn (gate.js)
        accepted.push({ text: result.text, clip });
      }
    }
    draining = false;
    settle();
  };

  return {
    begin: emit,
    /** Speech has lasted long enough to be worth stopping for. */
    speechStarted() {
      hearing = true;
      reply?.spoken.pause();
      emit();
    },
    /** It stopped too soon to count: the VAD throws such a clip away. */
    speechFailed() {
      hearing = false;
      settle();
      emit();
    },
    /** `learn`: from the microphone (listen.js), for a clip Whisper accepts as speech: the voice gate learns from it. */
    speechEnded(samples, learn) {
      hearing = false;
      const clip = { id: nextClip, speechMs: samples.length / SAMPLES_PER_MS, endedAt: now() };
      nextClip += 1;
      onClip?.(clip);
      waiting.push({ clip, samples, learn });
      drain();
    },
    /** The person tapped to stop the reply: it stops where it is, and listening carries on. */
    cutIn() {
      if (!reply || ended) return;
      const before = reply;
      reply = null;
      cut = { heard: heardOf(before), sending: before.sending };
      before.spoken.stop();
      abort();
      emit();
    },
    end() {
      ended = true;
      waiting.length = 0;
      accepted.length = 0;
      reply?.spoken.stop();
      reply = null;
      abort();
      emit();
    },
  };
}
