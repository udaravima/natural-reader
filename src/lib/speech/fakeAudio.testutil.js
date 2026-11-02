/** An <audio> element with the clock in the test's hands: `finish()` is a sentence ending. */
export class FakeAudio {
  src = '';

  currentTime = 0;

  duration = 2;

  onended = null;

  onerror = null;

  paused = true;

  play() { this.paused = false; return Promise.resolve(); }

  pause() { this.paused = true; }

  finish() { this.onended?.(); }
}
