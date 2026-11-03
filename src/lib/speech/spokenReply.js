const AHEAD = 2;              // sentences synthesized before they're needed: enough to leave no gap
const HEARD_SHARE = 0.5;      // of a sentence played before it counts as heard

/**
 * One reply, read aloud a sentence at a time. It keeps its place through a pause, and knows how much was really
 * heard: `audio` is the clock for that, never the time since the reply began, which runs ahead of the sound.
 * Sentences are `{ text, end }` (sentences.js). `synthesize(text, signal)` resolves to a blob URL, or null.
 */
export function createSpokenReply({ synthesize, audio, revoke = URL.revokeObjectURL, onPlaying, onSentence }) {
  const queue = [];
  const controller = new AbortController();
  let current = null;
  let heard = 0;          // where the last finished sentence ends
  let paused = false;
  let closed = false;     // nothing more is coming
  let stopped = false;
  let playedAll = false;
  let startedSound = false;
  let wake = () => {};
  const sleep = () => new Promise((resolve) => { wake = resolve; });
  const stopping = new Promise((resolve) => {
    controller.signal.addEventListener('abort', () => resolve(null));
  });
  const free = (item) => item.url?.then((url) => { if (url) revoke(url); });

  const fetchAudio = (item) => {
    if (!item || item.url) return;
    item.url = Promise.resolve(synthesize(item.text, controller.signal)).catch(() => null);
  };

  const fetchAhead = () => queue.slice(0, AHEAD).forEach(fetchAudio);

  const play = (url) => new Promise((resolve) => {
    audio.onended = resolve;
    audio.onerror = resolve;
    audio.src = url;
    audio.play().then(() => {
      if (!startedSound) { startedSound = true; onPlaying?.(); }
    }, resolve);
  });

  const run = async () => {
    for (;;) {
      if (stopped) return;
      if (paused || queue.length === 0) {
        if (!paused && closed) { playedAll = true; return; }
        await sleep();
        continue;
      }
      const item = queue.shift();
      fetchAudio(item);
      fetchAhead();
      const url = await Promise.race([item.url, stopping]);   // a stuck synthesis must not hold up a stop
      while (paused && !stopped) await sleep();
      if (stopped) { free(item); return; }
      if (!url) continue;
      current = item;
      onSentence?.(item.text);
      await play(url);
      while (paused && !stopped) await sleep();
      revoke(url);
      if (stopped) return;
      heard = item.end;
      current = null;
    }
  };

  const done = run();

  return {
    done,
    add(sentence) {
      if (stopped) return;
      queue.push({ ...sentence, url: null });
      fetchAhead();
      wake();
    },
    finish() {
      closed = true;
      wake();
    },
    pause() {
      paused = true;
      audio.pause();
    },
    resume() {
      if (!paused) return;
      paused = false;
      if (current) audio.play().catch(() => {});
      wake();
    },
    stop() {
      stopped = true;
      controller.abort();
      audio.pause();
      queue.splice(0).forEach(free);
      audio.onended?.();
      wake();
    },
    heardChars() {
      const played = current && audio.duration > 0 && audio.currentTime / audio.duration > HEARD_SHARE;
      return played ? current.end : heard;
    },
    playedAll: () => playedAll,
  };
}
